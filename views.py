import discord
import logging
from models import LobbySession
from config import ADMIN_IDS

logger = logging.getLogger(__name__)

def is_authorized(user_id: int, session: LobbySession) -> bool:
    return user_id == session.host.id or user_id in ADMIN_IDS

class RemoveSelect(discord.ui.Select):
    def __init__(self, session: LobbySession):
        self.session = session
        options = []

        for p in session.players:
            options.append(discord.SelectOption(label=f"📋 {p.display_name}", value=f"player_{p.id}"))

        for p in session.waitlist:
            options.append(discord.SelectOption(label=f"🔔 {p.display_name}", value=f"waitlist_{p.id}"))

        super().__init__(
            placeholder="Selecione quem remover...",
            options=options,
            custom_id="remove_select",
        )

    async def callback(self, interaction: discord.Interaction):
        session = self.session
        logger.info(f"[RemoveSelect] Lista #{session.id} | {interaction.user.name}#{interaction.user.id} selecionou quem remover | Host: {session.host.name}#{session.host.id}")

        if not is_authorized(interaction.user.id, session):
            logger.warning(f"[RemoveSelect] Lista #{session.id} | {interaction.user.name}#{interaction.user.id} tentou remover SEM PERMISSÃO")
            await interaction.response.send_message(
                "❌ Apenas quem criou a lista ou um administrador pode remover pessoas.", ephemeral=True
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
        
        logger.info(f"[RemoveSelect] Lista #{session.id} | {removed_member.name if removed_member else 'Unknown'} removido da {list_type} por {interaction.user.name}")

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
        logger.info(f"[AddUserSelect] Lista #{session.id} | {interaction.user.name}#{interaction.user.id} selecionou um usuário | Host: {session.host.name}#{session.host.id}")

        if not is_authorized(interaction.user.id, session) and not interaction.user.guild_permissions.manage_messages:
            logger.warning(f"[AddUserSelect] Lista #{session.id} | {interaction.user.name}#{interaction.user.id} tentou adicionar SEM PERMISSÃO")
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
            logger.info(f"[AddUserSelect] Lista #{session.id} | {member.name}#{member.id} ADICIONADO à ESPERA por {interaction.user.name}")
            response = f"🔔 {member.mention} foi adicionado à espera (posição {len(session.waitlist)})."
        else:
            session.add_player(member)
            logger.info(f"[AddUserSelect] Lista #{session.id} | {member.name}#{member.id} ADICIONADO à LISTA por {interaction.user.name}")
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
        logger.info(f"[Entrar] Lista #{session.id} | {interaction.user.name}#{interaction.user.id} clicou em 'Entrar' | Host: {session.host.name}#{session.host.id}")

        if session.closed:
            logger.warning(f"[Entrar] Lista #{session.id} | {interaction.user.name}#{interaction.user.id} tentou entrar em lista FECHADA")
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
            logger.info(f"[Entrar] Lista #{session.id} | {interaction.user.name}#{interaction.user.id} ENTROU na ESPERA (posição {len(session.waitlist)})")
            await interaction.response.send_message(
                f"🔔 Lista cheia! Você foi adicionado na espera (posição {len(session.waitlist)}).", 
                ephemeral=True
            )
            await session.message.edit(embed=session.build_embed(), view=self)
        else:
            session.add_player(interaction.user)
            logger.info(f"[Entrar] Lista #{session.id} | {interaction.user.name}#{interaction.user.id} ENTROU na LISTA ({len(session.players)}/10)")
            await interaction.response.edit_message(embed=session.build_embed(), view=self)

            if session.is_full():
                # Notifica que lista ficou cheia
                await session.message.channel.send(
                    f"🔒 **Lista completa! (10/10)**\nUse o botão \"Encerrar lista\" para finalizar ou mais pessoas podem entrar na espera."
                )

    @discord.ui.button(label="🚪 Sair", style=discord.ButtonStyle.danger, custom_id="sair")
    async def sair(self, interaction: discord.Interaction, button: discord.ui.Button):
        session = self.session
        logger.info(f"[Sair] Lista #{session.id} | {interaction.user.name}#{interaction.user.id} clicou em 'Sair' | Host: {session.host.name}#{session.host.id}")

        if session.closed:
            logger.warning(f"[Sair] Lista #{session.id} | {interaction.user.name}#{interaction.user.id} tentou sair de lista FECHADA")
            await interaction.response.send_message("❌ Esta lista já está fechada.", ephemeral=True)
            return

        # Tenta remover da lista principal
        removed = session.remove_player(interaction.user.id)
        promoted = None
        if not removed:
            # Se não está na lista, tenta remover da espera
            removed = session.remove_from_waitlist(interaction.user.id)
            if not removed:
                logger.warning(f"[Sair] Lista #{session.id} | {interaction.user.name}#{interaction.user.id} tentou sair mas NÃO estava em nenhuma lista")
                await interaction.response.send_message("⚠️ Você não está na lista nem na espera.", ephemeral=True)
                return
            logger.info(f"[Sair] Lista #{session.id} | {interaction.user.name}#{interaction.user.id} SAIU da ESPERA")
            msg = "🔔 Você foi removido da espera."
        else:
            promoted = session.promote_waitlist()
            logger.info(f"[Sair] Lista #{session.id} | {interaction.user.name}#{interaction.user.id} SAIU da LISTA ({len(session.players)}/10)")
            msg = "✅ Você saiu da lista."
            if promoted:
                logger.info(f"[Sair] Lista #{session.id} | {promoted.name}#{promoted.id} foi PROMOVIDO da espera para LISTA")
                msg += f"\n🔔 **{promoted.display_name}** foi promovido da espera para a lista."

        await interaction.response.send_message(msg, ephemeral=True)
        await session.message.edit(embed=session.build_embed(), view=self)

    @discord.ui.button(label="➕ Adicionar pessoa", style=discord.ButtonStyle.secondary, custom_id="adicionar")
    async def adicionar(self, interaction: discord.Interaction, button: discord.ui.Button):
        session = self.session
        logger.info(f"[Adicionar] Lista #{session.id} | {interaction.user.name}#{interaction.user.id} clicou em 'Adicionar pessoa' | Host: {session.host.name}#{session.host.id}")

        if not is_authorized(interaction.user.id, session) and not interaction.user.guild_permissions.manage_messages:
            logger.warning(f"[Adicionar] Lista #{session.id} | {interaction.user.name}#{interaction.user.id} tentou adicionar SEM PERMISSÃO")
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
        logger.info(f"[Remover Pessoa] Lista #{session.id} | {interaction.user.name}#{interaction.user.id} clicou em 'Remover pessoa' | Host: {session.host.name}#{session.host.id}")

        if not is_authorized(interaction.user.id, session):
            logger.warning(f"[Remover Pessoa] Lista #{session.id} | {interaction.user.name}#{interaction.user.id} tentou remover SEM PERMISSÃO")
            await interaction.response.send_message(
                "❌ Apenas quem criou a lista ou um administrador pode remover pessoas.", ephemeral=True
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
        from helpers import close_session
        
        session = self.session
        logger.info(f"[Encerrar] Lista #{session.id} | {interaction.user.name}#{interaction.user.id} clicou em 'Encerrar lista' | Host: {session.host.name}#{session.host.id} | Jogadores: {len(session.players)}/10")

        if not is_authorized(interaction.user.id, session):
            logger.warning(f"[Encerrar] Lista #{session.id} | {interaction.user.name}#{interaction.user.id} tentou encerrar SEM PERMISSÃO")
            await interaction.response.send_message(
                "❌ Apenas quem criou a lista ou um administrador pode encerrar.", ephemeral=True
            )
            return

        # Desabilita todos os botões
        for item in self.children:
            item.disabled = True

        await interaction.response.edit_message(embed=session.build_embed(), view=self)
        await close_session(session, interaction)
