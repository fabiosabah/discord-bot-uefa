# -*- coding: utf-8 -*-
import discord
from config import MAX_PLAYERS, LEAGUE_NAME, LEAGUE_EMOJI
from database import get_top_two

# ─────────────────────────────────────────────
# Modelo de sessão
# ─────────────────────────────────────────────

class LobbySession:
    def __init__(self, host: discord.Member, session_id: int):
        self.id = session_id
        self.host = host
        self.message: discord.Message | None = None
        self.players: list[discord.Member] = []
        self.player_ids: set[int] = set()
        self.waitlist: list[discord.Member] = []
        self.waitlist_ids: set[int] = set()
        self.closed = False

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
        Cruza os IDs dos jogadores presentes com o ranking do banco.
        """
        if len(self.players) < 2:
            return None

        # IDs dos jogadores atualmente na lista
        present_ids = self.player_ids

        # Busca o ranking e filtra apenas quem está na lista
        top = get_top_two()

        # Filtra capitães que estão na lista atual
        captains_in_list = [p for p in top if p["discord_id"] in present_ids]

        # Se não tiver 2 capitães na lista, pega os 2 primeiros da lista por ordem de entrada
        if len(captains_in_list) < 2:
            # fallback: primeiros da lista como capitães
            captain_a = self.players[0]
            captain_b = self.players[1]
            return (
                f"👑 **Capitães Definidos:**\n"
                f"🔵 Time A: {captain_a.mention}\n"
                f"🔴 Time B: {captain_b.mention}"
            )

        cap_a = captains_in_list[0]
        cap_b = captains_in_list[1]

        # Encontra o membro Discord correspondente
        member_a = next((p for p in self.players if p.id == cap_a["discord_id"]), None)
        member_b = next((p for p in self.players if p.id == cap_b["discord_id"]), None)

        if not member_a or not member_b:
            return None

        return (
            f"👑 **Capitães Definidos:**\n"
            f"🔵 Time A: {member_a.mention} ({cap_a['points']} pts)\n"
            f"🔴 Time B: {member_b.mention} ({cap_b['points']} pts)"
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

        # Lista de espera
        if self.waitlist:
            waitlist_str = "\n".join(
                f"`{i+1:02d}.` {p.mention}" for i, p in enumerate(self.waitlist)
            )
            embed.add_field(name=f"🔔 Espera ({len(self.waitlist)})", value=waitlist_str, inline=False)

        # Capitães (aparece sempre que houver 2+ jogadores na lista)
        captains_text = self._get_captains_field()
        if captains_text:
            embed.add_field(name="\u200b", value=captains_text, inline=False)

        return embed