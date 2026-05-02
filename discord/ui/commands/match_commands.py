# -*- coding: utf-8 -*-
import logging
import requests
from datetime import datetime, timedelta

import discord
from discord.ext import commands

from core.config import GC_API_URL
from core.db.audit_repo import (
    log_action,
    get_raw_match_audit_events,
    count_match_deletions_today,
)
from core.db.match_repo import (
    get_match_by_season_match_id,
    delete_league_match,
)
from ui.commands.score_helpers import is_admin

audit_logger = logging.getLogger("Audit")


def setup_match_commands(bot: commands.Bot):
    logger = logging.getLogger("MatchCommands")
    logger.info("Carregando comandos de partidas...")

    @bot.command(name="debugpartidas", aliases=["debugmatches", "auditmatches"])
    async def cmd_debug_partidas(ctx: commands.Context, limit: int = 30):
        if not is_admin(ctx.author.id):
            await ctx.message.delete()
            await ctx.send("❌ Apenas administradores.", delete_after=5)
            return

        events = get_raw_match_audit_events(limit)
        if not events:
            await ctx.send("📋 Nenhum evento registrado.")
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

    @bot.command(name="apagarid", aliases=["deleteid", "delmatch", "apagarmatch"])
    async def cmd_delete_match_by_id(ctx: commands.Context, match_num: int):
        if not is_admin(ctx.author.id):
            await ctx.message.delete()
            await ctx.send("❌ Apenas administradores.", delete_after=5)
            return

        if count_match_deletions_today(ctx.author.id) >= 1:
            await ctx.send("❌ Você já apagou uma partida hoje. Limite: 1 por dia.", delete_after=60)
            return

        match = get_match_by_season_match_id(match_num)
        if match is None:
            await ctx.send(f"❌ Partida `#{match_num}` não encontrada na temporada atual.", delete_after=30)
            return

        created_at = match["created_at"]
        internal_id = match["league_match_id"]

        try:
            match_dt = datetime.fromisoformat(created_at)
        except ValueError:
            match_dt = None

        if match_dt is None or datetime.now() - match_dt > timedelta(hours=24):
            await ctx.send(
                f"❌ Partida `#{match_num}` foi adicionada há mais de 24h e não pode ser apagada.",
                delete_after=60
            )
            return

        delete_league_match(internal_id)
        log_action(
            ctx.author.id, ctx.author.display_name,
            "!apagarmatch",
            f"Apagou partida #{match_num} (league_match_id={internal_id}, criada em {created_at})",
            affected_ids=[internal_id]
        )
        await ctx.message.delete()
        await ctx.send(f"🗑️ Partida `#{match_num}` apagada com sucesso.")

    @bot.command(name="id")
    async def cmd_lookup_match(ctx: commands.Context, match_num: int):
        match = get_match_by_season_match_id(match_num)
        if not match:
            await ctx.send(f"❌ Partida `#{match_num}` não encontrada na temporada atual.", delete_after=10)
            return

        season_match_id = match.get("season_match_id", match_num)
        season = match.get("season", 1)
        info = match.get("match_info", {})
        score = info.get("score") or {}
        radiant_score = score.get("radiant")
        dire_score = score.get("die")
        date = info.get("datetime") or info.get("match_date") or "desconhecida"
        duration = info.get("duration") or "desconhecida"
        winner = info.get("winner_team") or info.get("winner") or "desconhecido"

        lines = [
            f"🏆 Partida: `#{season_match_id}` (T{season})",
            f"🧾 Hash: `{match.get('match_hash')}`",
            f"🔗 External ID: `{match.get('external_match_id') or 'nenhum'}`",
            f"⏱️ Duração: {duration}",
            f"📅 Data: {date}",
            f"🎯 Vencedor: {winner.title() if isinstance(winner, str) else winner}",
            f"📊 Placar: {radiant_score or 0} x {score.get('dire') or 0}",
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

    @bot.command(name="apikeyid", aliases=["steampartida", "dotamatch"])
    async def cmd_steam_match(ctx: commands.Context, match_id: int):
        if not is_admin(ctx.author.id):
            await ctx.message.delete()
            await ctx.send("❌ Apenas administradores.", delete_after=5)
            return

        if not GC_API_URL:
            await ctx.send("❌ `GC_API_URL` não configurada.", delete_after=10)
            return

        await ctx.send(f"🔍 Buscando partida `{match_id}`...", delete_after=10)

        try:
            resp = requests.get(f"{GC_API_URL}/match/{match_id}", timeout=20)
            data = resp.json()
        except requests.RequestException as e:
            logger.error(f"[SteamAPI] Erro ao chamar GC: {e}")
            await ctx.send(f"❌ Erro ao chamar o serviço GC: `{e}`", delete_after=20)
            return

        if not resp.ok:
            await ctx.send(f"❌ GC retornou erro: `{data.get('error', resp.status_code)}`", delete_after=20)
            return

        result = data.get("result", {})
        if not result:
            await ctx.send("❌ Nenhum dado retornado. Verifique se o match ID é válido.", delete_after=15)
            return

        players = result.get("players", [])
        await ctx.send(
            f"✅ Partida `{match_id}` buscada — veja o log do Railway (serviço GC) para os detalhes completos.\n"
            f"Duração: `{result.get('duration')}s` | Vencedor: `{'Radiant' if result.get('radiant_win') else 'Dire'}` | Jogadores: `{len(players)}`"
        )
