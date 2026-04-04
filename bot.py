import discord
from discord.ext import commands
from config import TOKEN
from commands import setup_commands

# ---------------------------------------------
#  Configuração do bot
# ---------------------------------------------
intents = discord.Intents.default()
intents.message_content = True
intents.members = True

bot = commands.Bot(command_prefix="!", intents=intents)

active_lobbies: dict[int, "LobbySession"] = {}


# ---------------------------------------------
#  Eventos
# ---------------------------------------------
@bot.event
async def on_ready():
    print(f"? Bot conectado como {bot.user} (ID: {bot.user.id})")
    print("-" * 40)


# ---------------------------------------------
#  Setup
# ---------------------------------------------
setup_commands(bot, active_lobbies)


# ---------------------------------------------
#  Iniciar bot
# ---------------------------------------------
if __name__ == "__main__":
    bot.run(TOKEN)
