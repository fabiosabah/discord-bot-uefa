# -*- coding: utf-8 -*-
import discord
import logging
import json
import re
from discord.ext import commands
from core.config import ADMIN_IDS
from core.database import (
    upsert_player, add_win, add_loss, remove_win, remove_loss, delete_player,
    get_ranking, log_action, log_match_action, get_last_admin_action, delete_audit_log_entry,
    get_player, get_last_update, get_player_streak, get_player_match_history,
    get_match_summary, get_recent_match_summaries, get_player_top_opponents,
    get_raw_match_audit_events, create_or_replace_manual_match, rebuild_match_history
)
from core.utils.time import format_brazil_time, relative_time

audit_logger = logging.getLogger("Audit")

def is_admin(user_id: int) -> bool:
    return user_id in ADMIN_IDS

def points(wins: int, losses: int) -> int:
    return wins * 3 - losses

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

    @bot.command(name="rebuildhistory", aliases=["rebuildmatches", "repairhistory"])
    async def cmd_rebuild_history(ctx: commands.Context):
        if not is_admin(ctx.author.id):
            await ctx.message.delete()
            await ctx.send("❌ Apenas administradores.", delete_after=5)
            return

        rebuild_match_history()
        await ctx.message.delete()
        await ctx.send("✅ Histórico de partidas reconstruído a partir do audit_log.")

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