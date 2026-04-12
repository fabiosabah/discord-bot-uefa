# -*- coding: utf-8 -*-
import discord
import logging
import sys
from discord.ext import commands
from core.config import TOKEN
from core.database import init_db, migrate_db, get_list_channel
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


intents = discord.Intents.default()
intents.message_content = True
intents.members = True

bot = commands.Bot(command_prefix="!", intents=intents, help_command=None)

active_lobbies = {}

@bot.event
async def on_ready():
    init_db()
    migrate_db() # armengue provisorio.
    logger.info(f"✅ Bot conectado como {bot.user} (ID: {bot.user.id})")
    print("-" * 40)
    for guild in bot.guilds:
        logger.info(f"Servidor: {guild.name} | Membros: {len(guild.members)}")

@bot.event
async def on_message(message: discord.Message):
    if message.author.bot:
        return

    if not message.content.startswith(bot.command_prefix):
        return

    if message.guild:
        allowed_channel = get_list_channel(message.guild.id)
        if allowed_channel and message.channel.id == allowed_channel:
            content = message.content.strip()
            command_name = content.split()[0][1:].lower() if content else ""
            allowed = command_name in {"lista", "lobby", "inhouse"}
            if not allowed:
                await message.channel.send(
                    "❌ Neste canal só é permitido usar `!lista` para abrir a lista.",
                    delete_after=10
                )
                return

    await bot.process_commands(message)

logger.info("Configurando comandos...")
setup_lobby_commands(bot, active_lobbies)
setup_score_commands(bot)
logger.info("Comandos configurados com sucesso.")

if __name__ == "__main__":
    bot.run(TOKEN)
