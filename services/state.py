# -*- coding: utf-8 -*-
from datetime import datetime
import logging

logger = logging.getLogger("State")

# Estado global compartilhado
session_counter = 0
last_reset_date = datetime.now().date()

def get_next_id() -> int:
    """Gera o próximo ID da sessão, resetando se o dia mudou."""
    global session_counter, last_reset_date
    
    current_date = datetime.now().date()
    if current_date > last_reset_date:
        logger.info(f"[Sistema] 📅 Novo dia detectado ({current_date}). Resetando contador de IDs.")
        session_counter = 0
        last_reset_date = current_date
        
    session_counter += 1
    return session_counter
