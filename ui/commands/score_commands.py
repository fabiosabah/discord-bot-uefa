# -*- coding: utf-8 -*-
import discord
import logging
import json
import re
from typing import Any
from discord.ext import commands
from core.config import ADMIN_IDS, IMAGE_CHANNEL_ID
from core.dota_heroes import resolve_hero_name, format_hero_suggestions
from core.database import (
    upsert_player, add_win, add_loss, remove_win, remove_loss, delete_player,
    get_ranking, log_action, get_last_admin_action, delete_audit_log_entry,
    get_player, get_last_update, get_player_streak, get_player_match_history,
    get_player_history_stats, get_player_top_heroes, get_player_top_teammates,
    get_match_summary, get_recent_match_summaries, get_player_top_opponents,
    get_raw_match_audit_events, delete_match_history, create_or_replace_manual_match,
    get_pending_match_screenshots, get_match_screenshot, set_match_screenshot_status,
    enqueue_match_screenshot, is_match_screenshot_enqueued,
    find_player_by_display_name, resolve_player_names_exact, insert_ocr_match, get_match_by_league_id,
    add_player_alias, remove_player_alias, get_player_aliases,
    get_image_channel, set_image_channel, clear_image_channel,
    delete_match_screenshots, delete_match_screenshot, delete_match_history,
    delete_league_match,
    update_match_hero, update_league_match_heroes, update_league_match_player_name_by_slot,
    update_league_match_hero_by_slot, update_league_match_player_names, update_league_match_duration,
    get_player_match_stats_from_matches, get_player_top_heroes_from_matches,
    get_player_top_teammates_from_matches, get_player_top_opponents_from_matches,
    get_player_match_history_from_matches, get_player_streak_from_matches,
    get_ranking_from_matches, diagnose_and_fix_kda_data, find_unregistered_match_players,
    get_player_top_heroes_with_winrate_from_matches, get_player_head_to_head_from_matches,
    get_player_top_win_teammates_from_matches, get_player_top_loss_teammates_from_matches, get_last_ocr_match_info,
    get_match_created_at, count_match_deletions_today, get_streak_highlights_from_matches
)
from core.ocr import can_process_ocr, process_match_screenshot, _normalize_team, _normalize_team
from core.utils.time import format_brazil_time, relative_time

audit_logger = logging.getLogger("Audit")

def is_admin(user_id: int) -> bool:
    return user_id in ADMIN_IDS

def points(wins: int, losses: int) -> int:
    return wins * 3 - losses


def winrate_tier(winrate: float) -> str:
    if winrate >= 85:
        return "👑 Lenda da liga — ninguém te para"
    elif winrate >= 75:
        return "🏆 Praticamente intocável"
    elif winrate >= 65:
        return "💪 Fera do lobby — respeito ganho"
    elif winrate >= 55:
        return "🔥 Mandando bem, continua assim"
    elif winrate >= 50:
        return "⚖️ Na linha do equilíbrio, pode mais"
    elif winrate >= 42:
        return "📉 Precisa melhorar — mas tem jeito"
    elif winrate >= 32:
        return "😬 Isso tá doendo de ver, parceiro"
    elif winrate >= 20:
        return "🪦 A equipe reza quando te vê entrar"
    else:
        return "🫀 TESTE PRA CARDÍACO — sua equipe não aguenta"


def _format_ocr_player_line(player: dict[str, Any], index: int) -> str:
    slot = player.get("slot")
    if isinstance(slot, str) and slot.isdigit():
        slot = int(slot)
    if not isinstance(slot, int):
        slot = index

    player_name = (
        player.get("player_name")
        or player.get("name")
        or player.get("player")
        or "desconhecido"
    )
    player_name = str(player_name).strip() or "desconhecido"

    hero_name = (
        player.get("hero_name")
        or player.get("hero")
        or player.get("heroi")
        or "herói desconhecido"
    )
    hero_name = str(hero_name).strip() or "herói desconhecido"

    team = player.get("team") or player.get("side") or ""
    if isinstance(team, str):
        team = team.strip().lower()
    team_label = None
    if team in {"radiant", "dire"}:
        team_label = team.title()
    elif team in {"r", "rad", "radiante"}:
        team_label = "Radiant"
    elif team in {"d", "dir", "dire"}:
        team_label = "Dire"

    # Extrair KDA dos campos individuais (sempre dict-like)
    kills = player.get("kills")
    deaths = player.get("deaths") 
    assists = player.get("assists")
    kda = ""
    if kills is not None or deaths is not None or assists is not None:
        kda = f"{kills or '?'} / {deaths or '?'} / {assists or '?'}"

    networth = player.get("networth") or player.get("net_worth")
    networth_text = f"NW {networth}" if networth is not None and str(networth).strip() else ""

    parts = [f"{slot}", player_name, hero_name]
    if team_label:
        parts.append(team_label)
    if kda:
        parts.append(f"KDA {kda}")
    if networth_text:
        parts.append(networth_text)

    return " · ".join(parts)


def _get_winner_team(parsed: dict[str, Any]) -> str | None:
    match_info = parsed.get("match_info") or parsed.get("game_details") or {}
    winner = (
        match_info.get("winner_team")
        or match_info.get("winner")
        or parsed.get("winner")
    )
    normalized = _normalize_team(winner) if winner is not None else None
    if normalized in {"radiant", "dire"}:
        return normalized

    radiant_win = parsed.get("radiant_win")
    if isinstance(radiant_win, bool):
        return "radiant" if radiant_win else "dire"

    return None


def _find_ocr_job_player_entry(parsed: dict[str, Any], slot: int) -> tuple[dict[str, Any] | None, str | None]:
    for key in ("players_data", "players"):
        players = parsed.get(key)
        if not isinstance(players, list):
            continue

        for entry in players:
            if not isinstance(entry, dict):
                continue
            if entry.get("slot") is not None:
                try:
                    if int(entry.get("slot")) == slot:
                        return entry, key
                except (TypeError, ValueError):
                    continue

        if 1 <= slot <= len(players):
            entry = players[slot - 1]
            if isinstance(entry, dict):
                return entry, key

    return None, None


def _set_ocr_job_metadata(job_id: int, parsed: dict[str, Any]) -> None:
    job = get_match_screenshot(job_id)
    if job is None:
        raise ValueError(f"Job de imagem {job_id} não encontrado")
    set_match_screenshot_status(job_id, job["status"] or "processed", metadata=json.dumps(parsed, ensure_ascii=False))


def _get_ocr_player_name(player: dict[str, Any]) -> str:
    return (
        str(player.get("player_name") or player.get("name") or player.get("player") or "").strip()
    )


def build_ocr_job_summary_text(job_id: int, parsed: dict[str, Any]) -> str:
    if parsed.get("valid_dota_screenshot") is False:
        return (
            f"⚠️ Job {job_id} processado, mas não parece ser um placar válido de Dota 2. "
            f"Use `!detalhesimagem {job_id}` para revisar o JSON ou `!rawtextimagem {job_id}` para o texto OCR bruto."
        )

    match_info = parsed.get("match_info") or parsed.get("game_details") or {}
    score = match_info.get("score") or {}
    radiant_score = score.get("radiant") or parsed.get("radiant_score")
    dire_score = score.get("dire") or parsed.get("dire_score")
    winner_team = _get_winner_team(parsed)
    winner_text = winner_team.title() if winner_team else "não identificado"
    duration = match_info.get("duration") or parsed.get("duration") or parsed.get("match_date")
    mode = match_info.get("game_mode") or match_info.get("mode") or parsed.get("mode")

    players = parsed.get("players_data") or parsed.get("players") or []
    lines = [
        f"✅ OCR completo para o job {job_id}",
        "",
        f"📊 Placar: `{radiant_score or 0}` x `{dire_score or 0}`",
        f"🏆 Vencedor: {winner_text}",
    ]

    if duration:
        lines.append(f"⏱️ Duração/data: {duration}")
    if mode:
        lines.append(f"🎮 Modo: {mode}")

    if not players:
        lines.append("")
        lines.append("⚠️ Não foi possível extrair a lista de jogadores do OCR.")
        lines.append(f"Use `!detalhesimagem {job_id}` para ver o JSON completo.")
        return "\n".join(lines)

    lines.extend(["", "👥 Jogadores detectados:"])
    for index, player in enumerate(players, start=1):
        lines.append(_format_ocr_player_line(player, index))

    lines.extend(["", "🛠️ Correções e próximos passos:"])
    lines.append(f"• `!importarimagem {job_id} <mapeamento>` para salvar a partida no banco.")
    if not winner_team:
        lines.append(
            f"• O vencedor não foi extraído. Defina manualmente com `!setjobwinner {job_id} radiant|dire` "
            f"ou cancele com `!removerimagem {job_id} confirmar`."
        )
    lines.extend([
        f"• `!detalhesimagem {job_id}` para ver o JSON processado.",
        f"• `!rawtextimagem {job_id}` para ver o texto OCR bruto.",
        f"• Use `!ocrhero {job_id} <slot> <novo herói>`, `!ocrnick {job_id} <slot> <novo nick>` ou `!ocruser {job_id} <@u1> <@u2> ...` / `!ocruser {job_id} <slot> @u1` para corrigir antes de importar.",
        f"• Depois de corrigir a imagem, use `!ocrok {job_id}` para importar o job diretamente se todos os nicks estiverem mapeados.",
        f"• Use `!confirmarimagem {job_id} <texto>` ou `!editarimagem {job_id} <texto>` para corrigir metadados diretamente.",
        "• Se o nick ainda não estiver registrado, use `!addalias @Usuario NomeOCR`.",
        "• Depois de importar, ajuste com `!fixhero <league_match_id> <slot>, <herói>` e `!nick <league_match_id> <slot>, <novo nick> @Usuario>`",
    ])
    if winner_team:
        lines.append(
            "• Se o vencedor estiver incorreto, use `!setjobwinner {job_id} radiant|dire` antes de importar."
        )

    return "\n".join(lines)


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

        match = re.match(r'^(?:"(?P<quoted>[^"]+)"|(?P<plain>[^=]+))\s*=\s*@?<?@!?\s*(?P<id>\d+)>?$', token)
        if not match:
            continue

        player_key = match.group("quoted") or match.group("plain")
        player_key = player_key.strip()
        discord_id = int(match.group("id"))
        mappings.append({"player_key": player_key, "discord_id": discord_id, "hero": hero})

    return mappings


def _resolve_command_members(ctx: commands.Context, tokens: tuple[str, ...]) -> tuple[list[discord.User], str | None]:
    if not tokens:
        return [], "⚠️ Mencione 5 jogadores ou passe 5 IDs de usuário."

    if len(tokens) != 5:
        return [], "⚠️ O comando exige exatamente 5 jogadores. Use menções de usuário ou IDs numéricos."

    members: list[discord.User] = []
    for token in tokens:
        token = token.strip()
        if not token:
            return [], "⚠️ Foi fornecido um argumento vazio."

        if re.match(r"^<@&\d+>$", token):
            return [], "⚠️ Menção de cargo detectada. Use menção de usuário ou ID de usuário."

        discord_id = None
        mention_match = re.match(r"^<@!?(?P<id>\d+)>$", token)
        if mention_match:
            discord_id = int(mention_match.group("id"))
        elif token.isdigit():
            discord_id = int(token)
        else:
            return [], f"⚠️ Não foi possível resolver o jogador '{token}'. Use menção de usuário ou ID numérico."

        user = None
        if ctx.guild:
            user = ctx.guild.get_member(discord_id)
        if user is None:
            user = ctx.bot.get_user(discord_id)
        if user is None:
            return [], f"⚠️ Usuário com ID {discord_id} não encontrado. Certifique-se de usar um ID válido ou uma menção de um usuário presente no servidor."

        members.append(user)

    return members, None


async def _resolve_command_user_mentions(ctx: commands.Context, tokens: tuple[str, ...]) -> tuple[list[discord.User], str | None]:
    if not tokens:
        return [], "⚠️ Forneça pelo menos um usuário. Use menções de usuário ou IDs numéricos."

    members: list[discord.User] = []
    for token in tokens:
        token = token.strip()
        if not token:
            return [], "⚠️ Foi fornecido um argumento vazio."

        if re.match(r"^<@&\d+>$", token):
            return [], "⚠️ Menção de cargo detectada. Use menção de usuário ou ID de usuário."

        discord_id = None
        mention_match = re.match(r"^<@!?(?P<id>\d+)>$", token)
        if mention_match:
            discord_id = int(mention_match.group("id"))
        elif token.isdigit():
            discord_id = int(token)
        else:
            return [], f"⚠️ Não foi possível resolver o jogador '{token}'. Use menção de usuário ou ID numérico."

        user = None
        if ctx.guild:
            user = ctx.guild.get_member(discord_id)
        if user is None:
            user = ctx.bot.get_user(discord_id)
        if user is None:
            try:
                user = await ctx.bot.fetch_user(discord_id)
            except discord.NotFound:
                user = None
        if user is None:
            return [], f"⚠️ Usuário com ID {discord_id} não encontrado. Certifique-se de usar um ID válido ou uma menção de um usuário presente no servidor."

        members.append(user)

    return members, None


class UndoConfirmView(discord.ui.View):
    def __init__(self, author_id: int, action_id: int, command: str, affected_ids: list[int]):
        super().__init__(timeout=60)
        self.author_id = author_id
        self.action_id = action_id
        self.command = command
        self.affected_ids = affected_ids

        action_name = "remover vitória" if command == "!venceu" else "remover derrota"
        label = f"Confirmar {action_name} ({len(affected_ids)} IDs)"
        for child in self.children:
            if isinstance(child, discord.ui.Button) and child.custom_id == "undo_confirm":
                child.label = label
                break

    async def interaction_check(self, interaction: discord.Interaction) -> bool:
        if interaction.user.id != self.author_id:
            await interaction.response.send_message(
                "❌ Apenas quem solicitou o desfazer pode confirmar esta ação.",
                ephemeral=True
            )
            return False
        return True

    async def _resolve_user_name(self, interaction: discord.Interaction, discord_id: int) -> str:
        if interaction.guild:
            member = interaction.guild.get_member(discord_id)
            if member is not None:
                return member.display_name

        user = interaction.client.get_user(discord_id)
        if user is not None:
            return user.name

        try:
            user = await interaction.client.fetch_user(discord_id)
            return user.name
        except discord.NotFound:
            return str(discord_id)

    async def _format_affected_users(self, interaction: discord.Interaction) -> str:
        if not self.affected_ids:
            return "nenhum"

        names = []
        for discord_id in self.affected_ids:
            names.append(await self._resolve_user_name(interaction, discord_id))
        return ", ".join(names)

    @discord.ui.button(label="Confirmar desfazer", style=discord.ButtonStyle.danger, custom_id="undo_confirm")
    async def confirm(self, interaction: discord.Interaction, button: discord.ui.Button):
        if self.command == "!venceu":
            for discord_id in self.affected_ids:
                remove_win(discord_id)
        elif self.command == "!perdeu":
            for discord_id in self.affected_ids:
                remove_loss(discord_id)

        delete_audit_log_entry(self.action_id)
        for child in self.children:
            child.disabled = True

        affected_users = await self._format_affected_users(interaction)
        await interaction.response.edit_message(
            content=(
                f"✅ Ação `{self.command}` confirmada e desfeita."
                f"\nUsuários afetados: {affected_users}"
            ),
            view=self
        )

    @discord.ui.button(label="Cancelar", style=discord.ButtonStyle.secondary, custom_id="undo_cancel")
    async def cancel(self, interaction: discord.Interaction, button: discord.ui.Button):
        for child in self.children:
            child.disabled = True
        await interaction.response.edit_message(
            content="❌ Operação de desfazer cancelada.",
            view=self
        )


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


    @bot.command(name="venceu", aliases=["venceu_id", "venceuid"])
    async def cmd_venceu(ctx: commands.Context, *member_tokens: str):
        if not is_admin(ctx.author.id):
            await ctx.message.delete()
            await ctx.send("❌ Apenas administradores.", delete_after=5)
            return

        members, error = _resolve_command_members(ctx, member_tokens)
        if error:
            await ctx.send(error, delete_after=15)
            return

        nomes, ids = [], []
        for m in members:
            add_win(m.id, m.display_name)
            nomes.append(m.display_name)
            ids.append(m.id)

        log_action(
            ctx.author.id,
            ctx.author.display_name,
            "!venceu",
            f"Vitória para: {', '.join(nomes)}",
            ids
        )

        await ctx.message.delete()
        await ctx.send(f"🏆 Vitória registrada para {' '.join(getattr(m, 'mention', str(m.id)) for m in members)}")


    @bot.command(name="perdeu", aliases=["perdeu_id", "perdeuid"])
    async def cmd_perdeu(ctx: commands.Context, *member_tokens: str):
        if not is_admin(ctx.author.id):
            await ctx.message.delete()
            await ctx.send("❌ Apenas administradores.", delete_after=5)
            return

        members, error = _resolve_command_members(ctx, member_tokens)
        if error:
            await ctx.send(error, delete_after=15)
            return

        nomes, ids = [], []
        for m in members:
            add_loss(m.id, m.display_name)
            nomes.append(m.display_name)
            ids.append(m.id)

        log_action(
            ctx.author.id,
            ctx.author.display_name,
            "!perdeu",
            f"Derrota para: {', '.join(nomes)}",
            ids
        )

        await ctx.message.delete()
        await ctx.send(f"💀 Derrota registrada para {' '.join(getattr(m, 'mention', str(m.id)) for m in members)}")


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

        affected_ids = json.loads(action["affected_ids"]) if action["affected_ids"] else []
        if action["command"] not in {"!venceu", "!perdeu"}:
            await ctx.send("⚠️ A última ação não pode ser desfeita com este comando.", delete_after=10)
            return

        action_type = "remover vitória" if action["command"] == "!venceu" else "remover derrota"
        affected_text = ", ".join(str(_id) for _id in affected_ids) if affected_ids else "nenhum"

        await ctx.message.delete()
        await ctx.send(
            f"⚠️ Confirme o desfazer da ação `{action['command']}`.\n"
            f"A ação irá {action_type} para os IDs: {affected_text}.",
            view=UndoConfirmView(ctx.author.id, action["id"], action["command"], affected_ids)
        )

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

    @bot.command(name="perfil1")
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

    @bot.command(name="perfil", aliases=["perfil2"])
    async def cmd_perfil2(ctx, target: discord.Member = None):
        target = target or ctx.author
        stats = get_player_match_stats_from_matches(target.id)

        if stats["matches"] == 0:
            await ctx.send(f"❌ Nenhum histórico OCR encontrado para **{target.display_name}**.")
            return

        all_heroes    = get_player_top_heroes_with_winrate_from_matches(target.id, limit=50)
        head_to_head  = get_player_head_to_head_from_matches(target.id)
        win_teammates  = get_player_top_win_teammates_from_matches(target.id, limit=3)
        loss_teammates = get_player_top_loss_teammates_from_matches(target.id, limit=3)
        streak        = get_player_streak_from_matches(target.id)
        recent        = get_player_match_history_from_matches(target.id, limit=5)

        ranking   = get_ranking_from_matches()
        rank_pos  = next((i + 1 for i, p in enumerate(ranking) if p["discord_id"] == target.id), None)
        rank_pts  = next((p["points"] for p in ranking if p["discord_id"] == target.id), None)

        wins    = stats["wins"]
        losses  = stats["losses"]
        winrate = stats["winrate"]

        defuntos = sorted(
            [h for h in head_to_head if h["player_wins"] > h["opponent_wins"]],
            key=lambda x: x["player_wins"] - x["opponent_wins"],
            reverse=True
        )[:3]

        nemesis = sorted(
            [h for h in head_to_head if h["opponent_wins"] > h["player_wins"]],
            key=lambda x: x["opponent_wins"] - x["player_wins"],
            reverse=True
        )[:3]

        if winrate >= 60:
            color = discord.Color.green()
        elif winrate >= 40:
            color = discord.Color.gold()
        else:
            color = discord.Color.red()

        embed = discord.Embed(
            title=f"📊 Perfil de {target.display_name}",
            description=winrate_tier(winrate),
            color=color
        )
        embed.set_thumbnail(url=target.display_avatar.url)

        # ── Stats base ──
        embed.add_field(name="🎮 Partidas", value=str(stats["matches"]), inline=True)
        embed.add_field(name="🏆 Vitórias",  value=str(wins),           inline=True)
        embed.add_field(name="💀 Derrotas",  value=str(losses),         inline=True)
        embed.add_field(name="📈 Winrate",   value=f"{winrate:.1f}%",   inline=True)

        if rank_pos is not None:
            embed.add_field(name="🥇 Ranking", value=f"#{rank_pos} — {rank_pts} pts", inline=True)

        if stats["kda_rows"]:
            n = stats["kda_rows"]
            embed.add_field(
                name="⚔️ KDA Médio",
                value=f"{stats['total_kills']/n:.1f} / {stats['total_deaths']/n:.1f} / {stats['total_assists']/n:.1f}",
                inline=True
            )

        # ── Streak ──
        s_type  = streak["streak_type"]
        s_count = streak["streak_count"]
        if s_type == "win" and s_count >= 2:
            embed.add_field(name="🔥 Sequência", value=f"{s_count} vitórias seguidas", inline=True)
        elif s_type == "loss" and s_count >= 2:
            embed.add_field(name="📉 Sequência", value=f"{s_count} derrotas seguidas", inline=True)

        # ── Heróis ──
        top5_played  = all_heroes[:5]
        eligible_wr  = [h for h in all_heroes if h["plays"] >= 3]
        top3_best_wr = sorted(eligible_wr, key=lambda x: (-x["winrate"], -x["plays"]))[:3]
        top3_worst_wr = sorted(eligible_wr, key=lambda x: (x["winrate"], -x["plays"]))[:3]

        if top5_played:
            lines = [
                f"{i+1}. **{h['hero']}** — {h['plays']} jogos · {h['winrate']:.0f}% WR"
                for i, h in enumerate(top5_played)
            ]
            embed.add_field(name="🦸 Top 5 mais jogados", value="\n".join(lines), inline=False)

        if top3_best_wr:
            lines = [
                f"{i+1}. **{h['hero']}** — {h['winrate']:.0f}% WR ({h['plays']} jogos)"
                for i, h in enumerate(top3_best_wr)
            ]
            embed.add_field(name="📈 Melhor winrate", value="\n".join(lines), inline=True)

        if top3_worst_wr:
            lines = [
                f"{i+1}. **{h['hero']}** — {h['winrate']:.0f}% WR ({h['plays']} jogos)"
                for i, h in enumerate(top3_worst_wr)
            ]
            embed.add_field(name="📉 Pior winrate", value="\n".join(lines), inline=True)

        # ── Com quem mais vence / perde ──
        if win_teammates:
            lines = [
                f"{i+1}. **{t['display_name']}** — {t['count']} vitórias juntos"
                for i, t in enumerate(win_teammates)
            ]
            embed.add_field(name="🤝 Vence principalmente com", value="\n".join(lines), inline=False)

        if loss_teammates:
            lines = [
                f"{i+1}. **{t['display_name']}** — {t['count']} derrotas juntos"
                for i, t in enumerate(loss_teammates)
            ]
            embed.add_field(name="😤 Perde principalmente com", value="\n".join(lines), inline=False)

        # ── Defunto (eu domino) ──
        if defuntos:
            lines = []
            for i, d in enumerate(defuntos):
                diff = d["player_wins"] - d["opponent_wins"]
                lines.append(
                    f"{i+1}. **{d['display_name']}** — "
                    f"{d['player_wins']}V/{d['opponent_wins']}D em {d['total']} confrontos (+{diff})"
                )
            embed.add_field(name="⚰️ Meu Defunto", value="\n".join(lines), inline=False)

        # ── Nemesis (me domina) ──
        if nemesis:
            lines = []
            for i, m in enumerate(nemesis):
                diff = m["opponent_wins"] - m["player_wins"]
                lines.append(
                    f"{i+1}. **{m['display_name']}** — "
                    f"{m['player_wins']}V/{m['opponent_wins']}D em {m['total']} confrontos (−{diff})"
                )
            embed.add_field(name="👹 Meu Nemesis", value="\n".join(lines), inline=False)

        # ── Últimas 5 partidas ──
        if recent:
            lines = []
            for r in recent:
                icon  = "✅" if r["result"] == "win" else "❌"
                hero  = r["hero"] or "?"
                k, d, a = r.get("kills"), r.get("deaths"), r.get("assists")
                kda   = f"{k}/{d}/{a}" if k is not None and d is not None and a is not None else "?/?/?"
                lines.append(f"{icon} `#{r['league_match_id']}` {hero} · {kda}")
            embed.add_field(name="🕹️ Últimas 5 partidas", value="\n".join(lines), inline=False)

        embed.set_footer(text="Perfil gerado a partir de partidas importadas via OCR")
        await ctx.send(embed=embed)

    @bot.command(name="listarpartidas", aliases=["partidas", "matchlist"])
    async def cmd_listar_partidas(ctx, target: discord.Member = None, limit: int = 30):
        target = target or ctx.author
        if limit < 1 or limit > 200:
            limit = 30

        history = get_player_match_history_from_matches(target.id, limit=limit)
        if not history:
            await ctx.send(f"❌ Nenhuma partida OCR encontrada para **{target.display_name}**.")
            return

        total = stats["matches"] if (stats := get_player_match_stats_from_matches(target.id)) else len(history)

        lines = []
        for r in history:
            icon = "✅" if r["result"] == "win" else "❌"
            hero = (r["hero"] or "?").ljust(18)
            k, d, a = r.get("kills"), r.get("deaths"), r.get("assists")
            kda  = f"{k}/{d}/{a}" if k is not None and d is not None and a is not None else "?/?/?"
            date = ""
            if r.get("created_at"):
                try:
                    from datetime import datetime as _dt
                    date = _dt.fromisoformat(r["created_at"]).strftime("%d/%m")
                except Exception:
                    pass
            lines.append(f"{icon} #{str(r['league_match_id']).ljust(4)} {hero} {kda.ljust(9)} {date}")

        header = (
            f"📜 **Partidas de {target.display_name}** — {total} no total "
            f"| mostrando últimas {len(history)} | `!id <número>` para detalhes"
        )

        chunk_size = 1800
        text = "\n".join(lines)
        chunks = [text[i:i+chunk_size] for i in range(0, len(text), chunk_size)]

        await ctx.send(header)
        for chunk in chunks:
            await ctx.send(f"```\n{chunk}\n```")

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

        sanitized_text = sanitize_code_block(metadata_text)
        chunks = split_chunks(sanitized_text)

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

        metadata_text = json.dumps(metadata, ensure_ascii=False, indent=2)
        sanitized_text = sanitize_code_block(metadata_text)
        chunks = split_chunks(sanitized_text)

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
            await ctx.send(
                "❌ Time inválido. Use `radiant` ou `dire`.",
                delete_after=15
            )
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

        for name, discord_id in resolved.items():
            player_mapping[name] = {"discord_id": discord_id}
            add_player_alias(discord_id, name)

        try:
            league_match_id = insert_ocr_match(job_id, player_mapping, ctx.author.id, ctx.author.display_name)
            delete_match_screenshot(job_id)
        except Exception as exc:
            await ctx.send(f"❌ Falha ao importar job {job_id}: {exc}", delete_after=600)
            return

        duration = duration.strip()
        if duration and re.match(r"^\d{1,2}:\d{2}$", duration):
            update_league_match_duration(league_match_id, duration)

        await ctx.message.delete()
        await ctx.send(f"✅ Partida **#{league_match_id}** registrada com sucesso.")

    @bot.command(name="importarimagem", aliases=["importimage", "ocrimport"])
    async def cmd_import_image(ctx: commands.Context, job_id: int | None = None, *, mapping_text: str | None = None):
        if job_id is None or not mapping_text:
            await ctx.send(
                "❌ Uso incorreto. Exemplo: `!importarimagem 123 1=@123456789012345678`\n" \
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
                "\nUse `!importarimagem {job_id} <mapeamento>` com nomes oficiais, por exemplo `hero=Rubick`.",
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

    @bot.command(name="id")
    async def cmd_lookup_match(ctx: commands.Context, league_match_id: int):
        match = get_match_by_league_id(league_match_id)
        if not match:
            await ctx.send(f"❌ Partida `{league_match_id}` não encontrada.", delete_after=10)
            return

        info = match.get("match_info", {})
        score = info.get("score") or {}
        radiant_score = score.get("radiant")
        dire_score = score.get("dire")
        date = info.get("datetime") or info.get("match_date") or "desconhecida"
        duration = info.get("duration") or "desconhecida"
        winner = info.get("winner_team") or info.get("winner") or "desconhecido"

        lines = [
            f"🏆 Match ID: `{league_match_id}`",
            f"🧾 Hash: `{match.get('match_hash')}`",
            f"🔗 External ID: `{match.get('external_match_id') or 'nenhum'}`",
            f"⏱️ Duração: {duration}",
            f"📅 Data: {date}",
            f"🎯 Vencedor: {winner.title() if isinstance(winner, str) else winner}",
            f"📊 Placar: {radiant_score or 0} x {dire_score or 0}",
            "",
            "👥 Jogadores:",
        ]

        players = match.get("players_data") or []
        for player in players:
            kills = player.get("kills")
            deaths = player.get("deaths")
            assists = player.get("assists")

            discord_id = player.get("discord_id")
            if discord_id:
                user = ctx.guild.get_member(int(discord_id)) or bot.get_user(int(discord_id))
                mention = f" (@{user.name})" if user else f" (<@{discord_id}>)"
            else:
                mention = ""

            lines.append(
                f"{player.get('slot')}. {player.get('player_name') or 'desconhecido'}{mention} "
                f"({player.get('hero_name') or 'herói desconhecido'}) - {player.get('team') or 'time desconhecido'} "
                f"- KDA {kills if kills is not None else '?'} / {deaths if deaths is not None else '?'} / {assists if assists is not None else '?'} "
                f"- NW {player.get('networth') if player.get('networth') is not None else 'N/A'}"
            )

        await ctx.send(f"```\n{chr(10).join(lines)}\n```")

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
        if not re.match(r"^\d{1,2}:\d{2}$", duration):
            await ctx.send(
                "❌ Formato inválido. Use `MM:SS`, por exemplo: `!ocrtime 1 36:55`",
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

    @bot.command(name="cadastro")
    async def cmd_cadastro(ctx: commands.Context, nick: str, member: discord.Member = None):
        if not is_admin(ctx.author.id):
            await ctx.message.delete()
            await ctx.send("❌ Apenas administradores.", delete_after=5)
            return

        if member is None:
            await ctx.message.delete()
            await ctx.send(
                f"❌ Informe o usuário Discord.\n"
                f"→ `!cadastro {nick} @usuario`",
                delete_after=600
            )
            return

        existing = get_player(member.id)
        if existing:
            upsert_player(member.id, existing["display_name"], existing["wins"], existing["losses"])
        else:
            upsert_player(member.id, member.display_name, 0, 0)

        add_player_alias(member.id, nick)
        log_action(
            ctx.author.id, ctx.author.display_name,
            "!cadastro",
            f"discord_id={member.id} nick={nick}",
            affected_ids=[member.id]
        )

        await ctx.message.delete()
        await ctx.send(f"✅ `{nick}` cadastrado como nick de {member.mention}.", delete_after=600)

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

    @bot.command(name="apagarid", aliases=["deleteid", "delmatch", "apagarmatch"])
    async def cmd_delete_match_by_id(ctx: commands.Context, league_match_id: int):
        if not is_admin(ctx.author.id):
            await ctx.message.delete()
            await ctx.send("❌ Apenas administradores.", delete_after=5)
            return

        if count_match_deletions_today(ctx.author.id) >= 1:
            await ctx.send("❌ Você já apagou uma partida hoje. Limite: 1 por dia.", delete_after=60)
            return

        created_at = get_match_created_at(league_match_id)
        if created_at is None:
            await ctx.send(f"❌ Partida `#{league_match_id}` não encontrada.", delete_after=30)
            return

        from datetime import datetime, timedelta
        try:
            match_dt = datetime.fromisoformat(created_at)
        except ValueError:
            match_dt = None

        if match_dt is None or datetime.now() - match_dt > timedelta(hours=24):
            await ctx.send(
                f"❌ Partida `#{league_match_id}` foi adicionada há mais de 24h e não pode ser apagada.",
                delete_after=60
            )
            return

        delete_league_match(league_match_id)
        log_action(
            ctx.author.id, ctx.author.display_name,
            "!apagarmatch",
            f"Apagou partida league_match_id={league_match_id} (criada em {created_at})",
            affected_ids=[league_match_id]
        )
        await ctx.message.delete()
        await ctx.send(f"🗑️ Partida `#{league_match_id}` apagada com sucesso.")

    @bot.command(name="tabela1", aliases=["tabelamanual"])
    async def cmd_tabela1(ctx: commands.Context):
        ranking = get_ranking()

        if not ranking:
            await ctx.send("📋 Nenhum jogador registrado ainda.")
            return

        embed = discord.Embed(title="🏆 Tabela do Campeonato (manual)", color=discord.Color.gold())

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

    @bot.command(name="jogadoresfaltando", aliases=["missingplayers", "faltando"])
    async def cmd_jogadores_faltando(ctx: commands.Context):
        if not is_admin(ctx.author.id):
            await ctx.message.delete()
            await ctx.send("❌ Apenas administradores.", delete_after=5)
            return

        missing = find_unregistered_match_players()
        if not missing:
            await ctx.send("✅ Todos os jogadores das partidas importadas estão registrados.")
            return

        lines = [f"⚠️ **{len(missing)} jogador(es) em partidas mas sem registro na tabela `players`:**\n"]
        for m in missing:
            lines.append(
                f"• `{m['player_name']}` — discord_id: `{m['discord_id']}` — {m['partidas']} partida(s)\n"
                f"  → `!registrar <@{m['discord_id']}> 0 0` para registrar"
            )
        await ctx.send("\n".join(lines))

    @bot.command(name="fixkda", aliases=["corrigirkda"])
    async def cmd_fix_kda(ctx: commands.Context, confirm: str = ""):
        if not is_admin(ctx.author.id):
            await ctx.message.delete()
            await ctx.send("❌ Apenas administradores.", delete_after=5)
            return

        do_fix = confirm.lower() in {"sim", "yes", "ok", "fix"}
        result = diagnose_and_fix_kda_data(fix=do_fix)
        bad_rows = result["bad_rows"]

        if not bad_rows:
            await ctx.send("✅ Nenhum valor inválido de KDA encontrado nas partidas.")
            return

        lines = [f"⚠️ Encontrados **{len(bad_rows)}** registros com KDA inválido:"]
        for r in bad_rows[:20]:
            lines.append(
                f"• Partida {r['league_match_id']}, slot {r['slot']}, `{r['player_name']}` → "
                f"kills={r['kills']}({r['kills_type']}), deaths={r['deaths']}({r['deaths_type']}), assists={r['assists']}({r['assists_type']})"
            )
        if len(bad_rows) > 20:
            lines.append(f"... e mais {len(bad_rows) - 20} registros.")

        if do_fix:
            lines.append("\n✅ **Valores corrigidos para 0.**")
        else:
            lines.append("\n💡 Use `!fixkda sim` para corrigir os valores para 0.")

        await ctx.send("\n".join(lines))

    @bot.command(name="diagtabela2", aliases=["debugtabela2", "diagtab2"])
    async def cmd_diag_tabela2(ctx: commands.Context):
        if not is_admin(ctx.author.id):
            await ctx.message.delete()
            await ctx.send("❌ Apenas administradores.", delete_after=5)
            return

        import traceback as _tb
        try:
            ranking = get_ranking_from_matches()
            await ctx.send(f"✅ `get_ranking_from_matches()` OK — {len(ranking)} jogadores sem erro.")
        except Exception as exc:
            tb_text = _tb.format_exc()
            short_tb = tb_text[-1800:] if len(tb_text) > 1800 else tb_text
            await ctx.send(
                f"❌ Erro em `get_ranking_from_matches()`:\n"
                f"```\n{type(exc).__name__}: {exc}\n```\n"
                f"```\n{short_tb}\n```"
            )

    @bot.command(name="tabela", aliases=["tabela2"])
    async def cmd_tabela(ctx: commands.Context):
        ranking = get_ranking_from_matches()

        if not ranking:
            await ctx.send("📋 Nenhum jogador registrado ainda nas partidas importadas.")
            return

        streaks = get_streak_highlights_from_matches()
        cur_win_id  = streaks["current_win"]["discord_id"]
        cur_loss_id = streaks["current_loss"]["discord_id"]

        embed = discord.Embed(title="🏆 Tabela do Campeonato", color=discord.Color.dark_gold())

        linhas = []
        for i, p in enumerate(ranking):
            prefix = "👑" if i == 0 else "🥈" if i == 1 else "🥉" if i == 2 else f"{i+1}."
            streak_tag = ""
            if p["discord_id"] == cur_win_id and streaks["current_win"]["count"] >= 2:
                streak_tag = f" 🔥×{streaks['current_win']['count']}"
            elif p["discord_id"] == cur_loss_id and streaks["current_loss"]["count"] >= 2:
                streak_tag = f" 💀×{streaks['current_loss']['count']}"
            linhas.append(
                f"{prefix} **{p['display_name']}**{streak_tag} — "
                f"{p['points']} pts "
                f"(`{p['wins']}V / {p['losses']}D` — {p['games']} jogos)"
            )

        embed.description = "\n".join(linhas)

        rec_w = streaks["record_win"]
        rec_l = streaks["record_loss"]
        records_lines = []
        if rec_w["display_name"]:
            records_lines.append(f"🏅 Recorde winstreak: **{rec_w['display_name']}** ({rec_w['count']} seguidas)")
        if rec_l["display_name"]:
            records_lines.append(f"💔 Recorde lossstreak: **{rec_l['display_name']}** ({rec_l['count']} seguidas)")
        if records_lines:
            embed.add_field(name="Recordes", value="\n".join(records_lines), inline=False)

        last = get_last_ocr_match_info()
        if last:
            try:
                from datetime import datetime as _dt
                ts = _dt.fromisoformat(last["created_at"])
                last_text = f"📌 Última partida: #{last['league_match_id']} • {format_brazil_time(ts.isoformat())}"
            except Exception:
                last_text = f"📌 Última partida: #{last['league_match_id']}"
        else:
            last_text = "🕹️ Nenhuma partida importada ainda"

        embed.set_footer(text=f"⚖️ Vitória +3 pts | Derrota -1 pt\n{last_text}")

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