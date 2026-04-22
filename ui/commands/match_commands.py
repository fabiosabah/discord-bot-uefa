# -*- coding: utf-8 -*-
import json
import logging
import re
from datetime import datetime, timedelta

import discord
from discord.ext import commands

from core.db.audit_repo import (
    log_action,
    get_last_admin_action,
    delete_audit_log_entry,
    get_raw_match_audit_events,
    count_match_deletions_today,
)
from core.db.match_repo import (
    delete_match_history,
    get_match_by_league_id,
    create_or_replace_manual_match,
    delete_league_match,
    get_match_created_at,
)
from core.db.player_repo import (
    upsert_player,
    add_win,
    add_loss,
    remove_win,
    remove_loss,
    delete_player,
    get_player,
    get_ranking,
)
from ui.commands.score_helpers import (
    is_admin,
    points,
    build_footer,
    _resolve_command_members,
    UndoConfirmView,
)

audit_logger = logging.getLogger("Audit")


def setup_match_commands(bot: commands.Bot):
    logger = logging.getLogger("MatchCommands")
    logger.info("Carregando comandos de partidas...")

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
        wins   = [int(m) for m in re.findall(r"<@!?(\d+)>", winners_text)]
        losses = [int(m) for m in re.findall(r"<@!?(\d+)>", losers_text)]

        if not wins and not losses:
            await ctx.send("❌ Informe ao menos um vencedor ou um derrotado.", delete_after=15)
            return

        create_or_replace_manual_match(match_id, wins, losses, ctx.author.id, ctx.author.display_name)
        await ctx.message.delete()
        await ctx.send(f"✅ Match #{match_id:03d} registrado manualmente.")

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
