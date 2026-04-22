# -*- coding: utf-8 -*-
import asyncio
import discord
import logging
import sys
from datetime import datetime
from discord.ext import commands
from core.config import TOKEN, IMAGE_CHANNEL_ID
from core.database import (
    init_db,
    migrate_db,
    get_list_channel,
    get_image_channel,
    enqueue_match_screenshot,
    get_pending_match_screenshots,
    set_match_screenshot_status,
    get_lobby_sessions,
    delete_lobby_session
)
from core.ocr import can_process_ocr, can_process_llm, process_match_screenshot
from core.utils.discord_helpers import resolve_member
from domain.models import LobbySession
from services.lobby_service import close_session
from ui.commands.lobby_commands import setup_lobby_commands
from ui.commands.score_commands import setup_score_commands
from ui.commands.score_helpers import build_ocr_job_summary_text
from ui.views.lobby_view import LobbyView

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

bot = commands.Bot(command_prefix="!", intents=intents, help_command=None, case_insensitive=True)

active_lobbies = {}


async def restore_saved_lobby_sessions():
    saved_sessions = get_lobby_sessions()

    for row in saved_sessions:
        guild = bot.get_guild(row["guild_id"])
        if not guild:
            delete_lobby_session(row["guild_id"])
            continue

        channel = bot.get_channel(row["channel_id"])
        if channel is None:
            try:
                channel = await bot.fetch_channel(row["channel_id"])
            except (discord.NotFound, discord.Forbidden, discord.HTTPException):
                delete_lobby_session(row["guild_id"])
                continue

        try:
            message = await channel.fetch_message(row["message_id"])
        except (discord.NotFound, discord.Forbidden, discord.HTTPException):
            delete_lobby_session(row["guild_id"])
            continue

        host = await resolve_member(guild, row["host_id"])
        session = LobbySession(host=host, session_id=row["session_id"])
        session.message = message
        session.players = [await resolve_member(guild, pid) for pid in row["player_ids"]]
        session.player_ids = set(row["player_ids"])
        session.waitlist = [await resolve_member(guild, wid) for wid in row["waitlist_ids"]]
        session.waitlist_ids = set(row["waitlist_ids"])
        session.closed = bool(row["closed"])
        session.created_at = datetime.fromisoformat(row["created_at"]) if row["created_at"] else datetime.now()
        auto_close_at = row["auto_close_at"] if "auto_close_at" in row.keys() else None
        session.auto_close_at = datetime.fromisoformat(auto_close_at) if auto_close_at else None

        if session.closed:
            delete_lobby_session(row["guild_id"])
            continue

        active_lobbies[message.id] = session
        if session.auto_close_at or session.is_full():
            session.schedule_auto_close(active_lobbies, close_fn=lambda s, l: close_session(s, l, view_factory=lambda sv, lv: LobbyView(sv, lv)))


@bot.event
async def on_ready():
    init_db()
    migrate_db()
    logger.info(f"✅ Bot conectado como {bot.user} (ID: {bot.user.id})")
    print("-" * 40)
    for guild in bot.guilds:
        logger.info(f"Servidor: {guild.name} | Membros: {len(guild.members)}")

    await restore_saved_lobby_sessions()

    if can_process_ocr():
        bot.loop.create_task(ocr_background_worker())
    else:
        logger.warning(
            "OCR desativado: configure OPENAI_API_KEY ou GEMINI_API_KEY."
        )

    if not can_process_llm():
        logger.warning("LLM desativado: configure OPENAI_API_KEY ou GEMINI_API_KEY para usar a interpretação com IA.")

@bot.event
async def on_command(ctx: commands.Context):
    guild  = ctx.guild.name if ctx.guild else "DM"
    logger.info(f"[CMD] {ctx.author.display_name} ({ctx.author.id}) → !{ctx.command} | #{ctx.channel} | {guild}")


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
        logger.debug(f"[BOT] Processing message in image channel {message.channel.id}")
        if message.attachments:
            added = 0
            for attachment in message.attachments:
                content_type = attachment.content_type or ""
                filename = attachment.filename.lower()
                logger.debug(f"[BOT] Checking attachment: {filename}, content_type: {content_type}")
                if content_type.startswith("image") or filename.endswith((".png", ".jpg", ".jpeg", ".webp", ".gif")):
                    logger.info(f"[BOT] Enqueuing image for OCR: {attachment.url} from user {message.author.id}")
                    job_id = enqueue_match_screenshot(
                        message.id,
                        message.guild.id if message.guild else 0,
                        message.channel.id,
                        message.author.id,
                        attachment.url,
                        message.created_at.isoformat()
                    )
                    logger.info(f"[BOT] Image enqueued for OCR processing: job {job_id}")
                    added += 1
            if added:
                logger.info(f"[BOT] {added} image(s) added to OCR queue from message {message.id}")
                await message.channel.send(
                    f"📷 Imagem recebida! Enviando para análise... _(job #{job_id})_",
                    delete_after=60
                )
        else:
            logger.debug("[BOT] Message in image channel has no attachments")

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


@bot.event
async def on_command_error(ctx: commands.Context, error: commands.CommandError):
    if isinstance(error, commands.MissingRequiredArgument):
        await ctx.send(
            f"❌ Argumento obrigatório faltando: `{error.param.name}`. Use `!{ctx.command}` com todos os parâmetros.",
            delete_after=20
        )
        return

    if isinstance(error, commands.BadArgument):
        await ctx.send(f"❌ Argumento inválido: {error}", delete_after=20)
        return

    if isinstance(error, commands.CommandNotFound):
        return

    logger.exception(f"Erro no comando {ctx.command}: {error}")
    await ctx.send("❌ Ocorreu um erro ao processar o comando.", delete_after=20)


async def _schedule_ocr_summary_deletion(channel, summary_msg, job_id: int):
    await asyncio.sleep(105)  # espera 1m45s antes de avisar
    try:
        warning_msg = await channel.send(
            f"⏳ O resumo do job #{job_id} será apagado em **15 segundos**.\n"
            f"Para consultar os dados depois da importação use `!detalhesimagem {job_id}`.",
            delete_after=60
        )
    except Exception:
        warning_msg = None

    await asyncio.sleep(15)  # aviso fica 15s

    for msg in (summary_msg, warning_msg):
        if msg is None:
            continue
        try:
            await msg.delete()
        except Exception:
            pass


async def ocr_background_worker():
    await bot.wait_until_ready()
    logger.info("🔎 Iniciando worker OCR de imagens de partida.")
    while not bot.is_closed():
        if not can_process_ocr():
            logger.warning("OCR não configurado. Verifique as variáveis de ambiente.")
            await asyncio.sleep(60)
            continue

        logger.debug("🔍 Checking for pending OCR jobs...")
        jobs = get_pending_match_screenshots(limit=1)
        if not jobs:
            logger.debug("📭 No pending OCR jobs found, sleeping for 20 seconds")
            await asyncio.sleep(20)
            continue

        for job in jobs:
            job_id = job["id"]
            logger.info(f"📋 Processing OCR job {job_id} from channel {job['channel_id']}")
            
            try:
                logger.debug(f"🔄 Setting job {job_id} status to 'processing'")
                set_match_screenshot_status(job["id"], "processing")

                channel = bot.get_channel(job["channel_id"])
                if channel:
                    status_msg = await channel.send(
                        f"🤖 Analisando imagem com IA... _(job #{job_id})_"
                    )
                else:
                    status_msg = None

                logger.debug(f"🤖 Starting OCR processing for job {job_id}")
                result = await asyncio.to_thread(process_match_screenshot, job["id"], job)
                parsed = result.get("parsed", {})

                logger.info(f"✅ OCR completed for job {job_id}: duration={parsed.get('duration', 'unknown')}, valid={parsed.get('valid_dota_screenshot', 'unknown')}")

                if status_msg:
                    try:
                        await status_msg.delete()
                    except Exception:
                        pass

                if channel:
                    if parsed.get("valid_dota_screenshot") is False:
                        logger.warning(f"⚠️ Job {job_id} marked as invalid Dota screenshot; no message will be sent to channel")
                    else:
                        logger.debug(f"📤 Sending OCR results to channel {job['channel_id']}")
                        summary = build_ocr_job_summary_text(job["id"], parsed)
                        summary_msg = await channel.send(summary)
                        logger.info(f"📤 OCR results sent to channel {job['channel_id']} for job {job_id}")
                        bot.loop.create_task(
                            _schedule_ocr_summary_deletion(channel, summary_msg, job["id"])
                        )
                else:
                    logger.warning(f"❌ Could not find channel {job['channel_id']} to send OCR results")
                    
            except Exception as exc:
                logger.exception(f"💥 Error processing OCR job {job_id}: {exc}")
                set_match_screenshot_status(job["id"], "failed", metadata=str(exc))
                logger.info(f"❌ Job {job_id} marked as failed")

            # Rate limit the worker to roughly 15 requests per minute for Gemini 3 Flash.
            logger.debug("⏱️ Rate limiting: sleeping for 4 seconds")
            await asyncio.sleep(4)


logger.info("Configurando comandos...")
setup_lobby_commands(bot, active_lobbies)
setup_score_commands(bot)
logger.info("Comandos configurados com sucesso.")

if __name__ == "__main__":
    bot.run(TOKEN)
