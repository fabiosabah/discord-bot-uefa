import discord
import uuid
from config import MAX_PLAYERS


# ─────────────────────────────────────────────
#  Modelo de sessão
# ─────────────────────────────────────────────
class LobbySession:
    def __init__(self, host: discord.Member):
        self.id = str(uuid.uuid4())[:8].upper()  # ID único de 8 caracteres
        self.host = host
        self.message: discord.Message | None = None   # definido após o envio
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

    def build_embed(self) -> discord.Embed:
        filled = len(self.players)
        is_full = filled >= MAX_PLAYERS

        color = discord.Color.green() if is_full else discord.Color.blurple()
        status = "🔒 CHEIO — 10/10" if is_full else f"✅ Aberto — {filled}/{MAX_PLAYERS}"

        embed = discord.Embed(title="🎮 Lista de Presença — UEFA Fumos League", color=color)
        embed.set_footer(text=f"Aberto por {self.host.display_name} | ID: {self.id}")
        embed.add_field(name="Status", value=status, inline=False)

        if self.players:
            lista = "\n".join(
                f"`{i+1:02d}.` {p.mention}" for i, p in enumerate(self.players)
            )
        else:
            lista = "_Nenhum jogador ainda._"

        embed.add_field(name=f"Jogadores ({filled}/{MAX_PLAYERS})", value=lista, inline=False)

        # Mostrar lista de espera se houver
        if self.waitlist:
            waitlist_str = "\n".join(
                f"`{i+1:02d}.` {p.mention}" for i, p in enumerate(self.waitlist)
            )
            embed.add_field(name=f"🔔 Espera ({len(self.waitlist)})", value=waitlist_str, inline=False)

        return embed
