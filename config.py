import os
from dotenv import load_dotenv

# ─────────────────────────────────────────────
#  Configuração
# ─────────────────────────────────────────────
load_dotenv()

TOKEN = os.getenv("DISCORD_TOKEN")
MAX_PLAYERS = 10
