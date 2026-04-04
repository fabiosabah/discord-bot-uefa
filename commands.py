import discord
import logging
from datetime import datetime
from discord.ext import commands
from models import LobbySession
from views import LobbyView

logger = logging.getLogger(__name__)

session_counter = 0
last_reset_date = datetime.now().date()

# ─────────────────────────────────────────────
#  Comandos
# ─────────────────────────────────────────────
def setup_commands(bot: commands.Bot, active_lobbies: dict):
    @bot.command(name="lista", aliases=["lobby", "inhouse"])
    async def open_list(ctx: commands.Context):
        global session_counter, last_reset_date
        
        current_date = datetime.now().date()
        if current_date > last_reset_date:
            logger.info(f"[Sistema] 📅 Novo dia detectado ({current_date}). Resetando contador de IDs.")
            session_counter = 0
            last_reset_date = current_date
            
        session_counter += 1
        
        session = LobbySession(host=ctx.author, session_id=session_counter)
        logger.info(f"[Comando] 🆕 NOVA LISTA CRIADA | ID: #{session.id} | Host: {ctx.author.name}#{ctx.author.id}")
        
        view = LobbyView(session)

        msg = await ctx.send(embed=session.build_embed(), view=view)
        session.message = msg   # salva referência na sessão
        active_lobbies[msg.id] = session

        await ctx.message.delete()
