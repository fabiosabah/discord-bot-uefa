# -*- coding: utf-8 -*-
import discord
import logging
from core.config import MAX_PLAYERS
from core.db.lobby_repo import delete_lobby_session, save_lobby_session
from domain.models import LobbySession

logger = logging.getLogger("LobbyService")

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

        if session.waitlist:
            # Pega os próximos 10 da espera
            next_players = session.waitlist[:MAX_PLAYERS]
            remaining_waitlist = session.waitlist[MAX_PLAYERS:]

            new_session = LobbySession(host=session.host, session_id=get_next_id())
            
            for player in next_players:
                new_session.add_player(player)
            
            for player in remaining_waitlist:
                new_session.add_to_waitlist(player)

            new_view = view_factory(new_session, active_lobbies)
            new_msg = await message.channel.send(
                f"📋 **Nova lista criada automaticamente com a espera!**",
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
