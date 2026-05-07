# -*- coding: utf-8 -*-
import asyncio
import discord
from datetime import datetime, timedelta
from core.config import MAX_PLAYERS, LEAGUE_NAME, LEAGUE_EMOJI
from core.db.player_repo import get_captains_from_list, _CAPTAIN_FALLBACK_THRESHOLD
from core.db.match_repo import get_streak_highlights_from_matches

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
        self.dota_lobby_triggered: bool = False

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

    def schedule_auto_close(self, active_lobbies: dict, close_fn, delay: int | None = None):
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

        loop = asyncio.get_running_loop()
        self.close_task = loop.create_task(self._auto_close_countdown(active_lobbies, close_fn, delay))

    async def _auto_close_countdown(self, active_lobbies: dict, close_fn, delay: int):
        try:
            if delay > 0:
                await asyncio.sleep(delay)
            if self.closed:
                return

            if self.message is None:
                return

            await close_fn(self, active_lobbies)
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
        if len(self.players) < 2:
            return None

        present_ids = list(self.player_ids)
        result = get_captains_from_list(present_ids)
        captains_data = result["captains"]
        is_fallback = result["is_fallback"]
        season_used = result["season_used"]
        current_season = result["current_season"]
        match_count = result["match_count"]
        remaining = _CAPTAIN_FALLBACK_THRESHOLD - match_count

        if len(captains_data) < 2:
            captain_a = self.players[0]
            captain_b = self.players[1]
            lines = [
                f"👑 **Capitães Definidos:**",
                f"🔵 Time A: {captain_a.mention}",
                f"🔴 Time B: {captain_b.mention}",
                f"*(Baseado em ordem de entrada — sem dados no banco)*",
            ]
            if is_fallback:
                lines.append(f"⚠️ *Usando dados da Temporada {season_used} — ainda faltam {remaining} partida(s) na T{current_season} para usar os dados desta temporada.*")
            return "\n".join(lines)

        cap_a = captains_data[0]
        cap_b = captains_data[1]

        member_a = next((p for p in self.players if p.id == cap_a["discord_id"]), None)
        member_b = next((p for p in self.players if p.id == cap_b["discord_id"]), None)

        if not member_a or not member_b:
            return None

        lines = [
            f"👑 **Capitães Definidos:**",
            f"🔵 Time A: {member_a.mention} ({cap_a['points']} pts | {cap_a['wins']}V)",
            f"🔴 Time B: {member_b.mention} ({cap_b['points']} pts | {cap_b['wins']}V)",
        ]
        if is_fallback:
            lines.append(f"⚠️ *Usando dados da Temporada {season_used} — ainda faltam {remaining} partida(s) na T{current_season} para usar os dados desta temporada.*")
        elif current_season > 1 and match_count == _CAPTAIN_FALLBACK_THRESHOLD:
            lines.append(f"🎉 *A partir de agora os capitães são definidos pelos dados da Temporada {current_season}!*")
        return "\n".join(lines)

    def build_embed(self) -> discord.Embed:
        filled = len(self.players)
        is_full = filled >= MAX_PLAYERS
        color = discord.Color.green() if is_full else discord.Color.blurple()
        status = f"🔒 CHEIO — {MAX_PLAYERS}/{MAX_PLAYERS}" if is_full else f"✅ Aberto — {filled}/{MAX_PLAYERS}"

        embed = discord.Embed(title=f"{LEAGUE_EMOJI} Lista de Presença — {LEAGUE_NAME}", color=color)
        embed.set_footer(text=f"Aberto por {self.host.display_name} | ID: #{self.id}")
        embed.add_field(name="Status", value=status, inline=False)

        if self.players:
            streaks = get_streak_highlights_from_matches()
            win_ids  = {pl["discord_id"] for pl in streaks["current_win"]["players"]}  if streaks["current_win"]["count"]  >= 3 else set()
            loss_ids = {pl["discord_id"] for pl in streaks["current_loss"]["players"]} if streaks["current_loss"]["count"] >= 3 else set()
            linhas_jogadores = []
            has_streak = False
            for i, p in enumerate(self.players):
                tag = ""
                if p.id in win_ids:
                    tag = " 🔥"
                    has_streak = True
                elif p.id in loss_ids:
                    tag = " 💩"
                    has_streak = True
                linhas_jogadores.append(f"`{i+1:02d}.` {p.mention}{tag}")
            if has_streak:
                linhas_jogadores.append("\n🔥 tá pegando fogo  |  💩 tá fedendo")
            lista = "\n".join(linhas_jogadores)
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
