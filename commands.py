import discord
import logging
from discord.ext import commands
from models import LobbySession
from views import LobbyView

logger = logging.getLogger(__name__)


# ─────────────────────────────────────────────
#  Comandos
# ─────────────────────────────────────────────
def setup_commands(bot: commands.Bot, active_lobbies: dict):
    @bot.command(name="lista", aliases=["lobby", "inhouse"])
    async def open_list(ctx: commands.Context):
        session = LobbySession(host=ctx.author)
        logger.info(f"[Comando] 🆕 NOVA LISTA CRIADA | ID: {session.id} | Host: {ctx.author.name}#{ctx.author.id}")
        
        view = LobbyView(session)

        msg = await ctx.send(embed=session.build_embed(), view=view)
        session.message = msg   # salva referência na sessão
        active_lobbies[msg.id] = session

        await ctx.message.delete()
