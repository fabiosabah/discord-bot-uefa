# -*- coding: utf-8 -*-
import discord
from discord.ext import commands
from config import TOKEN
from commands import setup_commands

# ---------------------------------------------
#  Configuracao do bot
# ---------------------------------------------
intents = discord.Intents.default()
intents.message_content = True
intents.members = True

bot = commands.Bot(command_prefix="!", intents=intents)

active_lobbies = {}

@bot.event
async def on_ready():
    print(f"✅ Bot conectado como {bot.user} (ID: {bot.user.id})")
    print("-" * 40)


setup_commands(bot, active_lobbies)

if __name__ == "__main__":
    bot.run(TOKEN)
