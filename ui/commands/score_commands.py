# -*- coding: utf-8 -*-
# Setup facade — delegates to focused command modules.
from discord.ext import commands

from ui.commands.player_commands import setup_player_commands
from ui.commands.match_commands import setup_match_commands
from ui.commands.ocr_commands import setup_ocr_commands
from ui.commands.admin_commands import setup_admin_commands


def setup_score_commands(bot: commands.Bot):
    setup_player_commands(bot)
    setup_match_commands(bot)
    setup_ocr_commands(bot)
    setup_admin_commands(bot)
