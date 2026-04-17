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
from domain.models import LobbySession
from ui.commands.lobby_commands import setup_lobby_commands
from ui.commands.score_commands import setup_score_commands, build_ocr_job_summary_text

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

class PartialMember:
    def __init__(self, id: int, display_name: str):
        self.id = id
        self.display_name = display_name
        self.name = display_name

    @property
    def mention(self):
        return f"<@{self.id}>"


async def _resolve_member(guild: discord.Guild, member_id: int):
    member = guild.get_member(member_id)
    if member:
        return member
    try:
        return await guild.fetch_member(member_id)
    except discord.NotFound:
        return PartialMember(member_id, f"Usuário {member_id}")


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

        host = await _resolve_member(guild, row["host_id"])
        session = LobbySession(host=host, session_id=row["session_id"])
        session.message = message
        session.players = [await _resolve_member(guild, pid) for pid in row["player_ids"]]
        session.player_ids = set(row["player_ids"])
        session.waitlist = [await _resolve_member(guild, wid) for wid in row["waitlist_ids"]]
        session.waitlist_ids = set(row["waitlist_ids"])
        session.closed = bool(row["closed"])
        auto_close_at = row["auto_close_at"] if "auto_close_at" in row.keys() else None
        session.auto_close_at = datetime.fromisoformat(auto_close_at) if auto_close_at else None

        if session.closed:
            delete_lobby_session(row["guild_id"])
            continue

        active_lobbies[message.id] = session
        if session.auto_close_at or session.is_full():
            session.schedule_auto_close(active_lobbies)


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


async def ocr_background_worker():
    await bot.wait_until_ready()
    logger.info("🔎 Iniciando worker OCR de imagens de partida.")
    while not bot.is_closed():
        if not can_process_ocr():
            logger.warning("OCR não configurado. Verifique as variáveis de ambiente.")
            await asyncio.sleep(60)
            continue

        jobs = get_pending_match_screenshots(limit=1)
        if not jobs:
            await asyncio.sleep(20)
            continue

        for job in jobs:
            try:
                set_match_screenshot_status(job["id"], "processing")
                result = await asyncio.to_thread(process_match_screenshot, job["id"], job)
                parsed = result.get("parsed", {})
                logger.info(f"OCR concluído para job {job['id']}: {parsed.get('duration', 'sem duração')}.")
                channel = bot.get_channel(job["channel_id"])
                if channel:
                    if parsed.get("valid_dota_screenshot") is False:
                        await channel.send(
                            f"⚠️ O job {job['id']} não parece ser um placar de Dota válido e foi marcado como não processado."
                        )
                    else:
                        summary = build_ocr_job_summary_text(job["id"], parsed)
                        await channel.send(summary)
            except Exception as exc:
                logger.exception(f"Erro ao processar imagem OCR para job {job['id']}")
                set_match_screenshot_status(job["id"], "failed", metadata=str(exc))

            # Rate limit the worker to roughly 15 requests per minute for Gemini 3 Flash.
            await asyncio.sleep(4)


logger.info("Configurando comandos...")
setup_lobby_commands(bot, active_lobbies)
setup_score_commands(bot)
logger.info("Comandos configurados com sucesso.")

if __name__ == "__main__":
    bot.run(TOKEN)
