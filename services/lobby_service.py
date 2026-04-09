# -*- coding: utf-8 -*-
import discord
import logging
from core.config import MAX_PLAYERS
from domain.models import LobbySession

logger = logging.getLogger("LobbyService")

async def close_session(
    session: LobbySession,
    interaction: discord.Interaction,
    active_lobbies: dict
):
    from ui.views.lobby_view import LobbyView
    from services.state import get_next_id
    
    session.closed = True
    message = session.message
    
    if message:
        active_lobbies.pop(message.id, None)

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

            new_view = LobbyView(new_session, active_lobbies)
            new_msg = await message.channel.send(
                f"📋 **Nova lista criada automaticamente com a espera!**",
                embed=new_session.build_embed(),
                view=new_view
            )
            new_session.message = new_msg
            active_lobbies[new_msg.id] = new_session

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
