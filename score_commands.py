# -*- coding: utf-8 -*-
import discord
import logging
from discord.ext import commands
from config import ADMIN_IDS
from database import upsert_player, add_win, add_loss, get_ranking, log_action

logger = logging.getLogger(__name__)

# ─────────────────────────────────────────────
# Helper
# ─────────────────────────────────────────────

def is_admin(user_id: int) -> bool:
    return user_id in ADMIN_IDS


def points(wins: int, losses: int) -> int:
    return wins * 3 - losses


# ─────────────────────────────────────────────
# Setup dos comandos
# ─────────────────────────────────────────────

def setup_score_commands(bot: commands.Bot):

    # ── !registrar @usuario <vitorias> <derrotas> ──────────────────────────
    @bot.command(name="registrar")
    async def cmd_registrar(ctx: commands.Context, member: discord.User, wins: int, losses: int):
        """Sobrescreve os dados de um jogador. Apenas admins."""
        if not is_admin(ctx.author.id):
            await ctx.message.delete()
            await ctx.send("❌ Apenas administradores podem usar esse comando.", delete_after=5)
            return

        if wins < 0 or losses < 0:
            await ctx.send("❌ Vitórias e derrotas não podem ser negativos.", delete_after=5)
            return

        upsert_player(member.id, member.display_name, wins, losses)
        pts = points(wins, losses)

        log_action(
            ctx.author.id, ctx.author.display_name,
            "!registrar",
            f"{member.display_name} ({member.id}) → W:{wins} L:{losses} Pts:{pts}"
        )

        await ctx.message.delete()
        await ctx.send(
            f"✅ **{member.display_name}** atualizado: "
            f"`{wins}V / {losses}D` → **{pts} pts**"
        )
        logger.info(f"[Registrar] {ctx.author.name} registrou {member.display_name}: W{wins}/L{losses}")

    # ── !venceu @u1 @u2 @u3 @u4 @u5 ──────────────────────────────────────
    @bot.command(name="venceu")
    async def cmd_venceu(ctx: commands.Context, *members: discord.Member):
        """Adiciona 1 vitória (+3 pts) a cada jogador mencionado. Apenas admins."""
        if not is_admin(ctx.author.id):
            await ctx.message.delete()
            await ctx.send("❌ Apenas administradores podem usar esse comando.", delete_after=5)
            return

        if not members:
            await ctx.send("⚠️ Mencione ao menos um jogador.", delete_after=5)
            return

        nomes = []
        for m in members:
            add_win(m.id, m.display_name)
            nomes.append(m.display_name)

        log_action(
            ctx.author.id, ctx.author.display_name,
            "!venceu",
            f"Vitória registrada para: {', '.join(nomes)}"
        )

        await ctx.message.delete()
        mencoes = " ".join(m.mention for m in members)
        await ctx.send(f"🏆 Vitória registrada para {mencoes}! **(+3 pts cada)**")
        logger.info(f"[Venceu] {ctx.author.name} registrou vitória para: {', '.join(nomes)}")

    # ── !perdeu @u1 @u2 @u3 @u4 @u5 ──────────────────────────────────────
    @bot.command(name="perdeu")
    async def cmd_perdeu(ctx: commands.Context, *members: discord.Member):
        """Adiciona 1 derrota (-1 pt) a cada jogador mencionado. Apenas admins."""
        if not is_admin(ctx.author.id):
            await ctx.message.delete()
            await ctx.send("❌ Apenas administradores podem usar esse comando.", delete_after=5)
            return

        if not members:
            await ctx.send("⚠️ Mencione ao menos um jogador.", delete_after=5)
            return

        nomes = []
        for m in members:
            add_loss(m.id, m.display_name)
            nomes.append(m.display_name)

        log_action(
            ctx.author.id, ctx.author.display_name,
            "!perdeu",
            f"Derrota registrada para: {', '.join(nomes)}"
        )

        await ctx.message.delete()
        mencoes = " ".join(m.mention for m in members)
        await ctx.send(f"💀 Derrota registrada para {mencoes}. **(-1 pt cada)**")
        logger.info(f"[Perdeu] {ctx.author.name} registrou derrota para: {', '.join(nomes)}")

    # ── !tabela ────────────────────────────────────────────────────────────
    @bot.command(name="tabela")
    async def cmd_tabela(ctx: commands.Context):
        """Exibe o ranking completo do campeonato."""
        ranking = get_ranking()

        if not ranking:
            await ctx.send("📋 Nenhum jogador registrado ainda.")
            return

        embed = discord.Embed(title="🏆 Tabela do Campeonato", color=discord.Color.gold())

        linhas = []
        for i, p in enumerate(ranking):
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
                f"(`{p['wins']}V / {p['losses']}D` — {p['games']} jogos)"
            )

        embed.description = "\n".join(linhas)
        embed.set_footer(text="Pontuação: vitória +3 pts | derrota -1 pt | desempate por mais vitórias")
        await ctx.send(embed=embed)