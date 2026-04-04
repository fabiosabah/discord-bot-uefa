import os
from dotenv import load_dotenv

# ─────────────────────────────────────────────
#  Configuração
# ─────────────────────────────────────────────
load_dotenv()

TOKEN = os.getenv("DISCORD_TOKEN")
MAX_PLAYERS = 10
LEAGUE_NAME = os.getenv("LEAGUE_NAME")
LEAGUE_EMOJI = os.getenv("LEAGUE_EMOJI")

admin_ids_raw = os.getenv("ADMIN_IDS", "")
ADMIN_IDS = [int(id.strip()) for id in admin_ids_raw.split(",") if id.strip().isdigit()]

def is_admin(user_id: int) -> bool:
    """Verifica se o ID do usuário está na lista de administradores."""
    return user_id in ADMIN_IDS