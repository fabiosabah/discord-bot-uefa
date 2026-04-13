# -*- coding: utf-8 -*-
import discord
import logging
import json
import re
from discord.ext import commands
from core.config import ADMIN_IDS, IMAGE_CHANNEL_ID
from core.database import (
    upsert_player, add_win, add_loss, remove_win, remove_loss, delete_player,
    get_ranking, log_action, log_match_action, get_last_admin_action, delete_audit_log_entry,
    get_player, get_last_update, get_player_streak, get_player_match_history,
    get_match_summary, get_recent_match_summaries, get_player_top_opponents,
    get_raw_match_audit_events, delete_match_history, create_or_replace_manual_match,
    get_pending_match_screenshots, get_match_screenshot, set_match_screenshot_status,
    enqueue_match_screenshot, is_match_screenshot_enqueued,
    find_player_by_display_name, resolve_player_names_exact, insert_ocr_match,
    add_player_alias, remove_player_alias, get_player_aliases,
    get_image_channel, set_image_channel, clear_image_channel,
    delete_match_screenshots,
    update_match_hero
)
from core.ocr import can_process_ocr, process_match_screenshot
from core.utils.time import format_brazil_time, relative_time

audit_logger = logging.getLogger("Audit")

def is_admin(user_id: int) -> bool:
    return user_id in ADMIN_IDS

def points(wins: int, losses: int) -> int:
    return wins * 3 - losses


def parse_player_mapping(mapping_text: str) -> list[dict[str, object]]:
    mappings: list[dict[str, object]] = []
    if not mapping_text:
        return mappings

    for token in re.split(r"[;,]\s*", mapping_text.strip()):
        token = token.strip()
        if not token:
            continue

        hero = None
        hero_match = re.search(r'\bhero\s*=\s*("[^"]+"|[^,;\s]+)', token, flags=re.IGNORECASE)
        if hero_match:
            hero_value = hero_match.group(1).strip()
            hero = hero_value.strip('"').strip()
            token = (token[:hero_match.start()] + token[hero_match.end():]).strip()

        match = re.match(r'^(?:"(?P<quoted>[^"]+)"|(?P<plain>[^=]+))\s*=\s*@?(?P<id>\d+)$', token)
        if not match:
            continue

        player_key = match.group("quoted") or match.group("plain")
        player_key = player_key.strip()
        discord_id = int(match.group("id"))
        mappings.append({"player_key": player_key, "discord_id": discord_id, "hero": hero})

    return mappings


def build_footer(include_rules=True):
    last_update = get_last_update()

    if last_update:
        time= relative_time(last_update)
        brazil_time = format_brazil_time(last_update)
        
        update_text = f"📌 Última atualização: {time} • {brazil_time}"
    else:
        update_text = "🕹️ Nenhuma partida registrada ainda"
    if include_rules:
        return f"⚖️ Vitória +3 pts | Derrota -1 pt\n{update_text}"
    return update_text
    
def setup_score_commands(bot: commands.Bot):
    logger = logging.getLogger("ScoreSetup")
    logger.info("Carregando comandos de score...")

    @bot.command(name="registrar")
    async def cmd_registrar(ctx: commands.Context, member: discord.User, wins: int, losses: int):
        if not is_admin(ctx.author.id):
            await ctx.message.delete()
            await ctx.send("❌ Apenas administradores podem usar esse comando.", delete_after=5)
            return

        if wins < 0 or losses < 0:
            await ctx.send("❌ Valores inválidos.", delete_after=5)
            return

        upsert_player(member.id, member.display_name, wins, losses)
        pts = points(wins, losses)

        audit_logger.info(f"[REGISTRO] {ctx.author.name} → {member.display_name} ({wins}W/{losses}L)")

        log_action(
            ctx.author.id, ctx.author.display_name,
            "!registrar",
            f"{member.display_name} → W:{wins} L:{losses} Pts:{pts}",
            affected_ids=[member.id]
        )

        await ctx.message.delete()
        await ctx.send(f"✅ **{member.display_name}** atualizado: `{wins}V / {losses}D` → **{pts} pts**")


    @bot.command(name="venceu")
    async def cmd_venceu(ctx: commands.Context, *members: discord.Member):
        if not is_admin(ctx.author.id):
            await ctx.message.delete()
            await ctx.send("❌ Apenas administradores.", delete_after=5)
            return

        if not members:
            await ctx.send("⚠️ Mencione jogadores.", delete_after=5)
            return

        if len(members) != 5:
            await ctx.send("⚠️ O comando `!venceu` exige exatamente 5 jogadores.", delete_after=10)
            return

        nomes, ids = [], []
        for m in members:
            add_win(m.id, m.display_name)
            nomes.append(m.display_name)
            ids.append(m.id)

        log_match_action(ctx.author.id, ctx.author.display_name, "!venceu",
                         f"Vitória para: {', '.join(nomes)}", ids)

        await ctx.message.delete()
        await ctx.send(f"🏆 Vitória registrada para {' '.join(m.mention for m in members)}")


    @bot.command(name="perdeu")
    async def cmd_perdeu(ctx: commands.Context, *members: discord.Member):
        if not is_admin(ctx.author.id):
            await ctx.message.delete()
            await ctx.send("❌ Apenas administradores.", delete_after=5)
            return

        if not members:
            await ctx.send("⚠️ Mencione jogadores.", delete_after=5)
            return

        if len(members) != 5:
            await ctx.send("⚠️ O comando `!perdeu` exige exatamente 5 jogadores.", delete_after=10)
            return

        nomes, ids = [], []
        for m in members:
            add_loss(m.id, m.display_name)
            nomes.append(m.display_name)
            ids.append(m.id)

        log_match_action(ctx.author.id, ctx.author.display_name, "!perdeu",
                         f"Derrota para: {', '.join(nomes)}", ids)

        await ctx.message.delete()
        await ctx.send(f"💀 Derrota registrada para {' '.join(m.mention for m in members)}")


    @bot.command(name="desfazer", aliases=["undo", "z"])
    async def cmd_desfazer(ctx: commands.Context):
        if not is_admin(ctx.author.id):
            await ctx.message.delete()
            await ctx.send("❌ Apenas administradores.", delete_after=5)
            return

        action = get_last_admin_action(ctx.author.id)
        if not action:
            await ctx.send("⚠️ Nada para desfazer.", delete_after=5)
            return

        ids = json.loads(action["affected_ids"]) if action["affected_ids"] else []

        for pid in ids:
            if action["command"] == "!venceu":
                remove_win(pid)
            elif action["command"] == "!perdeu":
                remove_loss(pid)

        delete_audit_log_entry(action["id"])

        await ctx.message.delete()
        await ctx.send(f"↩️ Ação `{action['command']}` desfeita!")

    @bot.command(name="deletar")
    async def cmd_deletar(ctx: commands.Context, member: discord.User):
        if not is_admin(ctx.author.id):
            await ctx.message.delete()
            await ctx.send("❌ Apenas administradores podem usar esse comando.", delete_after=5)
            return

        player = get_player(member.id)
        if not player:
            await ctx.send("❌ Jogador não encontrado no ranking.", delete_after=5)
            return

        delete_player(member.id)
        log_action(
            ctx.author.id, ctx.author.display_name,
            "!deletar",
            f"Removido {member.display_name} ({member.id}) do ranking",
            affected_ids=[member.id]
        )

        await ctx.message.delete()
        await ctx.send(f"🗑️ **{member.display_name}** foi removido do ranking e do banco de dados.")

    @bot.command(name="perfil")
    async def cmd_perfil(ctx, target: discord.Member = None):
        target = target or ctx.author
        player = get_player(target.id)

        if not player:
            await ctx.send("❌ Esse jogador ainda não possui dados.")
            return

        wins = player["wins"]
        losses = player["losses"]
        games = wins + losses
        pts = points(wins, losses)
        winrate = (wins / games * 100) if games else 0

        streak = get_player_streak(target.id)
        streak_type = streak["streak_type"]
        streak_count = streak["streak_count"]

        recent_matches = get_player_match_history(target.id, limit=3)
        win_opponents = get_player_top_opponents(target.id, "win", limit=3)
        loss_opponents = get_player_top_opponents(target.id, "loss", limit=3)

        ranking = get_ranking()
        pos = next((i+1 for i,p in enumerate(ranking) if p["discord_id"] == target.id), None)

        # cor dinâmica
        if winrate >= 60:
            color = discord.Color.green()
        elif winrate >= 40:
            color = discord.Color.orange()
        else:
            color = discord.Color.red()

        # mensagem
        if winrate >= 70:
            msg = "🔥 Jogando demais!"
        elif winrate >= 50:
            msg = "⚖️ Equilibrado"
        else:
            msg = "📉 Precisa melhorar"

        if streak_type == "win":
            streak_text = f"🔥 Win streak de {streak_count}! Duplinha maldita na área."
        elif streak_type == "loss":
            streak_text = f"📉 Loss streak de {streak_count}. Segura a onda, parceiro."
        else:
            streak_text = "⚖️ Ainda sem sequência definida."

        title = f"📊 Perfil de {target.display_name}"
        if pos == 1:
            title = f"👑 Líder da Liga: {target.display_name}"

        embed = discord.Embed(title=title, description=f"{msg}\n{streak_text}", color=color)
        embed.set_thumbnail(url=target.display_avatar.url)

        embed.add_field(name="🏆 Vitórias", value=wins, inline=True)
        embed.add_field(name="💀 Derrotas", value=losses, inline=True)
        embed.add_field(name="🎯 Pontos", value=pts, inline=True)

        embed.add_field(name="🎮 Jogos", value=games, inline=True)
        embed.add_field(name="📈 Winrate", value=f"{winrate:.1f}%", inline=True)

        if streak_type and streak_count:
            if streak_type == "win":
                embed.add_field(name="🔥 Sequência", value=f"{streak_count} vitória(s) seguida(s)", inline=True)
            else:
                embed.add_field(name="📉 Sequência", value=f"{streak_count} derrota(s) seguida(s)", inline=True)

        if pos:
            embed.add_field(name="🥇 Ranking", value=f"#{pos}", inline=True)

        if recent_matches:
            recent_lines = []
            for item in recent_matches:
                summary = get_match_summary(item["match_id"])
                if not summary:
                    continue
                recent_lines.append(
                    f"`#{summary['match_id']:03d}` — "
                    f"🏆 {', '.join(summary['winners'])} | 💀 {', '.join(summary['losers'])}"
                )
            if recent_lines:
                embed.add_field(name="🕹️ Últimas 3 partidas", value="\n".join(recent_lines), inline=False)

        if win_opponents:
            win_lines = [f"{i+1}. {opp['display_name']} — {opp['count']}x" for i, opp in enumerate(win_opponents)]
            win_lines.append("\nMeu freguês: inimigo que você mais derrotou.")
            embed.add_field(name="😎 Meu freguês", value="\n".join(win_lines), inline=False)

        if loss_opponents:
            loss_lines = [f"{i+1}. {opp['display_name']} — {opp['count']}x" for i, opp in enumerate(loss_opponents)]
            loss_lines.append("\nMeu nemesis: inimigo que mais te derrotou.")
            embed.add_field(name="☠️ Meu nemesis", value="\n".join(loss_lines), inline=False)

        embed.set_footer(text="Sistema de Liga • UEFA Bot")

        await ctx.send(embed=embed)

    @bot.command(name="historico", aliases=["history"])
    async def cmd_historico(ctx: commands.Context, member: discord.Member = None, limit: int = 8):
        target = member or ctx.author
        history = get_player_match_history(target.id, limit)

        if not history:
            await ctx.send(f"📋 Nenhum histórico encontrado para {target.display_name}.")
            return

        lines = []
        for item in history:
            summary = get_match_summary(item["match_id"])
            if not summary:
                continue

            winners = summary["winners"] or ["(não registrado)"]
            losers = summary["losers"] or ["(não registrado)"]
            lines.append(
                f"`#{summary['match_id']:03d}` — "
                f"🏆 {', '.join(winners)} | 💀 {', '.join(losers)}"
            )

        embed = discord.Embed(
            title=f"📜 Histórico de partidas de {target.display_name}",
            description="\n".join(lines[:limit]),
            color=discord.Color.blurple()
        )
        embed.set_footer(text=f"Últimas {min(len(lines), limit)} partidas")
        await ctx.send(embed=embed)

    @bot.command(name="ultimas", aliases=["ultimaspartidas", "recentes"])
    async def cmd_ultimas(ctx: commands.Context, n: int = 7):
        if n < 1 or n > 12:
            await ctx.send("❌ Escolha um número entre 1 e 12.")
            return

        summaries = get_recent_match_summaries(n)
        summaries = [s for s in summaries if s]
        if not summaries:
            await ctx.send("📋 Nenhuma partida registrada ainda.")
            return

        lines = []
        for summary in summaries:
            winners = summary["winners"] or ["(não registrado)"]
            losers = summary["losers"] or ["(não registrado)"]
            lines.append(
                f"`#{summary['match_id']:03d}` — "
                f"🏆 {', '.join(winners)} | 💀 {', '.join(losers)}"
            )

        embed = discord.Embed(
            title="📈 Últimas partidas registradas na liga",
            description="\n".join(lines),
            color=discord.Color.gold()
        )
        embed.set_footer(text=f"Mostrando {len(lines)} partidas")
        await ctx.send(embed=embed)

    @bot.command(name="debugpartidas", aliases=["debugmatches", "auditmatches"])
    async def cmd_debug_partidas(ctx: commands.Context, limit: int = 30):
        if not is_admin(ctx.author.id):
            await ctx.message.delete()
            await ctx.send("❌ Apenas administradores.", delete_after=5)
            return

        events = get_raw_match_audit_events(limit)
        if not events:
            await ctx.send("📋 Nenhum evento de !venceu/!perdeu registrado.")
            return

        lines = []
        for event in events:
            players = event["players"] or ["(nenhum jogador registrado)"]
            match_id = f"#{event['match_id']:03d}" if event["match_id"] else "(sem match)"
            lines.append(
                f"`{event['audit_id']:03d}` {event['command']} {match_id} — {', '.join(players)}"
            )

        embed = discord.Embed(
            title="🛠️ Debug de partidas",
            description="\n".join(lines),
            color=discord.Color.orange()
        )
        embed.set_footer(text=f"Últimos {min(len(lines), limit)} eventos")
        await ctx.send(embed=embed)

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

    @bot.command(name="registrarcanalimagem", aliases=["registrarcanalocr"])
    async def cmd_register_image_channel(ctx: commands.Context):
        if not is_admin(ctx.author.id):
            await ctx.message.delete()
            await ctx.send("❌ Apenas administradores.", delete_after=5)
            return

        if not ctx.guild:
            await ctx.message.delete()
            await ctx.send("❌ Este comando só pode ser usado em um servidor.", delete_after=8)
            return

        set_image_channel(ctx.guild.id, ctx.channel.id)
        await ctx.message.delete()
        await ctx.send(
            f"✅ Canal <#{ctx.channel.id}> registrado para leitura de imagens de partida.",
            delete_after=15
        )

    @bot.command(name="limparcanalimagem", aliases=["limparcanalocr"])
    async def cmd_clear_image_channel(ctx: commands.Context):
        if not is_admin(ctx.author.id):
            await ctx.message.delete()
            await ctx.send("❌ Apenas administradores.", delete_after=5)
            return

        if not ctx.guild:
            await ctx.message.delete()
            await ctx.send("❌ Este comando só pode ser usado em um servidor.", delete_after=8)
            return

        clear_image_channel(ctx.guild.id)
        await ctx.message.delete()
        await ctx.send(
            "✅ Canal de imagem OCR foi removido. Imagens só serão processadas em canais explicitamente configurados.",
            delete_after=15
        )

    @bot.command(name="canalimagem", aliases=["imagemcanal"])
    async def cmd_show_image_channel(ctx: commands.Context):
        if not is_admin(ctx.author.id):
            await ctx.message.delete()
            await ctx.send("❌ Apenas administradores.", delete_after=5)
            return

        if not ctx.guild:
            await ctx.message.delete()
            await ctx.send("❌ Este comando só pode ser usado em um servidor.", delete_after=8)
            return

        image_channel_id = get_image_channel(ctx.guild.id)
        if image_channel_id:
            await ctx.send(f"📌 Canal de imagem OCR registrado: <#{image_channel_id}>")
        elif IMAGE_CHANNEL_ID:
            await ctx.send(f"📌 Canal de imagem OCR definido via ENV: <#{IMAGE_CHANNEL_ID}>")
        else:
            await ctx.send("⚠️ Nenhum canal de imagem OCR registrado.")

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

        metadata = None
        players = []
        if job["metadata"]:
            try:
                metadata = json.loads(job["metadata"])
                players = metadata.get("players") or []
            except json.JSONDecodeError:
                metadata = None

        description_lines = [
            f"**Status:** {job['status']}",
            f"**Imagem:** {job['image_url']}",
            f"**Criado em:** {job['created_at']}",
        ]

        if metadata:
            match_info = metadata.get("match_info") or metadata.get("game_details")
            if match_info:
                game_mode = match_info.get("game_mode") or match_info.get("mode")
                duration = match_info.get("duration")
                winner = match_info.get("winner") or ("Radiant" if match_info.get("radiant_win") else "Dire")
                score = match_info.get("score") or {}
                radiant_score = score.get("radiant")
                dire_score = score.get("dire")
                description_lines.append(f"**Modo de jogo:** {game_mode or 'desconhecido'}")
                description_lines.append(f"**Vencedor previsto:** {winner}")
                description_lines.append(f"**Duração:** {duration or 'desconhecida'}")
                if radiant_score is not None and dire_score is not None:
                    description_lines.append(f"**Placar:** Radiant {radiant_score} x {dire_score} Dire")
            else:
                description_lines.append(f"**Vencedor previsto:** {metadata.get('winner') or ('Radiant' if metadata.get('radiant_win') else 'Dire')}")
                description_lines.append(f"**Duração:** {metadata.get('duration') or metadata.get('game_details', {}).get('duration', 'desconhecida')}")

            if players:
                for index, player in enumerate(players, start=1):
                    player_name = player.get("name") or player.get("player") or "(sem nome)"
                    team = player.get("team") or player.get("side") or "?"
                    hero = player.get("hero") or ""
                    kda = player.get("score") or player.get("kda") or ""
                    display = f"{index}. `{player_name}` [{team}]"
                    if hero:
                        display += f" — {hero}"
                    if kda:
                        display += f" — {kda}"
                    description_lines.append(display)
            else:
                raw_text = metadata.get("raw_text") or metadata.get("metadata_payload", {}).get("raw_text")
                if raw_text:
                    raw_preview = raw_text.replace("\n", " ")[:400].strip()
                    if len(raw_text) > 400:
                        raw_preview += "..."
                    description_lines.append("**OCR raw:**")
                    description_lines.append(f"{raw_preview}")
                description_lines.append("*Nenhum jogador detectado nos metadados OCR. Use o texto acima para depurar ou confirme manualmente.*")
        else:
            description_lines.append("*Metadados OCR não disponíveis ou inválidos.*")

        embed = discord.Embed(
            title=f"🔎 Detalhes da imagem {job_id}",
            description="\n".join(description_lines),
            color=discord.Color.blue()
        )
        await ctx.send(embed=embed)

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

        def sanitize_code_block(text: str) -> str:
            return text.replace('```', '`\u200b``')

        def split_chunks(text: str, max_size: int = 1900) -> list[str]:
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

        sanitized_text = sanitize_code_block(raw_text)
        chunks = split_chunks(sanitized_text)

        if len(chunks) == 1:
            await ctx.send(f"```\n{chunks[0]}\n```")
            return

        await ctx.send(f"✅ Texto OCR bruto para o job {job_id} é longo e será exibido em {len(chunks)} partes.")
        for index, chunk in enumerate(chunks, start=1):
            await ctx.send(f"```\n{chunk}\n```\n({index}/{len(chunks)})")

    @bot.command(name="confirmarimagem", aliases=["confirmimage"])
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

    @bot.command(name="importarimagem", aliases=["importimage", "ocrimport"])
    async def cmd_import_image(ctx: commands.Context, job_id: int, *, mapping_text: str):
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

        players = parsed.get("players") or []
        if not isinstance(players, list) or not players:
            await ctx.send(f"❌ Não foi possível obter a lista de jogadores do job {job_id}.", delete_after=10)
            return

        mappings = parse_player_mapping(mapping_text)
        if not mappings:
            await ctx.send(
                "❌ Forneça mapeamentos no formato `1=@123456789012345678`, `\"Nome do jogador\"=@123...`, `PlayerName=@123` ou `1=@123 hero=Rubick`.",
                delete_after=15
            )
            return

        player_mapping: dict[str, dict[str, object]] = {}
        unresolved = []
        for mapping in mappings:
            player_key = mapping["player_key"]
            discord_id = mapping["discord_id"]
            hero = mapping.get("hero")

            if player_key.isdigit():
                index = int(player_key) - 1
                if index < 0 or index >= len(players):
                    await ctx.send(f"❌ Índice inválido: {player_key}", delete_after=10)
                    return
                player_key = players[index].get("name") or players[index].get("player") or f"player_{player_key}"

            player_mapping[player_key] = {"discord_id": discord_id, "hero": hero}

        extracted_names = [ (p.get("name") or p.get("player") or "").strip() for p in players ]
        missing_names = [name for name in extracted_names if name and name not in player_mapping]
        if missing_names:
            auto = resolve_player_names_exact(missing_names)
            for name, discord_id in auto.items():
                if name not in player_mapping:
                    player_mapping[name] = {"discord_id": discord_id}
            missing_names = [name for name in missing_names if name not in player_mapping]

        if missing_names:
            await ctx.send(
                f"❌ Ainda faltam mapear os seguintes jogadores: {', '.join(missing_names)}",
                delete_after=20
            )
            return

        try:
            insert_ocr_match(job_id, player_mapping, ctx.author.id, ctx.author.display_name)
        except Exception as exc:
            await ctx.send(f"❌ Falha ao importar imagem: {exc}", delete_after=20)
            return

        await ctx.message.delete()
        await ctx.send(f"✅ Imagem {job_id} importada como partida no banco.")

    @bot.command(name="fixhero", aliases=["corrigirhero"])
    async def cmd_fix_hero(ctx: commands.Context, match_id: int, member: discord.Member, *, hero: str):
        if not is_admin(ctx.author.id):
            await ctx.message.delete()
            await ctx.send("❌ Apenas administradores.", delete_after=5)
            return

        hero = hero.strip()
        if not hero:
            await ctx.send("❌ Informe o herói após o jogador.", delete_after=10)
            return

        updated = update_match_hero(match_id, member.id, hero)
        if not updated:
            await ctx.send(
                f"❌ Não foi possível encontrar o jogador {member.mention} na partida {match_id}."
                " Verifique se o match_id está correto e se o jogador pertence à partida.",
                delete_after=20
            )
            return

        log_action(
            ctx.author.id,
            ctx.author.display_name,
            "!fixhero",
            f"match_id={match_id} discord_id={member.id} hero={hero}",
            affected_ids=[member.id]
        )

        await ctx.message.delete()
        await ctx.send(f"✅ Hero de {member.mention} atualizado para **{hero}** na partida {match_id}.")

    @bot.command(name="addalias", aliases=["aliasadd", "alias"])
    async def cmd_add_alias(ctx: commands.Context, member: discord.Member, *, alias: str):
        if not is_admin(ctx.author.id):
            await ctx.message.delete()
            await ctx.send("❌ Apenas administradores.", delete_after=5)
            return

        alias = alias.strip()
        if not alias:
            await ctx.send("❌ Informe o apelido após o membro.", delete_after=10)
            return

        add_player_alias(member.id, alias)
        log_action(
            ctx.author.id,
            ctx.author.display_name,
            "!addalias",
            f"discord_id={member.id} alias={alias}",
            affected_ids=[member.id]
        )

        await ctx.message.delete()
        await ctx.send(f"✅ Alias `{alias}` adicionado para {member.mention}.")

    @bot.command(name="removealias", aliases=["delalias"])
    async def cmd_remove_alias(ctx: commands.Context, member: discord.Member, *, alias: str):
        if not is_admin(ctx.author.id):
            await ctx.message.delete()
            await ctx.send("❌ Apenas administradores.", delete_after=5)
            return

        alias = alias.strip()
        if not alias:
            await ctx.send("❌ Informe o alias a ser removido.", delete_after=10)
            return

        remove_player_alias(member.id, alias)
        log_action(
            ctx.author.id,
            ctx.author.display_name,
            "!removealias",
            f"discord_id={member.id} alias={alias}",
            affected_ids=[member.id]
        )

        await ctx.message.delete()
        await ctx.send(f"✅ Alias `{alias}` removido para {member.mention}.")

    @bot.command(name="aliases", aliases=["aliaslist"])
    async def cmd_alias_list(ctx: commands.Context, member: discord.Member):
        if not is_admin(ctx.author.id):
            await ctx.message.delete()
            await ctx.send("❌ Apenas administradores.", delete_after=5)
            return

        aliases = get_player_aliases(member.id)
        if not aliases:
            await ctx.send(f"⚠️ Nenhum alias registrado para {member.mention}.", delete_after=10)
            return

        alias_list = '\n'.join(f"- `{alias}`" for alias in aliases)
        await ctx.send(f"📝 Aliases para {member.mention}:\n{alias_list}")

    @bot.command(name="registrarmatch", aliases=["matchfix", "matchmanual"])
    async def cmd_registrar_match(ctx: commands.Context, match_id: int, *, rest: str):
        if not is_admin(ctx.author.id):
            await ctx.message.delete()
            await ctx.send("❌ Apenas administradores.", delete_after=5)
            return

        if "--" not in rest:
            await ctx.send(
                "❌ Use: `!registrarmatch <match_id> @win1 @win2 -- @loss1 @loss2`",
                delete_after=15
            )
            return

        winners_text, losers_text = [part.strip() for part in rest.split("--", 1)]
        wins = [int(m) for m in re.findall(r"<@!?(\d+)>", winners_text)]
        losses = [int(m) for m in re.findall(r"<@!?(\d+)>", losers_text)]

        if not wins and not losses:
            await ctx.send("❌ Informe ao menos um vencedor ou um derrotado.", delete_after=15)
            return

        create_or_replace_manual_match(match_id, wins, losses, ctx.author.id, ctx.author.display_name)
        await ctx.message.delete()
        await ctx.send(f"✅ Match #{match_id:03d} registrado manualmente.")

    @bot.command(name="limparhistorico", aliases=["clearmatchhistory", "apagarhistorico"])
    async def cmd_clear_match_history(ctx: commands.Context, confirm: str = None):
        if not is_admin(ctx.author.id):
            await ctx.message.delete()
            await ctx.send("❌ Apenas administradores.", delete_after=5)
            return

        if confirm != "confirmar":
            await ctx.send(
                "⚠️ Para apagar o histórico de partidas, use `!limparhistorico confirmar`.",
                delete_after=15
            )
            return

        delete_match_history()
        await ctx.message.delete()
        await ctx.send("🗑️ Histórico de partidas apagado com sucesso.")

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

    @bot.command(name="tabela")
    async def cmd_tabela(ctx: commands.Context):
        ranking = get_ranking()

        if not ranking:
            await ctx.send("📋 Nenhum jogador registrado ainda.")
            return

        embed = discord.Embed(title="🏆 Tabela do Campeonato", color=discord.Color.gold())

        linhas = []
        for i, p in enumerate(ranking):
            prefix = "👑" if i == 0 else "🥈" if i == 1 else "🥉" if i == 2 else f"{i+1}."
            linhas.append(
                f"{prefix} **{p['display_name']}** — "
                f"{p['points']} pts "
                f"(`{p['wins']}V / {p['losses']}D` — {p['games']} jogos)"
            )

        embed.description = "\n".join(linhas)
        embed.set_footer(text=build_footer())

        await ctx.send(embed=embed)

    @bot.command(name="top")
    async def cmd_top(ctx: commands.Context, n: int = 10):
        # 🔒 validação
        if n < 1 or n > 15:
            await ctx.send("❌ Escolha um número entre 1 e 15.")
            return

        ranking = get_ranking()

        if not ranking:
            await ctx.send("📋 Nenhum jogador registrado ainda.")
            return

        top_players = ranking[:n]

        embed = discord.Embed(
            title=f"🏆 Top {n} Jogadores",
            color=discord.Color.gold()
        )

        linhas = []
        for i, p in enumerate(top_players):
            if i == 0:
                prefix = "👑"
            elif i == 1:
                prefix = "🥈"
            elif i == 2:
                prefix = "🥉"
            else:
                prefix = f"`{i+1:02d}.`"

            linhas.append(
                f"{prefix} **{p['display_name']}** — "
                f"{p['points']} pts "
                f"(`{p['wins']}V/{p['losses']}D`)"
            )

        embed.description = "🔥 **Melhores jogadores da liga**\n\n" + "\n".join(linhas)
        embed.set_footer(text=build_footer())

        await ctx.send(embed=embed)