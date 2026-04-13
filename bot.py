# -*- coding: utf-8 -*-
import asyncio
import discord
import logging
import sys
from discord.ext import commands
from core.config import TOKEN, IMAGE_CHANNEL_ID
from core.database import (
    init_db,
    migrate_db,
    get_list_channel,
    get_image_channel,
    enqueue_match_screenshot,
    get_pending_match_screenshots,
    set_match_screenshot_status
)
from core.ocr import can_process_ocr, can_process_llm, process_match_screenshot
from ui.commands.lobby_commands import setup_lobby_commands
from ui.commands.score_commands import setup_score_commands

# ─────────────────────────────────────────────
# Configuração de Logging (Railway stdout)
# ─────────────────────────────────────────────
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s [%(levelname)s] %(name)s: %(message)s',
    handlers=[
        logging.StreamHandler(sys.stdout)
    ]
)
logger = logging.getLogger("BotCore")


intents = discord.Intents.default()
intents.message_content = True
intents.members = True

bot = commands.Bot(command_prefix="!", intents=intents, help_command=None)

active_lobbies = {}

@bot.event
async def on_ready():
    init_db()
    migrate_db()
    logger.info(f"✅ Bot conectado como {bot.user} (ID: {bot.user.id})")
    print("-" * 40)
    for guild in bot.guilds:
        logger.info(f"Servidor: {guild.name} | Membros: {len(guild.members)}")

    if can_process_ocr():
        bot.loop.create_task(ocr_background_worker())
    else:
        logger.warning(
            "OCR desativado: configure OPENAI_API_KEY ou GEMINI_API_KEY."
        )

    if not can_process_llm():
        logger.warning("LLM desativado: configure OPENAI_API_KEY ou GEMINI_API_KEY para usar a interpretação com IA.")

@bot.event
async def on_message(message: discord.Message):
    if message.author.bot:
        return

    image_channel_id = IMAGE_CHANNEL_ID
    if message.guild:
        guild_image_channel = get_image_channel(message.guild.id)
        if guild_image_channel:
            image_channel_id = guild_image_channel

    if image_channel_id and message.channel.id == image_channel_id:
        if message.attachments:
            added = 0
            for attachment in message.attachments:
                content_type = attachment.content_type or ""
                if content_type.startswith("image") or attachment.filename.lower().endswith((".png", ".jpg", ".jpeg", ".webp", ".gif")):
                    job_id = enqueue_match_screenshot(
                        message.id,
                        message.guild.id if message.guild else 0,
                        message.channel.id,
                        message.author.id,
                        attachment.url,
                        message.created_at.isoformat()
                    )
                    added += 1
            if added:
                await message.channel.send(
                    f"✅ Imagem adicionada para processamento de partida. ID da fila: {job_id}",
                    delete_after=15
                )

    if not message.content.startswith(bot.command_prefix):
        return

    if message.guild:
        allowed_channel = get_list_channel(message.guild.id)
        if allowed_channel and message.channel.id == allowed_channel:
            content = message.content.strip()
            command_name = content.split()[0][1:].lower() if content else ""
            allowed = command_name in {"lista", "lobby", "inhouse"}
            if not allowed:
                await message.channel.send(
                    "❌ Neste canal só é permitido usar `!lista` para abrir a lista.",
                    delete_after=10
                )
                return

    await bot.process_commands(message)


async def ocr_background_worker():
    await bot.wait_until_ready()
    logger.info("🔎 Iniciando worker OCR de imagens de partida.")
    while not bot.is_closed():
        if not can_process_ocr():
            logger.warning("OCR não configurado. Verifique as variáveis de ambiente.")
            await asyncio.sleep(60)
            continue

        jobs = get_pending_match_screenshots(limit=3)
        if not jobs:
            await asyncio.sleep(20)
            continue

        for job in jobs:
            try:
                set_match_screenshot_status(job["id"], "processing")
                result = process_match_screenshot(job["id"], job=job)
                parsed = result.get("parsed", {})
                logger.info(f"OCR concluído para job {job['id']}: {parsed.get('duration', 'sem duração')}.")
                channel = bot.get_channel(job["channel_id"])
                if channel:
                    if parsed.get("valid_dota_screenshot") is False:
                        await channel.send(
                            f"⚠️ O job {job['id']} não parece ser um placar de Dota válido e foi marcado como não processado."
                        )
                    else:
                        await channel.send(
                            f"✅ OCR concluído para a imagem do job {job['id']}. JSON processado e disponível. Use `!detalhesimagem {job['id']}` para revisar ou `!importarimagem {job['id']} <mapeamento>` para importar."
                        )
            except Exception as exc:
                logger.exception(f"Erro ao processar imagem OCR para job {job['id']}")
                set_match_screenshot_status(job["id"], "failed", metadata=str(exc))

        await asyncio.sleep(10)


logger.info("Configurando comandos...")
setup_lobby_commands(bot, active_lobbies)
setup_score_commands(bot)
logger.info("Comandos configurados com sucesso.")

if __name__ == "__main__":
    bot.run(TOKEN)
