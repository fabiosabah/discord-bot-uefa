# -*- coding: utf-8 -*-
import asyncio
import discord
from datetime import datetime, timedelta
from core.config import MAX_PLAYERS, LEAGUE_NAME, LEAGUE_EMOJI
from core.database import get_captains_from_list

class LobbySession:
    CLOSE_DELAY_SECONDS = 0
    TIMEOUT_TO_ALLOW_ANY_CLOSE_MINUTES = 10

    def __init__(self, host: discord.Member, session_id: int):
        self.id = session_id
        self.host = host
        self.message: discord.Message | None = None
        self.players: list[discord.Member] = []
        self.player_ids: set[int] = set()
        self.waitlist: list[discord.Member] = []
        self.waitlist_ids: set[int] = set()
        self.closed = False
        self.close_task: asyncio.Task | None = None
        self.auto_close_at: datetime | None = None
        self.created_at: datetime = datetime.now()

    def can_any_user_close(self) -> bool:
        """Verifica se já passaram os minutos necessários para qualquer um encerrar a lista."""
        elapsed = datetime.now() - self.created_at
        elapsed_minutes = elapsed.total_seconds() / 60
        return elapsed_minutes >= self.TIMEOUT_TO_ALLOW_ANY_CLOSE_MINUTES

    def cancel_auto_close(self):
        if self.close_task and not self.close_task.done():
            self.close_task.cancel()
        self.close_task = None
        self.auto_close_at = None

    def schedule_auto_close(self, active_lobbies: dict, delay: int | None = None):
        if self.closed:
            return

        if self.close_task is not None:
            return

        now = datetime.now()
        if delay is None:
            if self.auto_close_at:
                delay = max(0, int((self.auto_close_at - now).total_seconds()))
            else:
                delay = self.CLOSE_DELAY_SECONDS
                self.auto_close_at = now + timedelta(seconds=delay)
        else:
            self.auto_close_at = now + timedelta(seconds=delay)

        if delay == 0:
            return

        loop = asyncio.get_running_loop()
        self.close_task = loop.create_task(self._auto_close_countdown(active_lobbies, delay))

    async def _auto_close_countdown(self, active_lobbies: dict, delay: int):
        try:
            if delay > 0:
                await asyncio.sleep(delay)
            if self.closed:
                return

            if self.message is None:
                return

            from services.lobby_service import close_session
            await close_session(self, active_lobbies)
        except asyncio.CancelledError:
            pass

    def add_player(self, member: discord.Member) -> bool:
        if self.closed or member.id in self.player_ids or member.id in self.waitlist_ids:
            return False
        self.players.append(member)
        self.player_ids.add(member.id)
        return True

    def add_to_waitlist(self, member: discord.Member) -> bool:
        if self.closed or member.id in self.waitlist_ids or member.id in self.player_ids:
            return False
        self.waitlist.append(member)
        self.waitlist_ids.add(member.id)
        return True

    def remove_player(self, member_id: int) -> bool:
        if member_id not in self.player_ids:
            return False
        self.players = [p for p in self.players if p.id != member_id]
        self.player_ids.discard(member_id)
        return True

    def remove_from_waitlist(self, member_id: int) -> bool:
        if member_id not in self.waitlist_ids:
            return False
        self.waitlist = [p for p in self.waitlist if p.id != member_id]
        self.waitlist_ids.discard(member_id)
        return True

    def promote_waitlist(self) -> discord.Member | None:
        if not self.waitlist:
            return None
        next_player = self.waitlist.pop(0)
        self.waitlist_ids.discard(next_player.id)
        self.players.append(next_player)
        self.player_ids.add(next_player.id)
        return next_player

    def is_full(self) -> bool:
        return len(self.players) >= MAX_PLAYERS

    def _get_captains_field(self) -> str | None:
        """
        Retorna a string dos capitães se houver pelo menos 2 jogadores na lista.
        Busca os 2 melhores no banco dentre os IDs presentes.
        """
        if len(self.players) < 2:
            return None

        present_ids = list(self.player_ids)

        captains_data = get_captains_from_list(present_ids)

        if len(captains_data) < 2:
            captain_a = self.players[0]
            captain_b = self.players[1]
            return (
                f"👑 **Capitães Definidos:**\n"
                f"🔵 Time A: {captain_a.mention}\n"
                f"🔴 Time B: {captain_b.mention}\n"
                f"*(Baseado em ordem de entrada - sem dados no banco)*"
            )

        cap_a = captains_data[0]
        cap_b = captains_data[1]

        member_a = next((p for p in self.players if p.id == cap_a["discord_id"]), None)
        member_b = next((p for p in self.players if p.id == cap_b["discord_id"]), None)

        if not member_a or not member_b:
            return None

        return (
            f"👑 **Capitães Definidos:**\n"
            f"🔵 Time A: {member_a.mention} ({cap_a['points']} pts | {cap_a['wins']}V)\n"
            f"🔴 Time B: {member_b.mention} ({cap_b['points']} pts | {cap_b['wins']}V)"
        )

    def build_embed(self) -> discord.Embed:
        filled = len(self.players)
        is_full = filled >= MAX_PLAYERS
        color = discord.Color.green() if is_full else discord.Color.blurple()
        status = f"🔒 CHEIO — {MAX_PLAYERS}/{MAX_PLAYERS}" if is_full else f"✅ Aberto — {filled}/{MAX_PLAYERS}"

        embed = discord.Embed(title=f"{LEAGUE_EMOJI} Lista de Presença — {LEAGUE_NAME}", color=color)
        embed.set_footer(text=f"Aberto por {self.host.display_name} | ID: #{self.id}")
        embed.add_field(name="Status", value=status, inline=False)

        if self.players:
            lista = "\n".join(
                f"`{i+1:02d}.` {p.mention}" for i, p in enumerate(self.players)
            )
        else:
            lista = "_Nenhum jogador ainda._"

        embed.add_field(name=f"Jogadores ({filled}/{MAX_PLAYERS})", value=lista, inline=False)

        if self.waitlist:
            waitlist_str = "\n".join(
                f"`{i+1:02d}.` {p.mention}" for i, p in enumerate(self.waitlist)
            )
            embed.add_field(name=f"🔔 Espera ({len(self.waitlist)})", value=waitlist_str, inline=False)

        if self.auto_close_at and not self.closed:
            remaining = self.auto_close_at - datetime.now()
            if remaining.total_seconds() > 0:
                minutes = int(remaining.total_seconds() // 60)
                seconds = int(remaining.total_seconds() % 60)
                timer_text = f"⏱️ Fechamento automático em {minutes}m{seconds:02d}s"
            else:
                timer_text = "⏱️ Fechamento automático em breve"
            embed.add_field(name="⏳ Tempo restante", value=timer_text, inline=False)

        captains_text = self._get_captains_field()
        if captains_text:
            embed.add_field(name="\u200b", value=captains_text, inline=False)

        return embed
