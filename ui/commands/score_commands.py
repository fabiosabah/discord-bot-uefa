# -*- coding: utf-8 -*-
import discord
import logging
import json
from discord.ext import commands
from core.config import ADMIN_IDS
from core.database import (
    upsert_player, add_win, add_loss, remove_win, remove_loss, 
    get_ranking, log_action, get_last_admin_action, delete_audit_log_entry
)

# Logger específico para auditoria de ações
audit_logger = logging.getLogger("Audit")

def is_admin(user_id: int) -> bool:
    return user_id in ADMIN_IDS

def points(wins: int, losses: int) -> int:
    return wins * 3 - losses

def setup_score_commands(bot: commands.Bot):
    logger = logging.getLogger("ScoreSetup")
    logger.info("Carregando comandos de score...")

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

        audit_logger.info(f"[REGISTRO] ADM {ctx.author.name} ({ctx.author.id}) REGISTROU {member.display_name} ({member.id}) com W:{wins} L:{losses}")

        log_action(
            ctx.author.id, ctx.author.display_name,
            "!registrar",
            f"{member.display_name} ({member.id}) → W:{wins} L:{losses} Pts:{pts}",
            affected_ids=[member.id]
        )

        await ctx.message.delete()
        await ctx.send(
            f"✅ **{member.display_name}** atualizado: "
            f"`{wins}V / {losses}D` → **{pts} pts**"
        )

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
        ids = []
        for m in members:
            add_win(m.id, m.display_name)
            nomes.append(f"{m.name} ({m.id})")
            ids.append(m.id)

        audit_logger.info(f"[VITÓRIA] ADM {ctx.author.name} ({ctx.author.id}) ADICIONOU VITÓRIA para: {', '.join(nomes)}")

        log_action(
            ctx.author.id, ctx.author.display_name,
            "!venceu",
            f"Vitória registrada para: {', '.join(nomes)}",
            affected_ids=ids
        )

        await ctx.message.delete()
        mencoes = " ".join(m.mention for m in members)
        await ctx.send(f"🏆 Vitória registrada para {mencoes}! **(+3 pts cada)**")

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
        ids = []
        for m in members:
            add_loss(m.id, m.display_name)
            nomes.append(f"{m.name} ({m.id})")
            ids.append(m.id)

        audit_logger.info(f"[DERROTA] ADM {ctx.author.name} ({ctx.author.id}) ADICIONOU DERROTA para: {', '.join(nomes)}")

        log_action(
            ctx.author.id, ctx.author.display_name,
            "!perdeu",
            f"Derrota registrada para: {', '.join(nomes)}",
            affected_ids=ids
        )

        await ctx.message.delete()
        mencoes = " ".join(m.mention for m in members)
        await ctx.send(f"💀 Derrota registrada para {mencoes}. **(-1 pt cada)**")

    @bot.command(name="desfazer", aliases=["undo", "z"])
    async def cmd_desfazer(ctx: commands.Context):
        """Desfaz a última ação de vitória ou derrota do administrador."""
        if not is_admin(ctx.author.id):
            await ctx.message.delete()
            await ctx.send("❌ Apenas administradores podem usar esse comando.", delete_after=5)
            return

        last_action = get_last_admin_action(ctx.author.id)
        if not last_action:
            await ctx.send("⚠️ Nenhuma ação reversível encontrada para você.", delete_after=5)
            return

        affected_ids = json.loads(last_action['affected_ids']) if last_action['affected_ids'] else []
        if not affected_ids:
            await ctx.send("⚠️ Não há jogadores afetados para reverter nesta ação.", delete_after=5)
            return

        command = last_action['command']
        
        # Reverter a lógica
        for discord_id in affected_ids:
            if command == "!venceu":
                remove_win(discord_id)
            elif command == "!perdeu":
                remove_loss(discord_id)

        # Remover do log para não desfazer a mesma coisa duas vezes
        delete_audit_log_entry(last_action['id'])

        audit_logger.info(f"[UNDO] ADM {ctx.author.name} ({ctx.author.id}) DESFEZ a ação '{command}' que afetou {len(affected_ids)} jogadores.")
        
        await ctx.message.delete()
        await ctx.send(f"↩️ **Ação desfeita!** A última operação de `{command}` foi revertida para {len(affected_ids)} jogador(es).")

    @bot.command(name="perfil", aliases=["p"])
    async def cmd_perfil(ctx: commands.Context, member: discord.Member = None):
        """Exibe as estatísticas detalhadas de um jogador."""
        target = member or ctx.author
        player = get_player(target.id)

        if not player:
            await ctx.send(f"📋 **{target.display_name}** ainda não possui registros na liga.")
            return

        pts = points(player['wins'], player['losses'])
        total_games = player['wins'] + player['losses']
        wr = (player['wins'] / total_games * 100) if total_games > 0 else 0

        embed = discord.Embed(
            title=f"📊 Perfil de Jogador — {target.display_name}",
            color=discord.Color.blue()
        )
        embed.set_thumbnail(url=target.display_avatar.url)
        
        embed.add_field(name="🏆 Pontuação", value=f"**{pts} pts**", inline=True)
        embed.add_field(name="🎮 Jogos", value=f"{total_games}", inline=True)
        embed.add_field(name="📈 Win Rate", value=f"{wr:.1f}%", inline=True)
        
        embed.add_field(name="✅ Vitórias", value=f"{player['wins']}", inline=True)
        embed.add_field(name="💀 Derrotas", value=f"{player['losses']}", inline=True)
        embed.add_field(name="\u200b", value="\u200b", inline=True) # Spacer

        embed.set_footer(text=f"ID: {target.id} | Última atualização: {player['updated_at'][:10]}")
        
        await ctx.send(embed=embed)

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
            if i == 0: prefix = "👑"
            elif i == 1: prefix = "🥈"
            elif i == 2: prefix = "🥉"
            else: prefix = f"`{i+1:02d}.`"

            linhas.append(
                f"{prefix} **{p['display_name']}** — "
                f"{p['points']} pts "
                f"(`{p['wins']}V / {p['losses']}D` — {p['games']} jogos)"
            )

        embed.description = "\n".join(linhas)
        embed.set_footer(text="Pontuação: vitória +3 pts | derrota -1 pt | desempate por mais vitórias")
        await ctx.send(embed=embed)
