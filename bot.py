import os
import discord
from discord.ext import commands
from dotenv import load_dotenv

# ─────────────────────────────────────────────
#  Configuração
# ─────────────────────────────────────────────
load_dotenv()
TOKEN = os.getenv("DISCORD_TOKEN")
MAX_PLAYERS = 10

intents = discord.Intents.default()
intents.message_content = True
intents.members = True

bot = commands.Bot(command_prefix="!", intents=intents)

active_lobbies: dict[int, "LobbySession"] = {}


# ─────────────────────────────────────────────
#  Modelo de sessão
# ─────────────────────────────────────────────
class LobbySession:
    def __init__(self, host: discord.Member):
        self.host = host
        self.message: discord.Message | None = None   # definido após o envio
        self.players: list[discord.Member] = []
        self.player_ids: set[int] = set()
        self.closed = False

    def add_player(self, member: discord.Member) -> bool:
        if self.closed or member.id in self.player_ids:
            return False
        self.players.append(member)
        self.player_ids.add(member.id)
        return True

    def remove_player(self, member_id: int) -> bool:
        if member_id not in self.player_ids:
            return False
        self.players = [p for p in self.players if p.id != member_id]
        self.player_ids.discard(member_id)
        return True

    def is_full(self) -> bool:
        return len(self.players) >= MAX_PLAYERS

    def build_embed(self) -> discord.Embed:
        filled = len(self.players)
        is_full = filled >= MAX_PLAYERS

        color = discord.Color.green() if is_full else discord.Color.blurple()
        status = "🔒 FECHADO — 10/10" if is_full else f"✅ Aberto — {filled}/{MAX_PLAYERS}"

        embed = discord.Embed(title="🎮 Lista de Presença — UEFA Fumos League", color=color)
        embed.set_footer(text=f"Aberto por {self.host.display_name}")
        embed.add_field(name="Status", value=status, inline=False)

        if self.players:
            lista = "\n".join(
                f"`{i+1:02d}.` {p.mention}" for i, p in enumerate(self.players)
            )
        else:
            lista = "_Nenhum jogador ainda._"

        embed.add_field(name=f"Jogadores ({filled}/{MAX_PLAYERS})", value=lista, inline=False)
        return embed


# ─────────────────────────────────────────────
#  Dropdown de remoção (visível só para mod/dono)
# ─────────────────────────────────────────────
class RemoveSelect(discord.ui.Select):
    def __init__(self, session: LobbySession):
        self.session = session
        options = [
            discord.SelectOption(label=p.display_name, value=str(p.id))
            for p in session.players
        ]
        super().__init__(
            placeholder="Selecione o jogador para remover...",
            options=options,
            custom_id="remove_select",
        )

    async def callback(self, interaction: discord.Interaction):
        session = self.session

        # Só dono ou moderador
        can_manage = (
            interaction.user.id == session.host.id
            or interaction.user.guild_permissions.manage_messages
        )
        if not can_manage:
            await interaction.response.send_message(
                "❌ Você não tem permissão para remover jogadores.", ephemeral=True
            )
            return

        member_id = int(self.values[0])
        removed_member = next((p for p in session.players if p.id == member_id), None)
        session.remove_player(member_id)

        # Usa a referência salva na sessão para editar a mensagem original
        view = LobbyView(session)
        await session.message.edit(embed=session.build_embed(), view=view)

        name = removed_member.display_name if removed_member else "Jogador"
        await interaction.response.send_message(
            f"✅ **{name}** foi removido da lista.", ephemeral=True
        )


class RemoveView(discord.ui.View):
    def __init__(self, session: LobbySession):
        super().__init__(timeout=30)
        self.add_item(RemoveSelect(session))


# ─────────────────────────────────────────────
#  View principal com botões
# ─────────────────────────────────────────────
class LobbyView(discord.ui.View):
    def __init__(self, session: LobbySession):
        super().__init__(timeout=None)
        self.session = session

    @discord.ui.button(label="✋ Entrar", style=discord.ButtonStyle.success, custom_id="entrar")
    async def entrar(self, interaction: discord.Interaction, button: discord.ui.Button):
        session = self.session

        if session.closed:
            await interaction.response.send_message("❌ Esta lista já está fechada.", ephemeral=True)
            return

        if interaction.user.id in session.player_ids:
            await interaction.response.send_message("⚠️ Você já está na lista!", ephemeral=True)
            return

        session.add_player(interaction.user)
        await interaction.response.edit_message(embed=session.build_embed(), view=self)

        if session.is_full():
            await _close_session(session, self, completed=True)

    @discord.ui.button(label="🚪 Sair", style=discord.ButtonStyle.danger, custom_id="sair")
    async def sair(self, interaction: discord.Interaction, button: discord.ui.Button):
        session = self.session

        if session.closed:
            await interaction.response.send_message("❌ Esta lista já está fechada.", ephemeral=True)
            return

        removed = session.remove_player(interaction.user.id)
        if not removed:
            await interaction.response.send_message("⚠️ Você não está na lista.", ephemeral=True)
            return

        await interaction.response.edit_message(embed=session.build_embed(), view=self)

    @discord.ui.button(label="👤 Remover jogador", style=discord.ButtonStyle.secondary, custom_id="remover_jogador")
    async def remover_jogador(self, interaction: discord.Interaction, button: discord.ui.Button):
        session = self.session

        can_manage = (
            interaction.user.id == session.host.id
            or interaction.user.guild_permissions.manage_messages
        )
        if not can_manage:
            await interaction.response.send_message(
                "❌ Apenas quem abriu a lista (ou um moderador) pode remover jogadores.", ephemeral=True
            )
            return

        if not session.players:
            await interaction.response.send_message("⚠️ Não há jogadores na lista.", ephemeral=True)
            return

        view = RemoveView(session)
        await interaction.response.send_message(
            "Selecione o jogador que deseja remover:", view=view, ephemeral=True
        )

    @discord.ui.button(label="🔒 Fechar lista", style=discord.ButtonStyle.secondary, custom_id="fechar")
    async def fechar(self, interaction: discord.Interaction, button: discord.ui.Button):
        session = self.session

        can_close = (
            interaction.user.id == session.host.id
            or interaction.user.guild_permissions.manage_messages
        )
        if not can_close:
            await interaction.response.send_message(
                "❌ Apenas quem abriu a lista (ou um moderador) pode fechá-la.", ephemeral=True
            )
            return

        await interaction.response.edit_message(embed=session.build_embed(), view=self)
        await _close_session(session, self, completed=False)


# ─────────────────────────────────────────────
#  Helpers
# ─────────────────────────────────────────────
async def _close_session(
    session: LobbySession,
    view: LobbyView,
    completed: bool,
):
    session.closed = True
    message = session.message
    active_lobbies.pop(message.id, None)

    filled = len(session.players)

    if completed:
        for item in view.children:
            item.disabled = True
        await message.edit(embed=session.build_embed(), view=view)

        mention_list = " ".join(p.mention for p in session.players)
        await message.channel.send(
            f"🔒 **Lista fechada!** Jogadores confirmados ({filled}/{MAX_PLAYERS}):\n{mention_list}"
        )
    else:
        await message.delete()

        if filled == 0:
            await message.channel.send(
                f"⚠️ **Lista encerrada sem jogadores.** Não foi possível completar os {MAX_PLAYERS} jogadores."
            )
        else:
            mention_list = " ".join(p.mention for p in session.players)
            await message.channel.send(
                f"⚠️ **Lista encerrada incompleta!** Só foi possível reunir {filled}/{MAX_PLAYERS} jogadores:\n{mention_list}"
            )


# ─────────────────────────────────────────────
#  Comandos
# ─────────────────────────────────────────────
@bot.command(name="lista", aliases=["lobby", "inhouse"])
async def open_list(ctx: commands.Context):
    session = LobbySession(host=ctx.author)
    view = LobbyView(session)

    msg = await ctx.send(embed=session.build_embed(), view=view)
    session.message = msg   # salva referência na sessão
    active_lobbies[msg.id] = session

    await ctx.message.delete()


# ─────────────────────────────────────────────
#  Startup
# ─────────────────────────────────────────────
@bot.event
async def on_ready():
    print(f"✅ Bot conectado como {bot.user} (ID: {bot.user.id})")
    print("─" * 40)


bot.run(TOKEN)