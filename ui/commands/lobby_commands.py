# -*- coding: utf-8 -*-
import discord
import logging
from discord.ext import commands
from domain.models import LobbySession
from ui.views.lobby_view import LobbyView
from services.state import get_next_id
from core.config import ADMIN_IDS

logger = logging.getLogger("LobbyCommands")

def setup_lobby_commands(bot: commands.Bot, active_lobbies: dict):
    
    @bot.command(name="lista", aliases=["lobby", "inhouse"])
    async def open_list(ctx: commands.Context):
        session_id = get_next_id()
        
        session = LobbySession(host=ctx.author, session_id=session_id)
        logger.info(f"[Comando] 🆕 NOVA LISTA CRIADA | ID: #{session.id} | Host: {ctx.author.name}#{ctx.author.id}")
        
        view = LobbyView(session, active_lobbies)

        msg = await ctx.send(embed=session.build_embed(), view=view)
        session.message = msg
        active_lobbies[msg.id] = session

        await ctx.message.delete()

    @bot.command(name="uefa", aliases=["liga", "comandos"])
    async def help_command(ctx: commands.Context):
        """Exibe a lista de comandos administrativos e informações da liga."""
        
        embed = discord.Embed(
            title="📖 Guia de Comandos - UEFA Fumos League",
            description="Aqui estão os comandos disponíveis para gerenciar a liga e as listas.",
            color=discord.Color.blue()
        )

        embed.add_field(
            name="🎮 Comandos de Jogador",
            value=(
                "`!lista` ou `!lobby`: Abre uma nova lista de presença.\n"
                "`!tabela`: Mostra o ranking atual da liga.\n"
                "`!perfil @usuario`: Mostra as estatísticas de um jogador, viorias e derrotas por enquanto.\n"
                "`!uefa` ou `!liga`: Abre este guia de ajuda."
            ),
            inline=False
        )

        # Comandos Administrativos
        embed.add_field(
            name="🛠️ Comandos Administrativos (Apenas ADMs)",
            value=(
                "`!venceu @u1 @u2...`: Adiciona 1 vitória (+3 pts) para os jogadores.\n"
                "`!perdeu @u1 @u2...`: Adiciona 1 derrota (-1 pt) para os jogadores.\n"
                "`!registrar @u <V> <D>`: Define manualmente o score de um jogador.\n"
                "`!desfazer` ou `!undo`: Desfaz sua última ação de vitória/derrota.\n"
                "**Botões da Lista:** ADMs podem adicionar/remover pessoas e encerrar qualquer lista."
            ),
            inline=False
        )

        admin_mentions = [f"<@{admin_id}>" for admin_id in ADMIN_IDS]
        admins_text = ", ".join(admin_mentions) if admin_mentions else "Nenhum administrador configurado no .env"
        
        embed.add_field(
            name="👑 Administradores da Liga",
            value=f"Os seguintes usuários têm permissão administrativa:\n{admins_text}",
            inline=False
        )

        embed.set_footer(text="Dúvidas? Entre em contato com um administrador.")
        
        await ctx.send(embed=embed)
