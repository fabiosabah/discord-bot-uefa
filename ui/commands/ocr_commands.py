# -*- coding: utf-8 -*-
import json
import logging
import re
from typing import Any

import discord
from discord.ext import commands

from core.config import IMAGE_CHANNEL_ID
from core.db.audit_repo import log_action
from core.db.lobby_repo import get_image_channel
from core.db.match_repo import (
    insert_ocr_match,
    get_match_by_league_id,
    update_league_match_hero_by_slot,
    update_league_match_heroes,
    update_league_match_player_name_by_slot,
    update_league_match_player_names,
    update_league_match_duration,
)
from core.db.ocr_repo import (
    get_pending_match_screenshots,
    get_match_screenshot,
    set_match_screenshot_status,
    delete_match_screenshot,
    delete_match_screenshots,
    is_match_screenshot_enqueued,
    enqueue_match_screenshot,
)
from core.db.player_repo import add_player_alias, resolve_player_names_exact, get_player, upsert_player
from core.dota_heroes import resolve_hero_name, format_hero_suggestions
from core.ocr import can_process_ocr, process_match_screenshot, _normalize_team
from ui.commands.score_helpers import (
    is_admin,
    _format_ocr_player_line,
    _get_winner_team,
    _find_ocr_job_player_entry,
    _set_ocr_job_metadata,
    _get_ocr_player_name,
    build_ocr_job_summary_text,
    parse_player_mapping,
    _resolve_command_user_mentions,
)

audit_logger = logging.getLogger("Audit")


def _sanitize_code_block(text: str) -> str:
    return text.replace('```', '`​``')


def _split_chunks(text: str, max_size: int = 1900) -> list[str]:
    chunks: list[str] = []
    while text:
        chunk = text[:max_size]
        if len(text) > max_size:
            last_newline = chunk.rfind("\n")
            if last_newline > max_size // 2:
                chunk = chunk[:last_newline]
        chunks.append(chunk)
        text = text[len(chunk):]
    return chunks


def setup_ocr_commands(bot: commands.Bot):
    logger = logging.getLogger("OcrCommands")
    logger.info("Carregando comandos OCR...")

    @bot.command(name="pendenciaimagem", aliases=["pendingimages", "pendenciasimagem"])
    async def cmd_pending_images(ctx: commands.Context, limit: int = 10):
        if not is_admin(ctx.author.id):
            await ctx.message.delete()
            await ctx.send("❌ Apenas administradores.", delete_after=5)
            return

        jobs = get_pending_match_screenshots(limit)
        if not jobs:
            await ctx.send("📋 Nenhuma imagem pendente para processamento.")
            return

        lines = []
        for job in jobs:
            lines.append(
                f"`{job['id']:03d}` {job['image_url']} — enviado por <@{job['author_id']}> em {job['created_at']}"
            )

        embed = discord.Embed(
            title="📷 Imagens de partida pendentes",
            description="\n".join(lines),
            color=discord.Color.orange()
        )
        embed.set_footer(text=f"Mostrando {len(lines)} imagens")
        await ctx.send(embed=embed)

    @bot.command(name="reenfileirarimagens", aliases=["scanimages", "scanhistory", "reenfileira"])
    async def cmd_rescan_image_history(ctx: commands.Context, channel: str = None, limit: int = 500):
        if not is_admin(ctx.author.id):
            await ctx.message.delete()
            await ctx.send("❌ Apenas administradores.", delete_after=5)
            return

        target_channel = None
        if channel:
            if channel.isdigit():
                limit = int(channel)
            else:
                try:
                    target_channel = await commands.TextChannelConverter().convert(ctx, channel)
                except commands.ChannelNotFound:
                    await ctx.send(
                        "❌ Canal não encontrado. Use uma menção de canal, nome de canal ou defina `IMAGE_CHANNEL_ID`.",
                        delete_after=10
                    )
                    return

        if target_channel is None:
            image_channel_id = get_image_channel(ctx.guild.id) if ctx.guild else None
            if image_channel_id:
                target_channel = bot.get_channel(image_channel_id)

        if target_channel is None and IMAGE_CHANNEL_ID:
            target_channel = bot.get_channel(IMAGE_CHANNEL_ID)

        if target_channel is None:
            await ctx.send("❌ Canal de imagens não foi encontrado. Registre um canal de imagem ou passe um canal mencionando ele.", delete_after=10)
            return

        if limit < 1 or limit > 2000:
            await ctx.send("❌ O limite deve ser entre 1 e 2000 mensagens.", delete_after=10)
            return

        status_message = await ctx.send(
            f"⏳ Iniciando varredura de até {limit} mensagens em {target_channel.mention}..."
        )

        queued = 0
        skipped = 0
        ignored = 0
        async for message in target_channel.history(limit=limit, oldest_first=False):
            if not message.attachments:
                ignored += 1
                continue

            image_found = False
            for attachment in message.attachments:
                content_type = attachment.content_type or ""
                if not (content_type.startswith("image") or attachment.filename.lower().endswith((".png", ".jpg", ".jpeg", ".webp", ".gif"))):
                    continue

                image_found = True
                if is_match_screenshot_enqueued(message.id):
                    skipped += 1
                    break

                enqueue_match_screenshot(
                    message.id,
                    message.guild.id if message.guild else 0,
                    target_channel.id,
                    message.author.id,
                    attachment.url,
                    message.created_at.isoformat()
                )
                queued += 1

            if not image_found:
                ignored += 1

        await status_message.edit(content=(
            f"✅ Varredura concluída: {queued} imagem(ns) enfileiradas, {skipped} já existentes, {ignored} mensagens sem imagem ignoradas do histórico de {limit} mensagens."
        ))

    @bot.command(name="detalhesimagem", aliases=["imagedetails", "imagemdetalhes"])
    async def cmd_image_details(ctx: commands.Context, job_id: int):
        if not is_admin(ctx.author.id):
            await ctx.message.delete()
            await ctx.send("❌ Apenas administradores.", delete_after=5)
            return

        job = get_match_screenshot(job_id)
        if not job:
            await ctx.send(f"❌ Job de imagem {job_id} não encontrado.", delete_after=10)
            return

        if not job["metadata"]:
            await ctx.send(f"❌ Job {job_id} não possui metadados OCR.", delete_after=10)
            return

        try:
            metadata = json.loads(job["metadata"])
        except json.JSONDecodeError:
            await ctx.send(f"❌ Metadados OCR inválidos para o job {job_id}.", delete_after=10)
            return

        def strip_debug_fields(value: Any) -> Any:
            if isinstance(value, dict):
                return {
                    k: strip_debug_fields(v)
                    for k, v in value.items()
                    if k not in {"raw_text", "metadata_payload", "kda"}
                }
            if isinstance(value, list):
                return [strip_debug_fields(item) for item in value]
            return value

        metadata = strip_debug_fields(metadata)
        metadata_text = json.dumps(metadata, indent=2, ensure_ascii=False)
        sanitized_text = _sanitize_code_block(metadata_text)
        chunks = _split_chunks(sanitized_text)

        await ctx.send(f"🔎 Detalhes do job {job_id} (JSON mapeado):")
        for chunk in chunks:
            await ctx.send(f"```json\n{chunk}\n```")

    @bot.command(name="rawtextimagem", aliases=["rawtextimage", "rawimagem", "rawtext"])
    async def cmd_raw_text_image(ctx: commands.Context, job_id: int):
        if not is_admin(ctx.author.id):
            await ctx.message.delete()
            await ctx.send("❌ Apenas administradores.", delete_after=5)
            return

        job = get_match_screenshot(job_id)
        if not job:
            await ctx.send(f"❌ Job de imagem {job_id} não encontrado.", delete_after=10)
            return

        metadata = None
        if job["metadata"]:
            try:
                metadata = json.loads(job["metadata"])
            except json.JSONDecodeError:
                metadata = None

        raw_text = None
        if metadata:
            raw_text = metadata.get("raw_text") or metadata.get("metadata_payload", {}).get("raw_text")

        if not raw_text:
            await ctx.send("❌ Texto OCR bruto não encontrado para este job.", delete_after=10)
            return

        sanitized_text = _sanitize_code_block(raw_text)
        chunks = _split_chunks(sanitized_text)

        if len(chunks) == 1:
            await ctx.send(f"```\n{chunks[0]}\n```")
            return

        await ctx.send(f"✅ Texto OCR bruto para o job {job_id} é longo e será exibido em {len(chunks)} partes.")
        for index, chunk in enumerate(chunks, start=1):
            await ctx.send(f"```\n{chunk}\n```\n({index}/{len(chunks)})")

    @bot.command(name="metadadosimagem", aliases=["imagemjson", "imagejson", "jsonimagem"])
    async def cmd_image_metadata(ctx: commands.Context, job_id: int):
        if not is_admin(ctx.author.id):
            await ctx.message.delete()
            await ctx.send("❌ Apenas administradores.", delete_after=5)
            return

        job = get_match_screenshot(job_id)
        if not job:
            await ctx.send(f"❌ Job de imagem {job_id} não encontrado.", delete_after=10)
            return

        if not job["metadata"]:
            await ctx.send(f"❌ Job {job_id} não possui metadados OCR.", delete_after=10)
            return

        try:
            metadata = json.loads(job["metadata"])
        except json.JSONDecodeError:
            await ctx.send(f"❌ Metadados OCR inválidos para o job {job_id}.", delete_after=10)
            return

        metadata_text = json.dumps(metadata, ensure_ascii=False, indent=2)
        sanitized_text = _sanitize_code_block(metadata_text)
        chunks = _split_chunks(sanitized_text)

        for chunk in chunks:
            await ctx.send(f"```json\n{chunk}\n```")

    @bot.command(name="imagemresumo", aliases=["resumoimagem", "matchsummary", "jobsummary"])
    async def cmd_image_summary(ctx: commands.Context, job_id: int):
        if not is_admin(ctx.author.id):
            await ctx.message.delete()
            await ctx.send("❌ Apenas administradores.", delete_after=5)
            return

        job = get_match_screenshot(job_id)
        if not job:
            await ctx.send(f"❌ Job de imagem {job_id} não encontrado.", delete_after=10)
            return

        if not job["metadata"]:
            await ctx.send(f"❌ Job {job_id} não possui metadados OCR.", delete_after=10)
            return

        try:
            metadata = json.loads(job["metadata"])
        except json.JSONDecodeError:
            await ctx.send(f"❌ Metadados OCR inválidos para o job {job_id}.", delete_after=10)
            return

        summary = build_ocr_job_summary_text(job_id, metadata)
        await ctx.send(summary)

    @bot.command(name="setjobwinner", aliases=["definirvencedorjob", "setwinnerjob"])
    async def cmd_set_job_winner(ctx: commands.Context, job_id: int, team: str):
        if not is_admin(ctx.author.id):
            await ctx.message.delete()
            await ctx.send("❌ Apenas administradores.", delete_after=5)
            return

        job = get_match_screenshot(job_id)
        if not job:
            await ctx.send(f"❌ Job de imagem {job_id} não encontrado.", delete_after=10)
            return

        if not job["metadata"]:
            await ctx.send(f"❌ Job {job_id} não possui metadados OCR.", delete_after=10)
            return

        try:
            parsed = json.loads(job["metadata"])
        except json.JSONDecodeError:
            await ctx.send(f"❌ Metadados OCR inválidos para o job {job_id}.", delete_after=10)
            return

        normalized = _normalize_team(team)
        if normalized not in {"radiant", "dire"}:
            await ctx.send("❌ Time inválido. Use `radiant` ou `dire`.", delete_after=15)
            return

        match_info = parsed.get("match_info") or parsed.get("game_details")
        if not isinstance(match_info, dict):
            match_info = {}
            parsed["match_info"] = match_info

        match_info["winner_team"] = normalized
        match_info["winner"] = normalized.title()
        parsed["winner_team"] = normalized
        parsed["winner"] = normalized.title()
        parsed["radiant_win"] = normalized == "radiant"

        set_match_screenshot_status(
            job_id,
            job["status"] or "processed",
            metadata=json.dumps(parsed, ensure_ascii=False)
        )

        await ctx.message.delete()
        await ctx.send(
            f"✅ Vencedor do job {job_id} definido como **{normalized.title()}**. "
            f"Use `!importarimagem {job_id} <mapeamento>` para importar a partida."
        )

    @bot.command(name="removerimagem", aliases=["deleteimage", "deleteimagem", "removeimage"])
    async def cmd_remove_image(ctx: commands.Context, job_id: int, confirm: str = None):
        if not is_admin(ctx.author.id):
            await ctx.message.delete()
            await ctx.send("❌ Apenas administradores.", delete_after=5)
            return

        if confirm != "confirmar":
            await ctx.send(
                "⚠️ Para remover o job de OCR, use `!removerimagem <job_id> confirmar`.",
                delete_after=15
            )
            return

        if not get_match_screenshot(job_id):
            await ctx.send(f"❌ Job de imagem {job_id} não encontrado.", delete_after=10)
            return

        delete_match_screenshot(job_id)
        await ctx.message.delete()
        await ctx.send(f"🗑️ Job de imagem {job_id} removido com sucesso.")

    @bot.command(name="ocrhero", aliases=["editocrhero"])
    async def cmd_ocr_hero(ctx: commands.Context, job_id: int, slot: int, *, hero: str):
        if not is_admin(ctx.author.id):
            await ctx.message.delete()
            await ctx.send("❌ Apenas administradores.", delete_after=5)
            return

        job = get_match_screenshot(job_id)
        if not job:
            await ctx.send(f"❌ Job de imagem {job_id} não encontrado.", delete_after=10)
            return

        if not job["metadata"]:
            await ctx.send(f"❌ Job {job_id} não possui metadados OCR processados.", delete_after=10)
            return

        try:
            parsed = json.loads(job["metadata"])
        except json.JSONDecodeError:
            await ctx.send(f"❌ Metadados OCR inválidos para o job {job_id}.", delete_after=10)
            return

        entry, _ = _find_ocr_job_player_entry(parsed, slot)
        if entry is None:
            await ctx.send(f"❌ Slot {slot} não encontrado no job {job_id}.", delete_after=10)
            return

        resolved_hero, suggestions, status = resolve_hero_name(hero)
        if status == "empty":
            await ctx.send("❌ Informe o nome do herói após o slot.", delete_after=10)
            return
        if status == "ambiguous":
            await ctx.send(
                f"❌ Nome ambíguo: '{hero}'. Tente digitar um pouco mais ou escolha um destes: {format_hero_suggestions(suggestions)}",
                delete_after=30
            )
            return
        if resolved_hero is None:
            await ctx.send(
                f"❌ Não foi possível encontrar um herói parecido com '{hero}'. Sugestões: {format_hero_suggestions(suggestions)}",
                delete_after=30
            )
            return

        old_hero = entry.get("hero_name") or entry.get("hero") or entry.get("heroi") or "desconhecido"
        if "hero_name" in entry:
            entry["hero_name"] = resolved_hero
        elif "hero" in entry:
            entry["hero"] = resolved_hero
        elif "heroi" in entry:
            entry["heroi"] = resolved_hero
        else:
            entry["hero_name"] = resolved_hero

        try:
            _set_ocr_job_metadata(job_id, parsed)
        except ValueError as exc:
            await ctx.send(f"❌ Erro ao atualizar o job {job_id}: {exc}", delete_after=20)
            return

        await ctx.message.delete()
        await ctx.send(f"✅ Herói `{old_hero}` alterado para `{resolved_hero}` no job {job_id}.")

    @bot.command(name="ocrnick", aliases=["editocrnick"])
    async def cmd_ocr_nick(ctx: commands.Context, job_id: int, slot: int, *, new_nick: str):
        if not is_admin(ctx.author.id):
            await ctx.message.delete()
            await ctx.send("❌ Apenas administradores.", delete_after=5)
            return

        job = get_match_screenshot(job_id)
        if not job:
            await ctx.send(f"❌ Job de imagem {job_id} não encontrado.", delete_after=10)
            return

        if not job["metadata"]:
            await ctx.send(f"❌ Job {job_id} não possui metadados OCR processados.", delete_after=10)
            return

        try:
            parsed = json.loads(job["metadata"])
        except json.JSONDecodeError:
            await ctx.send(f"❌ Metadados OCR inválidos para o job {job_id}.", delete_after=10)
            return

        entry, _ = _find_ocr_job_player_entry(parsed, slot)
        if entry is None:
            await ctx.send(f"❌ Slot {slot} não encontrado no job {job_id}.", delete_after=10)
            return

        old_nick = entry.get("player_name") or entry.get("name") or entry.get("player") or "desconhecido"
        if "player_name" in entry:
            entry["player_name"] = new_nick
        elif "name" in entry:
            entry["name"] = new_nick
        elif "player" in entry:
            entry["player"] = new_nick
        else:
            entry["player_name"] = new_nick

        try:
            _set_ocr_job_metadata(job_id, parsed)
        except ValueError as exc:
            await ctx.send(f"❌ Erro ao atualizar o job {job_id}: {exc}", delete_after=20)
            return

        await ctx.message.delete()
        await ctx.send(f"✅ Nick `{old_nick}` alterado para `{new_nick}` no job {job_id}.")

    @bot.command(name="ocruser", aliases=["editocruser"])
    async def cmd_ocr_user(ctx: commands.Context, job_id: int, *args: str):
        if not is_admin(ctx.author.id):
            await ctx.message.delete()
            await ctx.send("❌ Apenas administradores.", delete_after=5)
            return

        if not args:
            await ctx.send(
                "❌ Uso incorreto. Exemplo: `!ocruser 1 @Usuario1 @Usuario2 ...` para mapear todos os slots, ou `!ocruser 1 2 @Usuario` para mapear apenas o slot 2.",
                delete_after=20
            )
            return

        job = get_match_screenshot(job_id)
        if not job:
            await ctx.send(f"❌ Job de imagem {job_id} não encontrado.", delete_after=10)
            return

        if not job["metadata"]:
            await ctx.send(f"❌ Job {job_id} não possui metadados OCR processados.", delete_after=10)
            return

        try:
            parsed = json.loads(job["metadata"])
        except json.JSONDecodeError:
            await ctx.send(f"❌ Metadados OCR inválidos para o job {job_id}.", delete_after=10)
            return

        players = parsed.get("players_data") or parsed.get("players") or []
        if not isinstance(players, list) or not players:
            await ctx.send(f"❌ Não foi possível obter a lista de jogadores do job {job_id}.", delete_after=10)
            return

        first_arg = args[0]
        slot: int | None = None
        user_tokens: tuple[str, ...]

        if first_arg.isdigit():
            if len(args) < 2:
                await ctx.send(
                    "❌ Uso incorreto. Quando informar slot, também passe um usuário: `!ocruser 1 2 @Usuario`.",
                    delete_after=20
                )
                return

            slot = int(first_arg)
            user_tokens = args[1:]
            if len(user_tokens) != 1:
                await ctx.send(
                    "❌ Quando informar slot, passe exatamente um usuário: `!ocruser 1 2 @Usuario`.",
                    delete_after=20
                )
                return

            if slot < 1 or slot > len(players):
                await ctx.send(
                    f"❌ Slot inválido. O job {job_id} tem {len(players)} slots.",
                    delete_after=20
                )
                return
        else:
            user_tokens = args
            if len(user_tokens) > len(players):
                await ctx.send(
                    f"❌ Você forneceu mais usuários do que slots existem no job ({len(players)}).",
                    delete_after=20
                )
                return

        members, error = await _resolve_command_user_mentions(ctx, user_tokens)
        if error:
            await ctx.send(error, delete_after=20)
            return

        aliases_added = 0
        if slot is not None:
            entry, _ = _find_ocr_job_player_entry(parsed, slot)
            if entry is None:
                await ctx.send(f"❌ Slot {slot} não encontrado no job {job_id}.", delete_after=10)
                return

            entry["discord_id"] = members[0].id
            player_name = _get_ocr_player_name(entry)
            if player_name:
                add_player_alias(members[0].id, player_name)
                aliases_added += 1
            mapped_count = 1
        else:
            for index, member in enumerate(members, start=1):
                entry, _ = _find_ocr_job_player_entry(parsed, index)
                if entry is None:
                    continue
                entry["discord_id"] = member.id
                player_name = _get_ocr_player_name(entry)
                if player_name:
                    add_player_alias(member.id, player_name)
                    aliases_added += 1
            mapped_count = len(members)

        try:
            _set_ocr_job_metadata(job_id, parsed)
        except ValueError as exc:
            await ctx.send(f"❌ Erro ao atualizar o job {job_id}: {exc}", delete_after=20)
            return

        audit_logger.info(
            f"[OCRUSER] job={job_id} slot={slot if slot is not None else 'all'} mapped={mapped_count} aliases_added={aliases_added}"
        )

        await ctx.message.delete()
        if slot is not None:
            await ctx.send(
                f"✅ Slot {slot} do job {job_id} mapeado para {members[0].mention}. "
                f"Apelido salvo. → `!ok {job_id} MM:SS`"
            )
        else:
            await ctx.send(
                f"✅ {mapped_count} slot(s) mapeados no job {job_id}. "
                f"Apelidos salvos. → `!ok {job_id} MM:SS`"
            )

    @bot.command(name="confirmarimagem", aliases=["confirmimage", "editarimagem", "editimage"])
    async def cmd_confirm_image(ctx: commands.Context, job_id: int, *, text: str):
        if not is_admin(ctx.author.id):
            await ctx.message.delete()
            await ctx.send("❌ Apenas administradores.", delete_after=5)
            return

        job = get_match_screenshot(job_id)
        if not job:
            await ctx.send(f"❌ Job de imagem {job_id} não encontrado.", delete_after=10)
            return

        set_match_screenshot_status(job_id, "confirmed", metadata=text)
        await ctx.message.delete()
        await ctx.send(f"✅ Imagem {job_id} confirmada. Use o texto para registrar o histórico: {text}")

    @bot.command(name="ok", aliases=["ocrok"])
    async def cmd_ok(ctx: commands.Context, job_id: int, duration: str = ""):
        if not is_admin(ctx.author.id):
            await ctx.message.delete()
            await ctx.send("❌ Apenas administradores.", delete_after=5)
            return

        job = get_match_screenshot(job_id)
        if not job:
            await ctx.send(f"❌ Job {job_id} não encontrado.", delete_after=600)
            return

        if not job["metadata"]:
            await ctx.send(f"❌ Job {job_id} ainda sem metadados OCR.", delete_after=600)
            return

        try:
            parsed = json.loads(job["metadata"])
        except json.JSONDecodeError:
            await ctx.send(f"❌ Metadados OCR inválidos no job {job_id}.", delete_after=600)
            return

        players = parsed.get("players_data") or parsed.get("players") or []
        if not isinstance(players, list) or not players:
            await ctx.send(f"❌ Sem lista de jogadores no job {job_id}.", delete_after=600)
            return

        match_info   = parsed.get("match_info") or parsed.get("game_details") or {}
        ocr_duration = match_info.get("duration") or parsed.get("duration")
        arg_duration = duration.strip()
        if not ocr_duration and not (arg_duration and re.match(r"^\d{1,2}:\d{2}:\d{2}$|^\d{1,2}:\d{2}$", arg_duration)):
            await ctx.send(
                f"⏱️ Duração não detectada no OCR. Informe ao registrar:\n`!ok {job_id} MM:SS`",
                delete_after=600
            )
            return

        player_mapping: dict[str, dict[str, object]] = {}
        unresolved_names: list[str] = []
        for player in players:
            if not isinstance(player, dict):
                continue
            player_name = _get_ocr_player_name(player)
            if not player_name:
                continue
            discord_id = player.get("discord_id")
            if discord_id is not None:
                try:
                    player_mapping[player_name] = {"discord_id": int(discord_id)}
                    continue
                except (TypeError, ValueError):
                    pass
            unresolved_names.append(player_name)

        existing_count = len(player_mapping)
        resolved = resolve_player_names_exact(unresolved_names)
        missing = [name for name in unresolved_names if name not in resolved]

        audit_logger.info(
            f"[OK] job={job_id} existing_ids={existing_count} resolved={len(resolved)} missing={missing}"
        )

        if missing:
            dur_hint = duration.strip() if duration.strip() else "MM:SS"
            lines = ["⚠️ Nicks não reconhecidos. Cadastre e tente novamente:\n"]
            for name in missing:
                lines.append(f"`!cadastro {name} @usuario`")
            lines.append(f"\nApós cadastrar:\n`!ok {job_id} {dur_hint}`")
            await ctx.message.delete()
            await ctx.send("\n".join(lines), delete_after=600)
            return

        alias_errors: list[str] = []
        for name, discord_id in resolved.items():
            player_mapping[name] = {"discord_id": discord_id}
            try:
                add_player_alias(discord_id, name)
            except ValueError as e:
                alias_errors.append(str(e))

        if alias_errors:
            await ctx.send(
                "❌ Limite de usuários por nick atingido:\n" +
                "\n".join(f"• {e}" for e in alias_errors) +
                "\nUse `!cadastro <nick> @usuario` para verificar ou remover associações.",
                delete_after=600
            )
            return

        try:
            league_match_id = insert_ocr_match(job_id, player_mapping, ctx.author.id, ctx.author.display_name)
            delete_match_screenshot(job_id)
        except Exception as exc:
            await ctx.send(f"❌ Falha ao importar job {job_id}: {exc}", delete_after=600)
            return

        duration = duration.strip()
        if duration and re.match(r"^\d{1,2}:\d{2}:\d{2}$|^\d{1,2}:\d{2}$", duration):
            update_league_match_duration(league_match_id, duration)

        await ctx.message.delete()
        await ctx.send(f"✅ Partida **#{league_match_id}** registrada com sucesso.")

    @bot.command(name="importarimagem", aliases=["importimage", "ocrimport"])
    async def cmd_import_image(ctx: commands.Context, job_id: int | None = None, *, mapping_text: str | None = None):
        if job_id is None or not mapping_text:
            await ctx.send(
                "❌ Uso incorreto. Exemplo: `!importarimagem 123 1=@123456789012345678`\n"
                "ou `!importarimagem 123 \"Nome do jogador\"=@123456789012345678 hero=Rubick`",
                delete_after=20
            )
            return

        if not is_admin(ctx.author.id):
            await ctx.message.delete()
            await ctx.send("❌ Apenas administradores.", delete_after=5)
            return

        job = get_match_screenshot(job_id)
        if not job:
            await ctx.send(f"❌ Job de imagem {job_id} não encontrado.", delete_after=10)
            return

        if not job["metadata"]:
            await ctx.send(f"❌ Job {job_id} não possui metadados OCR processados.", delete_after=10)
            return

        try:
            parsed = json.loads(job["metadata"])
        except json.JSONDecodeError:
            await ctx.send(f"❌ Metadados OCR inválidos para o job {job_id}.", delete_after=10)
            return

        players = parsed.get("players_data") or parsed.get("players") or []
        if not isinstance(players, list) or not players:
            await ctx.send(f"❌ Não foi possível obter a lista de jogadores do job {job_id}.", delete_after=10)
            return

        mappings = parse_player_mapping(mapping_text)
        if not mappings:
            await ctx.send(
                "❌ Forneça mapeamentos no formato `1=@123456789012345678`, `1=<@123456789012345678>`, `\"Nome do jogador\"=@123...`, `PlayerName=@123` ou `1=@123 hero=Rubick`.",
                delete_after=15
            )
            return

        player_mapping: dict[str, dict[str, object]] = {}
        hero_errors: list[str] = []
        for mapping in mappings:
            player_key = mapping["player_key"]
            discord_id = mapping["discord_id"]
            hero = mapping.get("hero")

            if player_key.isdigit():
                index = int(player_key) - 1
                if index < 0 or index >= len(players):
                    await ctx.send(f"❌ Índice inválido: {player_key}", delete_after=10)
                    return
                player_key = (
                    players[index].get("player_name")
                    or players[index].get("name")
                    or players[index].get("player")
                    or f"player_{player_key}"
                )

            if hero is not None:
                resolved_hero, suggestions, status = resolve_hero_name(str(hero))
                if status == "exact":
                    hero = resolved_hero
                elif status == "ambiguous":
                    hero_errors.append(
                        f"Herói ambíguo para '{hero}': {format_hero_suggestions(suggestions)}"
                    )
                else:
                    if suggestions:
                        hero_errors.append(
                            f"Herói desconhecido para '{hero}'. Sugestões: {format_hero_suggestions(suggestions)}"
                        )
                    else:
                        hero_errors.append(
                            f"Herói desconhecido para '{hero}'. Use um nome oficial de Dota 2."
                        )

            player_mapping[player_key] = {"discord_id": discord_id, "hero": hero}

        if hero_errors:
            await ctx.send(
                "❌ Alguns nomes de herói não estão na lista oficial:\n" +
                "\n".join(f"• {error}" for error in hero_errors) +
                f"\nUse `!importarimagem {job_id} <mapeamento>` com nomes oficiais, por exemplo `hero=Rubick`.",
                delete_after=30
            )
            return

        extracted_names = [
            (
                p.get("player_name")
                or p.get("name")
                or p.get("player")
                or ""
            ).strip()
            for p in players
        ]
        missing_names = [name for name in extracted_names if name and name not in player_mapping]
        if missing_names:
            auto = resolve_player_names_exact(missing_names)
            for name, discord_id in auto.items():
                if name not in player_mapping:
                    player_mapping[name] = {"discord_id": discord_id}
            missing_names = [name for name in missing_names if name not in player_mapping]

        if missing_names:
            await ctx.send(
                f"❌ Ainda faltam mapear os seguintes jogadores: {', '.join(missing_names)}\n"
                "Use `!addalias @Usuario NomeOCR` para registrar o nick quando ele aparecer pela primeira vez, "
                "ou use o slot numérico no mapeamento: `1=@123456789012345678`.",
                delete_after=30
            )
            return

        try:
            league_match_id = insert_ocr_match(job_id, player_mapping, ctx.author.id, ctx.author.display_name)
        except Exception as exc:
            await ctx.send(f"❌ Falha ao importar imagem: {exc}", delete_after=20)
            return

        await ctx.message.delete()
        await ctx.send(
            f"✅ Imagem {job_id} importada como partida `#{league_match_id}` no banco. "
            f"Use `!id {league_match_id}` para revisar e `!fixhero` / `!nick` para ajustes."
        )

    @bot.command(name="fixhero", aliases=["corrigirhero"])
    async def cmd_fix_hero(ctx: commands.Context, league_match_id: int, slot: int, *, hero: str):
        if not is_admin(ctx.author.id):
            await ctx.message.delete()
            await ctx.send("❌ Apenas administradores.", delete_after=5)
            return

        match = get_match_by_league_id(league_match_id)
        if not match:
            await ctx.send(f"❌ Partida da liga {league_match_id} não encontrada.", delete_after=10)
            return

        players = match.get("players_data") or []
        player_entry = next((p for p in players if p.get("slot") == slot), None)
        if not player_entry:
            await ctx.send(
                f"❌ Slot {slot} não foi encontrado para a partida {league_match_id}. Use o slot correto.",
                delete_after=20
            )
            return

        hero = hero.lstrip(", ")
        resolved_hero, suggestions, status = resolve_hero_name(hero)
        if status == "empty":
            await ctx.send("❌ Informe o nome do herói após o slot.", delete_after=10)
            return
        if status == "ambiguous":
            await ctx.send(
                f"❌ Nome ambíguo: '{hero}'. Tente digitar um pouco mais ou escolha um destes: {format_hero_suggestions(suggestions)}",
                delete_after=30
            )
            return
        if resolved_hero is None:
            await ctx.send(
                f"❌ Não foi possível encontrar um herói parecido com '{hero}'. Sugestões: {format_hero_suggestions(suggestions)}",
                delete_after=30
            )
            return

        updated = update_league_match_hero_by_slot(league_match_id, slot, resolved_hero)
        if not updated:
            await ctx.send(
                f"❌ Não foi possível atualizar o herói do slot {slot} na partida {league_match_id}.",
                delete_after=20
            )
            return

        log_action(
            ctx.author.id,
            ctx.author.display_name,
            "!fixhero",
            f"league_match_id={league_match_id} slot={slot} hero={resolved_hero}",
        )

        await ctx.message.delete()
        await ctx.send(
            f"✅ Herói do slot {slot} na partida {league_match_id} atualizado para **{resolved_hero}**."
        )

    @bot.command(name="ocrtime", aliases=["settime", "definirtempo"])
    async def cmd_ocr_time(ctx: commands.Context, league_match_id: int, *, duration: str):
        if not is_admin(ctx.author.id):
            await ctx.message.delete()
            await ctx.send("❌ Apenas administradores.", delete_after=5)
            return

        match = get_match_by_league_id(league_match_id)
        if not match:
            await ctx.send(f"❌ Partida `{league_match_id}` não encontrada.", delete_after=10)
            return

        duration = duration.strip()
        if not re.match(r"^\d{1,2}:\d{2}:\d{2}$|^\d{1,2}:\d{2}$", duration):
            await ctx.send(
                "❌ Formato inválido. Use `MM:SS` ou `H:MM:SS`, por exemplo: `!ocrtime 1 36:55` ou `!ocrtime 1 1:07:40`",
                delete_after=10
            )
            return

        updated = update_league_match_duration(league_match_id, duration)
        if not updated:
            await ctx.send(f"❌ Não foi possível atualizar a duração da partida `{league_match_id}`.", delete_after=10)
            return

        log_action(
            ctx.author.id,
            ctx.author.display_name,
            "!ocrtime",
            f"league_match_id={league_match_id} duration={duration}",
        )

        await ctx.message.delete()
        await ctx.send(f"✅ Duração da partida `#{league_match_id}` atualizada para **{duration}**.")

    @bot.command(name="definirherois", aliases=["setmatchheroes", "setherois"])
    async def cmd_set_match_heroes(ctx: commands.Context, league_match_id: int, *, heroes_text: str):
        if not is_admin(ctx.author.id):
            await ctx.message.delete()
            await ctx.send("❌ Apenas administradores.", delete_after=5)
            return

        match = get_match_by_league_id(league_match_id)
        if not match:
            await ctx.send(f"❌ Partida da liga {league_match_id} não encontrada.", delete_after=10)
            return

        heroes = [hero.strip() for hero in heroes_text.split(",") if hero.strip()]
        players = match.get("players_data") or []
        if len(heroes) != len(players):
            await ctx.send(
                f"❌ Esta partida possui {len(players)} jogadores. Forneça exatamente {len(players)} heróis separados por vírgula.",
                delete_after=20
            )
            return

        resolved_heroes: list[str] = []
        for hero in heroes:
            resolved_hero, suggestions, status = resolve_hero_name(hero)
            if status == "empty":
                await ctx.send("❌ Um dos heróis está vazio. Use nomes ou abreviações válidas.", delete_after=20)
                return
            if status == "ambiguous":
                await ctx.send(
                    f"❌ Nome ambíguo: '{hero}'. Sugestões: {format_hero_suggestions(suggestions)}",
                    delete_after=30
                )
                return
            if resolved_hero is None:
                await ctx.send(
                    f"❌ Não foi possível encontrar um herói parecido com '{hero}'. Sugestões: {format_hero_suggestions(suggestions)}",
                    delete_after=30
                )
                return
            resolved_heroes.append(resolved_hero)

        updated = update_league_match_heroes(league_match_id, resolved_heroes)
        if updated != len(players):
            await ctx.send(
                f"⚠️ Atualizados {updated} de {len(players)} heróis para a partida {league_match_id}. Verifique o match_id e tente novamente.",
                delete_after=20
            )
            return

        await ctx.message.delete()
        await ctx.send(f"✅ Heróis definidos para a partida {league_match_id}.")

    @bot.command(name="definirjogadores", aliases=["setmatchplayers", "setplayers"])
    async def cmd_set_match_player_names(ctx: commands.Context, league_match_id: int, *members: discord.Member):
        if not is_admin(ctx.author.id):
            await ctx.message.delete()
            await ctx.send("❌ Apenas administradores.", delete_after=5)
            return

        match = get_match_by_league_id(league_match_id)
        if not match:
            await ctx.send(f"❌ Partida da liga {league_match_id} não encontrada.", delete_after=10)
            return

        players = match.get("players_data") or []
        if len(members) != len(players):
            await ctx.send(
                f"❌ Esta partida possui {len(players)} jogadores. Forneça exatamente {len(players)} menções de Discord na ordem correta.",
                delete_after=20
            )
            return

        player_names = [member.display_name for member in members]
        updated = update_league_match_player_names(league_match_id, player_names)
        if updated != len(players):
            await ctx.send(
                f"⚠️ Atualizados {updated} de {len(players)} nomes para a partida {league_match_id}. Verifique o match_id e tente novamente.",
                delete_after=20
            )
            return

        for member in members:
            existing = get_player(member.id)
            if existing:
                upsert_player(member.id, member.display_name, existing["wins"], existing["losses"])
            else:
                upsert_player(member.id, member.display_name, 0, 0)

        await ctx.message.delete()
        await ctx.send(f"✅ Nomes dos jogadores definidos para a partida {league_match_id}.")

    @bot.command(name="nick", aliases=["setnick", "renomear"])
    async def cmd_set_match_player_nick(ctx: commands.Context, league_match_id: int, slot: int, *, rest: str):
        if not is_admin(ctx.author.id):
            await ctx.message.delete()
            await ctx.send("❌ Apenas administradores.", delete_after=5)
            return

        match = get_match_by_league_id(league_match_id)
        if not match:
            await ctx.send(f"❌ Partida da liga {league_match_id} não encontrada.", delete_after=10)
            return

        players = match.get("players_data") or []
        player_entry = next((p for p in players if p.get("slot") == slot), None)
        if not player_entry:
            await ctx.send(
                f"❌ Slot {slot} não encontrado para a partida {league_match_id}. Use o slot correto.",
                delete_after=20
            )
            return

        mention_match = re.search(r"<@!?(?P<id>\d+)>", rest)
        if not mention_match:
            await ctx.send(
                "❌ Mencione o jogador do Discord com @ e informe o novo nick. Ex: `!nick 123 3, Shadow Blade @Player`",
                delete_after=20
            )
            return

        discord_id = int(mention_match.group("id"))
        new_nick = (rest[:mention_match.start()] + rest[mention_match.end():]).strip()
        new_nick = new_nick.lstrip(", ")
        if not new_nick:
            await ctx.send(
                "❌ Informe o novo nick juntamente com a menção do jogador.",
                delete_after=20
            )
            return

        updated = update_league_match_player_name_by_slot(league_match_id, slot, new_nick)
        if not updated:
            await ctx.send(
                f"❌ Não foi possível atualizar o nick no slot {slot} da partida {league_match_id}.",
                delete_after=20
            )
            return

        user = None
        if ctx.guild:
            user = ctx.guild.get_member(discord_id)
        if user is None:
            user = bot.get_user(discord_id)

        if user:
            existing = get_player(user.id)
            if existing:
                upsert_player(user.id, existing["display_name"], existing["wins"], existing["losses"])
            else:
                upsert_player(user.id, user.display_name, 0, 0)

        log_action(
            ctx.author.id,
            ctx.author.display_name,
            "!nick",
            f"league_match_id={league_match_id} slot={slot} discord_id={discord_id} new_nick={new_nick}",
            affected_ids=[discord_id]
        )

        await ctx.send(
            f"✅ Nick do slot {slot} na partida {league_match_id} atualizado para **{new_nick}** (<@{discord_id}>)."
        )

    @bot.command(name="limparhistoricodeimagens", aliases=["clearimagehistory", "apagarhistoricodeimagens", "limparimagens"])
    async def cmd_clear_image_history(ctx: commands.Context, confirm: str = None):
        if not is_admin(ctx.author.id):
            await ctx.message.delete()
            await ctx.send("❌ Apenas administradores.", delete_after=5)
            return

        if confirm != "confirmar":
            await ctx.send(
                "⚠️ Para apagar o histórico de imagens OCR, use `!limparimagens confirmar`.",
                delete_after=15
            )
            return

        delete_match_screenshots()
        await ctx.message.delete()
        await ctx.send("🗑️ Histórico de imagens OCR apagado com sucesso.")
