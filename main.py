# -*- coding: utf-8 -*-
import sys
import os

BASE = os.path.dirname(os.path.abspath(__file__))
# Expõe discord/ como raiz de pacotes (core/, domain/, services/, ui/, etc.)
sys.path.insert(0, os.path.join(BASE, "discord"))
# Expõe gc/ diretamente — import como "from gc_client import ..." para evitar
# conflito com o módulo builtin "gc" do Python (garbage collector)
sys.path.insert(0, os.path.join(BASE, "gc"))

from bot import bot
from core.config import TOKEN

if __name__ == "__main__":
    bot.run(TOKEN)
