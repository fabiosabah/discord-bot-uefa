# -*- coding: utf-8 -*-
import asyncio
import logging

import discord
import requests

from core.config import MAX_PLAYERS, GC_API_URL
from core.db.bot_config_repo import is_lobby_integration_enabled
from core.db.lobby_repo import delete_lobby_session, save_lobby_session
from core.db.player_repo import get_steam_friend_id
from domain.models import LobbySession

logger = logging.getLogger("LobbyService")

_FRIEND_ID_GUIDE = (
    "**Como encontrar seu Friend ID:**\n"
    "1. Abra o Steam\n"
    "2. Clique em **Amigos e conversas** (canto inferior direito)\n"
    "3. Clique no botão **Adicionar Amigo** (ícone de pessoa com +)\n"
    "4. Seu **Friend Code** aparecerá em destaque — clique em Copiar\n"
    "5. Digite no Discord: `!meusteam <seu friend code>`"
)


async def close_session(
    session: LobbySession,
    active_lobbies: dict,
    view_factory,
):
    from services.state import get_next_id

    session.closed = True
    session.cancel_auto_close()
    message = session.message

    if message:
        await message.edit(embed=session.build_embed(), view=view_factory(session, active_lobbies))
        active_lobbies.pop(message.id, None)
        delete_lobby_session(message.guild.id)

    filled = len(session.players)
    cancelled = filled < MAX_PLAYERS

    if cancelled:
        logger.warning(f"[Encerrar] Lista #{session.id} | ❌ CANCELADA | Host: {session.host.name}#{session.host.id} | Apenas {filled}/10 jogadores")
        await message.channel.send(
            f"❌ **Lista cancelada!** Não foi possível reunir {MAX_PLAYERS} jogadores (só {filled})."
        )
        if session.players:
            mention_list = " ".join(p.mention for p in session.players)
            await message.channel.send(f"Jogadores que participaram: {mention_list}")
    else:
        logger.info(f"[Encerrar] Lista #{session.id} | ✅ CONFIRMADA | Host: {session.host.name}#{session.host.id} | {filled}/10 jogadores")
        mention_list = " ".join(p.mention for p in session.players)
        await message.channel.send(
            f"🔒 **Lista confirmada!** Jogadores finais ({filled}/{MAX_PLAYERS}):\n{mention_list}"
        )

        if is_lobby_integration_enabled() and GC_API_URL and not session.dota_lobby_triggered:
            asyncio.create_task(
                _trigger_dota_lobby(session, message.channel)
            )

        if session.waitlist:
            next_players = session.waitlist[:MAX_PLAYERS]
            remaining_waitlist = session.waitlist[MAX_PLAYERS:]

            new_session = LobbySession(host=session.host, session_id=get_next_id())
            for player in next_players:
                new_session.add_player(player)
            for player in remaining_waitlist:
                new_session.add_to_waitlist(player)

            new_view = view_factory(new_session, active_lobbies)
            new_msg = await message.channel.send(
                "📋 **Nova lista criada automaticamente com a espera!**",
                embed=new_session.build_embed(),
                view=new_view
            )
            new_session.message = new_msg
            active_lobbies[new_msg.id] = new_session
            save_lobby_session(new_session, created_at=new_session.message.created_at.isoformat())

            if new_session.is_full():
                new_session.schedule_auto_close(active_lobbies, close_fn=lambda s, l: close_session(s, l, view_factory))

            mention_waitlist = " ".join(p.mention for p in next_players)
            await message.channel.send(
                f"🔄 **Próxima lista criada!** {len(next_players)} jogadores da espera:\n{mention_waitlist}"
            )

            if len(remaining_waitlist) > 0:
                await message.channel.send(
                    f"🔔 Ainda há {len(remaining_waitlist)} pessoa(s) na espera da nova lista."
                )
        else:
            await message.channel.send("✅ Lista concluída! Nenhuma espera.")


async def _poll_lobby_id(retries: int = 8, delay: float = 2.0) -> int | None:
    for _ in range(retries):
        await asyncio.sleep(delay)
        try:
            resp = await asyncio.to_thread(
                requests.get, f"{GC_API_URL}/status", timeout=5
            )
            if resp.ok:
                lobby = resp.json().get("lobby") or {}
                lid = lobby.get("lobby_id", 0)
                if lid:
                    return lid
        except Exception:
            pass
    return None


async def _trigger_dota_lobby(session: LobbySession, channel: discord.TextChannel):
    players = []
    missing_steam = []

    for member in session.players:
        fid = get_steam_friend_id(member.id)
        if fid:
            players.append({"steam_friend_id": fid, "discord_name": member.display_name})
        else:
            missing_steam.append(member)

    if missing_steam:
        names = ", ".join(m.mention for m in missing_steam)
        await channel.send(
            f"⚠️ Os seguintes jogadores **não têm Friend ID cadastrado** e não receberão convite automático:\n"
            f"{names}\n\n"
            f"🎮 {_FRIEND_ID_GUIDE}",
        )

    if not players:
        await channel.send("❌ Nenhum jogador com Friend ID cadastrado — lobby Dota não criado.")
        return

    try:
        resp = await asyncio.to_thread(
            requests.post,
            f"{GC_API_URL}/lobby",
            json={"preset": "inhouse", "password": "1234", "players": players},
            timeout=35,
        )
        data = resp.json()
    except Exception as e:
        logger.error(f"[LobbyService] Erro ao criar lobby no GC: {e}")
        await channel.send("❌ Erro ao comunicar com o serviço GC. Crie o lobby manualmente.")
        return

    if not resp.ok:
        await channel.send(f"❌ GC recusou o lobby: `{data.get('error', resp.status_code)}`")
        return

    password = data.get('password', '1234')
    await channel.send(
        f"🎮 **Lobby Dota 2 criado!**\n"
        f"Senha: `{password}`\n"
        f"Convites enviados para {len(players)} jogadores."
    )
    logger.info(f"[LobbyService] Lobby criado para lista #{session.id} com {len(players)} jogadores")

    lobby_id = await _poll_lobby_id()
    if lobby_id:
        await channel.send(
            f"🔗 **Entrar no lobby:** `steam://joinlobby/570/{lobby_id}`\n"
            f"_(clique no link ou copie para o navegador/Steam)_"
        )
    else:
        logger.warning("[LobbyService] lobby_id não disponível após polling — link não enviado")
