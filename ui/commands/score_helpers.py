# -*- coding: utf-8 -*-
import json
import re
from typing import Any

import discord
from discord.ext import commands

from core.config import ADMIN_IDS
from core.db.audit_repo import get_last_update
from core.db.ocr_repo import get_match_screenshot, set_match_screenshot_status
from core.utils.time import format_brazil_time, relative_time


def is_admin(user_id: int) -> bool:
    return user_id in ADMIN_IDS


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


def build_footer(include_rules=True):
    last_update = get_last_update()
    if last_update:
        time = relative_time(last_update)
        brazil_time = format_brazil_time(last_update)
        update_text = f"📌 Última atualização: {time} • {brazil_time}"
    else:
        update_text = "🕹️ Nenhuma partida registrada ainda"
    if include_rules:
        return f"⚖️ Vitória +3 pts | Derrota -1 pt\n{update_text}"
    return update_text


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
    elif team in {"d", "dir"}:
        team_label = "Dire"

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
    from core.ocr import _normalize_team
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
    return str(player.get("player_name") or player.get("name") or player.get("player") or "").strip()


def build_ocr_job_summary_text(job_id: int, parsed: dict[str, Any]) -> str:
    if parsed.get("valid_dota_screenshot") is False:
        return (
            f"⚠️ **Job {job_id}** — imagem não reconhecida como placar de Dota 2.\n"
            f"Use `!detalhesimagem {job_id}` para revisar o JSON ou `!rawtextimagem {job_id}` para o texto bruto."
        )

    match_info = parsed.get("match_info") or parsed.get("game_details") or {}
    score = match_info.get("score") or {}
    radiant_score = score.get("radiant") or parsed.get("radiant_score")
    dire_score    = score.get("dire")    or parsed.get("dire_score")
    winner_team   = _get_winner_team(parsed)
    duration      = match_info.get("duration") or parsed.get("duration")
    mode          = match_info.get("game_mode") or match_info.get("mode") or parsed.get("mode")
    players       = parsed.get("players_data") or parsed.get("players") or []

    lines = [f"📸 **Análise OCR concluída — Job #{job_id}**", ""]

    lines.append(f"📊 Placar: `{radiant_score or 0}` × `{dire_score or 0}`")

    if winner_team:
        lines.append(f"🏆 Vencedor: **{winner_team.title()}**")
    else:
        lines.append(
            f"🏆 Vencedor: ❌ **não detectado** — defina antes de registrar:\n"
            f"   `!setjobwinner {job_id} radiant`  ou  `!setjobwinner {job_id} dire`"
        )

    if duration:
        lines.append(f"⏱️ Duração: `{duration}`")
    else:
        lines.append(
            f"⏱️ Duração: ❌ **não detectada** — informe ao registrar:\n"
            f"   `!ok {job_id} MM:SS`"
        )

    if mode:
        lines.append(f"🎮 Modo: {mode}")

    if not players:
        lines += [
            "",
            "⚠️ Não foi possível extrair a lista de jogadores.",
            f"Use `!detalhesimagem {job_id}` para ver o JSON completo.",
        ]
        return "\n".join(lines)

    lines.extend(["", "👥 **Jogadores detectados:**"])
    for index, player in enumerate(players, start=1):
        lines.append(f"  {_format_ocr_player_line(player, index)}")

    lines.extend([
        "",
        "🛠️ **Correções antes de importar:**",
        f"• Herói errado? → `!ocrhero {job_id} <slot> <herói>`",
        f"• Nick errado? → `!ocrnick {job_id} <slot> <novo nick>`",
        f"• Nick sem conta Discord vinculada? → `!cadastro <nick> @usuario`",
        f"• Vencedor errado? → `!setjobwinner {job_id} radiant|dire`",
    ])

    lines.append("")
    if not winner_team:
        lines.append(f"⛔ **Defina o vencedor antes de registrar** (veja acima).")
    elif not duration:
        lines.append(
            f"✅ **Tudo certo?** Registre informando a duração:\n"
            f"   `!ok {job_id} MM:SS`"
        )
    else:
        lines.append(
            f"✅ **Tudo certo?** Registre com:\n"
            f"   `!ok {job_id}`  — ou  `!ok {job_id} MM:SS` para sobrescrever a duração"
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


