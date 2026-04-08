# -*- coding: utf-8 -*-
import discord
from discord.ext import commands
from config import TOKEN
from commands import setup_commands
from score_commands import setup_score_commands
from database import init_db

# ─────────────────────────────────────────────
# Configuração do bot
# ─────────────────────────────────────────────

intents = discord.Intents.default()
intents.message_content = True
intents.members = True

bot = commands.Bot(command_prefix="!", intents=intents)

active_lobbies = {}

@bot.event
async def on_ready():
    init_db()
    print(f"✅ Bot conectado como {bot.user} (ID: {bot.user.id})")
    print("-" * 40)

setup_commands(bot, active_lobbies)
setup_score_commands(bot)

if __name__ == "__main__":
    bot.run(TOKEN)