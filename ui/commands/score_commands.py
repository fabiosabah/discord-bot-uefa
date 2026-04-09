# -*- coding: utf-8 -*-
import discord
import logging
import json
from discord.ext import commands
from core.config import ADMIN_IDS
from core.database import (
    upsert_player, add_win, add_loss, remove_win, remove_loss, 
    get_ranking, log_action, get_last_admin_action, delete_audit_log_entry, get_player, get_last_update
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

        nomes, ids = [], []
        for m in members:
            add_win(m.id, m.display_name)
            nomes.append(m.display_name)
            ids.append(m.id)

        log_action(ctx.author.id, ctx.author.display_name, "!venceu",
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

        nomes, ids = [], []
        for m in members:
            add_loss(m.id, m.display_name)
            nomes.append(m.display_name)
            ids.append(m.id)

        log_action(ctx.author.id, ctx.author.display_name, "!perdeu",
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

        title = f"📊 Perfil de {target.display_name}"
        if pos == 1:
            title = f"👑 Líder da Liga: {target.display_name}"

        embed = discord.Embed(title=title, description=msg, color=color)
        embed.set_thumbnail(url=target.display_avatar.url)

        embed.add_field(name="🏆 Vitórias", value=wins, inline=True)
        embed.add_field(name="💀 Derrotas", value=losses, inline=True)
        embed.add_field(name="🎯 Pontos", value=pts, inline=True)

        embed.add_field(name="🎮 Jogos", value=games, inline=True)
        embed.add_field(name="📈 Winrate", value=f"{winrate:.1f}%", inline=True)

        if pos:
            embed.add_field(name="🥇 Ranking", value=f"#{pos}", inline=True)

        embed.set_footer(text="Sistema de Liga • UEFA Bot")

        await ctx.send(embed=embed)

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