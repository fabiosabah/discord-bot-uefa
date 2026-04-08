# -*- coding: utf-8 -*-
import os
from dotenv import load_dotenv

# ─────────────────────────────────────────────
#  Configuração Global
# ─────────────────────────────────────────────
load_dotenv()

TOKEN = os.getenv("DISCORD_TOKEN")
MAX_PLAYERS = 10

# Configurações da Liga
LEAGUE_NAME = os.getenv("LEAGUE_NAME", "UEFA Fumos League")
LEAGUE_EMOJI = os.getenv("LEAGUE_EMOJI", "🎮")

# Caminho do Banco de Dados (Railway Volume)
DB_PATH = os.getenv("DB_PATH", "data/database.db")

# Lista de IDs de administradores (separados por vírgula no .env)
admin_ids_raw = os.getenv("ADMIN_IDS", "")
ADMIN_IDS = [int(id.strip()) for id in admin_ids_raw.split(",") if id.strip().isdigit()]

def is_admin(user_id: int) -> bool:
    """Verifica se o ID do usuário está na lista de administradores."""
    return user_id in ADMIN_IDS
