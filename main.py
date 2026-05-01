# -*- coding: utf-8 -*-
import sys
import os

# Expõe discord/ como raiz de pacotes (core/, domain/, services/, ui/, etc.)
sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), "discord"))

from bot import bot
from core.config import TOKEN

if __name__ == "__main__":
    bot.run(TOKEN)
