# -*- coding: utf-8 -*-
import discord
import logging
import sys
from discord.ext import commands
from core.config import TOKEN
from core.database import init_db
from ui.commands.lobby_commands import setup_lobby_commands
from ui.commands.score_commands import setup_score_commands

# ─────────────────────────────────────────────
# Configuração de Logging (Railway stdout)
# ─────────────────────────────────────────────
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s [%(levelname)s] %(name)s: %(message)s',
    handlers=[
        logging.StreamHandler(sys.stdout)
    ]
)
logger = logging.getLogger("BotCore")

# ─────────────────────────────────────────────
# Configuração do bot
# ─────────────────────────────────────────────

intents = discord.Intents.default()
intents.message_content = True
intents.members = True

bot = commands.Bot(command_prefix="!", intents=intents, help_command=None)

# Dicionário global de lobbies ativos
active_lobbies = {}

@bot.event
async def on_ready():
    init_db()
    logger.info(f"✅ Bot conectado como {bot.user} (ID: {bot.user.id})")
    print("-" * 40)
    for guild in bot.guilds:
        logger.info(f"Servidor: {guild.name} | Membros: {len(guild.members)}")

# Setup dos comandos
logger.info("Configurando comandos...")
setup_lobby_commands(bot, active_lobbies)
setup_score_commands(bot)
logger.info("Comandos configurados com sucesso.")

if __name__ == "__main__":
    bot.run(TOKEN)
