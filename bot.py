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
        embed.set_footer(text=f"Aberto por {self.host.display_name}")
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


# ─────────────────────────────────────────────
#  Dropdown de remoção (visível só para criador)
# ─────────────────────────────────────────────
class RemoveSelect(discord.ui.Select):
    def __init__(self, session: LobbySession):
        self.session = session
        options = []

        # Adiciona jogadores da lista principal
        for p in session.players:
            options.append(discord.SelectOption(label=f"📋 {p.display_name}", value=f"player_{p.id}"))

        # Adiciona jogadores da espera
        for p in session.waitlist:
            options.append(discord.SelectOption(label=f"🔔 {p.display_name}", value=f"waitlist_{p.id}"))

        super().__init__(
            placeholder="Selecione quem remover...",
            options=options,
            custom_id="remove_select",
        )

    async def callback(self, interaction: discord.Interaction):
        session = self.session

        # Só criador
        if interaction.user.id != session.host.id:
            await interaction.response.send_message(
                "❌ Apenas quem criou a lista pode remover pessoas.", ephemeral=True
            )
            return

        value = self.values[0]
        is_waitlist = value.startswith("waitlist_")
        member_id = int(value.split("_")[1])

        if is_waitlist:
            removed_member = next((p for p in session.waitlist if p.id == member_id), None)
            session.remove_from_waitlist(member_id)
            list_type = "espera"
            promoted = None
        else:
            removed_member = next((p for p in session.players if p.id == member_id), None)
            session.remove_player(member_id)
            promoted = session.promote_waitlist()
            list_type = "lista"

        # Usa a referência salva na sessão para editar a mensagem original
        view = LobbyView(session)
        await session.message.edit(embed=session.build_embed(), view=view)

        name = removed_member.display_name if removed_member else "Pessoa"
        response = f"✅ **{name}** foi removido da {list_type}."
        if promoted:
            response += f"\n🔔 **{promoted.display_name}** foi promovido da espera para a lista."

        await interaction.response.send_message(response, ephemeral=True)


class RemoveView(discord.ui.View):
    def __init__(self, session: LobbySession):
        super().__init__(timeout=30)
        self.add_item(RemoveSelect(session))


class AddUserSelect(discord.ui.UserSelect):
    def __init__(self, session: LobbySession):
        self.session = session
        super().__init__(placeholder="Selecione um usuário para adicionar...", custom_id="add_user_select")

    async def callback(self, interaction: discord.Interaction):
        session = self.session

        if interaction.user.id != session.host.id and not interaction.user.guild_permissions.manage_messages:
            await interaction.response.send_message(
                "❌ Apenas o criador ou um administrador pode adicionar pessoas.", ephemeral=True
            )
            return

        member = self.values[0]
        if member.id in session.player_ids or member.id in session.waitlist_ids:
            await interaction.response.send_message(
                "⚠️ Esse usuário já está na lista ou na espera.", ephemeral=True
            )
            return

        if session.is_full():
            session.add_to_waitlist(member)
            response = f"🔔 {member.mention} foi adicionado à espera (posição {len(session.waitlist)})."
        else:
            session.add_player(member)
            response = f"✅ {member.mention} foi adicionado à lista."

        await interaction.response.send_message(response, ephemeral=True)
        await session.message.edit(embed=session.build_embed(), view=LobbyView(session))


class AddUserView(discord.ui.View):
    def __init__(self, session: LobbySession):
        super().__init__(timeout=30)
        self.add_item(AddUserSelect(session))


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

        if interaction.user.id in session.waitlist_ids:
            await interaction.response.send_message("⚠️ Você já está na espera!", ephemeral=True)
            return

        # Se a lista está cheia, adiciona à espera
        if session.is_full():
            session.add_to_waitlist(interaction.user)
            await interaction.response.send_message(
                f"🔔 Lista cheia! Você foi adicionado na espera (posição {len(session.waitlist)}).", 
                ephemeral=True
            )
            await session.message.edit(embed=session.build_embed(), view=self)
        else:
            session.add_player(interaction.user)
            await interaction.response.edit_message(embed=session.build_embed(), view=self)

            if session.is_full():
                # Notifica que lista ficou cheia
                await session.message.channel.send(
                    f"🔒 **Lista completa! (10/10)**\nUse o botão \"Encerrar lista\" para finalizar ou mais pessoas podem entrar na espera."
                )

    @discord.ui.button(label="🚪 Sair", style=discord.ButtonStyle.danger, custom_id="sair")
    async def sair(self, interaction: discord.Interaction, button: discord.ui.Button):
        session = self.session

        if session.closed:
            await interaction.response.send_message("❌ Esta lista já está fechada.", ephemeral=True)
            return

        # Tenta remover da lista principal
        removed = session.remove_player(interaction.user.id)
        promoted = None
        if not removed:
            # Se não está na lista, tenta remover da espera
            removed = session.remove_from_waitlist(interaction.user.id)
            if not removed:
                await interaction.response.send_message("⚠️ Você não está na lista nem na espera.", ephemeral=True)
                return
            msg = "🔔 Você foi removido da espera."
        else:
            promoted = session.promote_waitlist()
            msg = "✅ Você saiu da lista."
            if promoted:
                msg += f"\n🔔 **{promoted.display_name}** foi promovido da espera para a lista."

        await interaction.response.send_message(msg, ephemeral=True)
        await session.message.edit(embed=session.build_embed(), view=self)

    @discord.ui.button(label="➕ Adicionar pessoa", style=discord.ButtonStyle.secondary, custom_id="adicionar")
    async def adicionar(self, interaction: discord.Interaction, button: discord.ui.Button):
        session = self.session

        if interaction.user.id != session.host.id and not interaction.user.guild_permissions.manage_messages:
            await interaction.response.send_message(
                "❌ Apenas o criador ou um administrador pode adicionar pessoas.", ephemeral=True
            )
            return

        view = AddUserView(session)
        await interaction.response.send_message(
            "Selecione o usuário para adicionar:", view=view, ephemeral=True
        )

    @discord.ui.button(label="👤 Remover pessoa", style=discord.ButtonStyle.secondary, custom_id="remover_jogador")
    async def remover_jogador(self, interaction: discord.Interaction, button: discord.ui.Button):
        session = self.session

        # Só criador
        if interaction.user.id != session.host.id:
            await interaction.response.send_message(
                "❌ Apenas quem criou a lista pode remover pessoas.", ephemeral=True
            )
            return

        if not session.players and not session.waitlist:
            await interaction.response.send_message("⚠️ Não há ninguém na lista nem na espera.", ephemeral=True)
            return

        view = RemoveView(session)
        await interaction.response.send_message(
            "Selecione quem deseja remover:", view=view, ephemeral=True
        )

    @discord.ui.button(label="🔒 Encerrar lista", style=discord.ButtonStyle.primary, custom_id="encerrar")
    async def encerrar(self, interaction: discord.Interaction, button: discord.ui.Button):
        session = self.session

        # Só criador
        if interaction.user.id != session.host.id:
            await interaction.response.send_message(
                "❌ Apenas quem criou a lista pode encerrar.", ephemeral=True
            )
            return

        # Desabilita todos os botões
        for item in self.children:
            item.disabled = True

        await interaction.response.edit_message(embed=session.build_embed(), view=self)
        await _close_session(session, interaction)


# ─────────────────────────────────────────────
#  Helpers
# ─────────────────────────────────────────────
async def _close_session(
    session: LobbySession,
    interaction: discord.Interaction,
):
    session.closed = True
    message = session.message
    active_lobbies.pop(message.id, None)

    filled = len(session.players)
    cancelled = filled < MAX_PLAYERS

    if cancelled:
        await message.channel.send(
            f"❌ **Lista cancelada!** Não foi possível reunir {MAX_PLAYERS} jogadores (só {filled})."
        )
        if session.players:
            mention_list = " ".join(p.mention for p in session.players)
            await message.channel.send(f"Jogadores que participaram: {mention_list}")
    else:
        # Lista confirmada
        mention_list = " ".join(p.mention for p in session.players)
        await message.channel.send(
            f"🔒 **Lista confirmada!** Jogadores finais ({filled}/{MAX_PLAYERS}):\n{mention_list}"
        )

        # Se houver espera, criar nova lista com os próximos
        if session.waitlist:
            # Pega os próximos 10 da espera
            next_players = session.waitlist[:MAX_PLAYERS]
            remaining_waitlist = session.waitlist[MAX_PLAYERS:]

            # Cria nova sessão com o mesmo host
            new_session = LobbySession(host=session.host)
            
            # Adiciona os primeiros 10 da espera à nova lista
            for player in next_players:
                new_session.add_player(player)
            
            # Adiciona os restantes da espera à lista de espera da nova sessão
            for player in remaining_waitlist:
                new_session.add_to_waitlist(player)

            # Envia a nova lista
            new_view = LobbyView(new_session)
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
