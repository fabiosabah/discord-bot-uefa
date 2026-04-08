# -*- coding: utf-8 -*-
import discord
import logging
from models import LobbySession
from config import ADMIN_IDS

logger = logging.getLogger(__name__)


def is_authorized(user_id: int, session: LobbySession) -> bool:
    return user_id == session.host.id or user_id in ADMIN_IDS


# ─────────────────────────────────────────────
# Helper: atualiza a mensagem da lista
# ─────────────────────────────────────────────

async def refresh_lobby(session: LobbySession):
    """Edita a mensagem da lista com o embed e view atualizados."""
    view = LobbyView(session)
    await session.message.edit(embed=session.build_embed(), view=view)


# ─────────────────────────────────────────────
# RemoveSelect — seletor de remoção
# ─────────────────────────────────────────────

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
        logger.info(
            f"[RemoveSelect] Lista #{session.id} | "
            f"{interaction.user.name}#{interaction.user.id} selecionou quem remover"
        )

        if not is_authorized(interaction.user.id, session):
            logger.warning(
                f"[RemoveSelect] Lista #{session.id} | "
                f"{interaction.user.name} tentou remover SEM PERMISSÃO"
            )
            await interaction.response.send_message(
                "❌ Apenas quem criou a lista ou um administrador pode remover pessoas.",
                ephemeral=True,
            )
            return

        # Defer imediato para evitar timeout de 3s do Discord
        await interaction.response.defer(ephemeral=True)

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

        logger.info(
            f"[RemoveSelect] Lista #{session.id} | "
            f"{removed_member.name if removed_member else 'Unknown'} removido da {list_type} "
            f"por {interaction.user.name}"
        )

        # Atualiza a mensagem da lista (capitães incluídos via build_embed)
        await refresh_lobby(session)

        name = removed_member.display_name if removed_member else "Pessoa"
        response = f"✅ **{name}** foi removido da {list_type}."
        if promoted:
            response += f"\n🔔 **{promoted.display_name}** foi promovido da espera para a lista."

        await interaction.followup.send(response, ephemeral=True)


class RemoveView(discord.ui.View):
    def __init__(self, session: LobbySession):
        super().__init__(timeout=30)
        self.add_item(RemoveSelect(session))


# ─────────────────────────────────────────────
# AddUserSelect — seletor de adição (admin)
# ─────────────────────────────────────────────

class AddUserSelect(discord.ui.UserSelect):
    def __init__(self, session: LobbySession):
        self.session = session
        super().__init__(
            placeholder="Selecione um usuário para adicionar...",
            custom_id="add_user_select",
        )

    async def callback(self, interaction: discord.Interaction):
        session = self.session
        logger.info(
            f"[AddUserSelect] Lista #{session.id} | "
            f"{interaction.user.name}#{interaction.user.id} selecionou usuário para adicionar"
        )

        if not is_authorized(interaction.user.id, session):
            logger.warning(
                f"[AddUserSelect] Lista #{session.id} | "
                f"{interaction.user.name} tentou adicionar SEM PERMISSÃO"
            )
            await interaction.response.send_message(
                "❌ Apenas o criador ou um administrador pode adicionar pessoas.",
                ephemeral=True,
            )
            return

        member = self.values[0]

        if member.id in session.player_ids or member.id in session.waitlist_ids:
            await interaction.response.send_message(
                "⚠️ Esse usuário já está na lista ou na espera.", ephemeral=True
            )
            return

        # Defer imediato para evitar timeout de 3s do Discord
        await interaction.response.defer(ephemeral=True)

        if session.is_full():
            session.add_to_waitlist(member)
            logger.info(
                f"[AddUserSelect] Lista #{session.id} | "
                f"{member.name} ADICIONADO à ESPERA por {interaction.user.name}"
            )
            response = f"🔔 {member.mention} foi adicionado à espera (posição {len(session.waitlist)})."
        else:
            session.add_player(member)
            logger.info(
                f"[AddUserSelect] Lista #{session.id} | "
                f"{member.name} ADICIONADO à LISTA por {interaction.user.name}"
            )
            response = f"✅ {member.mention} foi adicionado à lista."

        # Atualiza a mensagem da lista (capitães incluídos via build_embed)
        await refresh_lobby(session)
        await interaction.followup.send(response, ephemeral=True)


class AddUserView(discord.ui.View):
    def __init__(self, session: LobbySession):
        super().__init__(timeout=30)
        self.add_item(AddUserSelect(session))


# ─────────────────────────────────────────────
# LobbyView — view principal com botões
# ─────────────────────────────────────────────

class LobbyView(discord.ui.View):
    def __init__(self, session: LobbySession):
        super().__init__(timeout=None)
        self.session = session

    # ── ✋ Entrar ──────────────────────────────────────────────────────────
    @discord.ui.button(label="✋ Entrar", style=discord.ButtonStyle.success, custom_id="entrar")
    async def entrar(self, interaction: discord.Interaction, button: discord.ui.Button):
        session = self.session
        logger.info(
            f"[Entrar] Lista #{session.id} | "
            f"{interaction.user.name}#{interaction.user.id} clicou em 'Entrar'"
        )

        if session.closed:
            await interaction.response.send_message("❌ Esta lista já está fechada.", ephemeral=True)
            return

        if interaction.user.id in session.player_ids:
            await interaction.response.send_message("⚠️ Você já está na lista!", ephemeral=True)
            return

        if interaction.user.id in session.waitlist_ids:
            await interaction.response.send_message("⚠️ Você já está na espera!", ephemeral=True)
            return

        # Defer imediato para evitar timeout de 3s do Discord
        await interaction.response.defer()

        if session.is_full():
            session.add_to_waitlist(interaction.user)
            logger.info(
                f"[Entrar] Lista #{session.id} | "
                f"{interaction.user.name} ENTROU na ESPERA (posição {len(session.waitlist)})"
            )
            await refresh_lobby(session)
            await interaction.followup.send(
                f"🔔 Lista cheia! Você foi adicionado na espera (posição {len(session.waitlist)}).",
                ephemeral=True,
            )
        else:
            session.add_player(interaction.user)
            logger.info(
                f"[Entrar] Lista #{session.id} | "
                f"{interaction.user.name} ENTROU na LISTA ({len(session.players)}/{10})"
            )
            await refresh_lobby(session)

            if session.is_full():
                await session.message.channel.send(
                    '🔒 **Lista completa! (10/10)**\n'
                    'Use o botão "Encerrar lista" para finalizar ou mais pessoas podem entrar na espera.'
                )

    # ── 🚪 Sair ───────────────────────────────────────────────────────────
    @discord.ui.button(label="🚪 Sair", style=discord.ButtonStyle.danger, custom_id="sair")
    async def sair(self, interaction: discord.Interaction, button: discord.ui.Button):
        session = self.session
        logger.info(
            f"[Sair] Lista #{session.id} | "
            f"{interaction.user.name}#{interaction.user.id} clicou em 'Sair'"
        )

        if session.closed:
            await interaction.response.send_message("❌ Esta lista já está fechada.", ephemeral=True)
            return

        # Defer imediato para evitar timeout de 3s do Discord
        await interaction.response.defer()

        removed = session.remove_player(interaction.user.id)
        promoted = None

        if not removed:
            removed = session.remove_from_waitlist(interaction.user.id)
            if not removed:
                logger.warning(
                    f"[Sair] Lista #{session.id} | "
                    f"{interaction.user.name} tentou sair mas NÃO estava em nenhuma lista"
                )
                await interaction.followup.send(
                    "⚠️ Você não está na lista nem na espera.", ephemeral=True
                )
                return
            logger.info(f"[Sair] Lista #{session.id} | {interaction.user.name} SAIU da ESPERA")
            msg = "🔔 Você foi removido da espera."
        else:
            promoted = session.promote_waitlist()
            logger.info(
                f"[Sair] Lista #{session.id} | "
                f"{interaction.user.name} SAIU da LISTA ({len(session.players)}/10)"
            )
            msg = "✅ Você saiu da lista."
            if promoted:
                logger.info(
                    f"[Sair] Lista #{session.id} | "
                    f"{promoted.name} foi PROMOVIDO da espera para LISTA"
                )
                msg += f"\n🔔 **{promoted.display_name}** foi promovido da espera para a lista."

        # Atualiza a mensagem da lista (capitães incluídos via build_embed)
        await refresh_lobby(session)
        await interaction.followup.send(msg, ephemeral=True)

    # ── ➕ Adicionar pessoa ────────────────────────────────────────────────
    @discord.ui.button(label="➕ Adicionar pessoa", style=discord.ButtonStyle.secondary, custom_id="adicionar")
    async def adicionar(self, interaction: discord.Interaction, button: discord.ui.Button):
        session = self.session
        logger.info(
            f"[Adicionar] Lista #{session.id} | "
            f"{interaction.user.name}#{interaction.user.id} clicou em 'Adicionar pessoa'"
        )

        if not is_authorized(interaction.user.id, session):
            logger.warning(
                f"[Adicionar] Lista #{session.id} | "
                f"{interaction.user.name} tentou adicionar SEM PERMISSÃO"
            )
            await interaction.response.send_message(
                "❌ Apenas o criador ou um administrador pode adicionar pessoas.", ephemeral=True
            )
            return

        view = AddUserView(session)
        await interaction.response.send_message(
            "Selecione o usuário para adicionar:", view=view, ephemeral=True
        )

    # ── 👤 Remover pessoa ─────────────────────────────────────────────────
    @discord.ui.button(label="👤 Remover pessoa", style=discord.ButtonStyle.secondary, custom_id="remover_jogador")
    async def remover_jogador(self, interaction: discord.Interaction, button: discord.ui.Button):
        session = self.session
        logger.info(
            f"[Remover Pessoa] Lista #{session.id} | "
            f"{interaction.user.name}#{interaction.user.id} clicou em 'Remover pessoa'"
        )

        if not is_authorized(interaction.user.id, session):
            logger.warning(
                f"[Remover Pessoa] Lista #{session.id} | "
                f"{interaction.user.name} tentou remover SEM PERMISSÃO"
            )
            await interaction.response.send_message(
                "❌ Apenas quem criou a lista ou um administrador pode remover pessoas.",
                ephemeral=True,
            )
            return

        if not session.players and not session.waitlist:
            await interaction.response.send_message(
                "⚠️ Não há ninguém na lista nem na espera.", ephemeral=True
            )
            return

        view = RemoveView(session)
        await interaction.response.send_message(
            "Selecione quem deseja remover:", view=view, ephemeral=True
        )

    # ── 🔒 Encerrar lista ─────────────────────────────────────────────────
    @discord.ui.button(label="🔒 Encerrar lista", style=discord.ButtonStyle.primary, custom_id="encerrar")
    async def encerrar(self, interaction: discord.Interaction, button: discord.ui.Button):
        from helpers import close_session
        session = self.session
        logger.info(
            f"[Encerrar] Lista #{session.id} | "
            f"{interaction.user.name}#{interaction.user.id} clicou em 'Encerrar lista' | "
            f"Jogadores: {len(session.players)}/10"
        )

        if not is_authorized(interaction.user.id, session):
            logger.warning(
                f"[Encerrar] Lista #{session.id} | "
                f"{interaction.user.name} tentou encerrar SEM PERMISSÃO"
            )
            await interaction.response.send_message(
                "❌ Apenas quem criou a lista ou um administrador pode encerrar.",
                ephemeral=True,
            )
            return

        # Defer imediato para evitar timeout de 3s do Discord
        await interaction.response.defer()

        # Desabilita todos os botões
        for item in self.children:
            item.disabled = True

        await session.message.edit(embed=session.build_embed(), view=self)
        await close_session(session, interaction)