# -*- coding: utf-8 -*-
import discord
import logging
from core.db.lobby_repo import save_lobby_session
from domain.models import LobbySession
from core.config import ADMIN_IDS
from services.lobby_service import close_session

# Logger específico para auditoria de ações
audit_logger = logging.getLogger("Audit")

def is_authorized(user_id: int, session: LobbySession) -> bool:
    return user_id == session.host.id or user_id in ADMIN_IDS

class RemoveSelect(discord.ui.Select):
    def __init__(self, session: LobbySession, active_lobbies: dict):
        self.session = session
        self.active_lobbies = active_lobbies
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
        
        if not is_authorized(interaction.user.id, session):
            await interaction.response.send_message(
                "❌ Apenas quem criou a lista ou um administrador pode remover pessoas.", ephemeral=True
            )
            return

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
        
        # LOG DE AUDITORIA
        audit_logger.info(f"[REMOÇÃO] {interaction.user.name} ({interaction.user.id}) REMOVEU {removed_member.name if removed_member else 'Desconhecido'} ({member_id}) da {list_type} na Lista #{session.id}")

        view = LobbyView(session, self.active_lobbies)
        await session.message.edit(embed=session.build_embed(), view=view)
        save_lobby_session(session)

        name = removed_member.display_name if removed_member else "Pessoa"
        response = f"✅ **{name}** foi removido da {list_type}."
        if promoted:
            response += f"\n🔔 **{promoted.display_name}** foi promovido da espera para a lista."

        await interaction.followup.send(response, ephemeral=True)


class RemoveView(discord.ui.View):
    def __init__(self, session: LobbySession, active_lobbies: dict):
        super().__init__(timeout=30)
        self.add_item(RemoveSelect(session, active_lobbies))


class AddUserSelect(discord.ui.UserSelect):
    def __init__(self, session: LobbySession, active_lobbies: dict):
        self.session = session
        self.active_lobbies = active_lobbies
        super().__init__(placeholder="Selecione um usuário para adicionar...", custom_id="add_user_select")

    async def callback(self, interaction: discord.Interaction):
        session = self.session
        
        if not is_authorized(interaction.user.id, session) and not interaction.user.guild_permissions.manage_messages:
            await interaction.response.send_message(
                "❌ Apenas o criador ou um administrador pode adicionar pessoas.", ephemeral=True
            )
            return

        await interaction.response.defer(ephemeral=True)

        member = self.values[0]
        if member.id in session.player_ids or member.id in session.waitlist_ids:
            await interaction.followup.send("⚠️ Esse usuário já está na lista ou na espera.", ephemeral=True)
            return

        if session.is_full():
            session.add_to_waitlist(member)
            list_type = "espera"
            response = f"🔔 {member.mention} foi adicionado à espera (posição {len(session.waitlist)})."
        else:
            session.add_player(member)
            if session.is_full():
                session.schedule_auto_close(self.active_lobbies, close_fn=lambda s, l: close_session(s, l, view_factory=lambda sv, lv: LobbyView(sv, lv)))
            list_type = "lista"
            response = f"✅ {member.mention} foi adicionado à lista."

        # LOG DE AUDITORIA
        audit_logger.info(f"[ADIÇÃO] {interaction.user.name} ({interaction.user.id}) ADICIONOU {member.name} ({member.id}) à {list_type} na Lista #{session.id}")

        await session.message.edit(embed=session.build_embed(), view=LobbyView(session, self.active_lobbies))
        save_lobby_session(session)
        await interaction.followup.send(response, ephemeral=True)


class AddUserView(discord.ui.View):
    def __init__(self, session: LobbySession, active_lobbies: dict):
        super().__init__(timeout=30)
        self.add_item(AddUserSelect(session, active_lobbies))


class LobbyView(discord.ui.View):
    def __init__(self, session: LobbySession, active_lobbies: dict):
        super().__init__(timeout=None)
        self.session = session
        self.active_lobbies = active_lobbies

        if session.closed:
            for child in self.children:
                child.disabled = True

    @discord.ui.button(label="✋ Entrar", style=discord.ButtonStyle.success, custom_id="entrar")
    async def entrar(self, interaction: discord.Interaction, button: discord.ui.Button):
        session = self.session
        
        if session.closed:
            await interaction.response.send_message("❌ Esta lista já está fechada.", ephemeral=True)
            return

        if interaction.user.id in session.player_ids or interaction.user.id in session.waitlist_ids:
            await interaction.response.send_message("⚠️ Você já está na lista ou na espera!", ephemeral=True)
            return

        await interaction.response.defer()

        if session.is_full():
            session.add_to_waitlist(interaction.user)
            audit_logger.info(f"[ENTRAR] {interaction.user.name} ({interaction.user.id}) ENTROU na ESPERA da Lista #{session.id}")
            await session.message.edit(embed=session.build_embed(), view=self)
            save_lobby_session(session)
            await interaction.followup.send(
                f"🔔 Lista cheia! Você foi adicionado na espera (posição {len(session.waitlist)}).", 
                ephemeral=True
            )
        else:
            session.add_player(interaction.user)
            audit_logger.info(f"[ENTRAR] {interaction.user.name} ({interaction.user.id}) ENTROU na LISTA da Lista #{session.id}")

            if session.is_full():
                session.schedule_auto_close(self.active_lobbies, close_fn=lambda s, l: close_session(s, l, view_factory=lambda sv, lv: LobbyView(sv, lv)))

            await session.message.edit(embed=session.build_embed(), view=self)
            save_lobby_session(session)

            if session.is_full():
                await session.message.channel.send(
                    f"🔒 **Lista completa! (10/10)**"
                )

    @discord.ui.button(label="🚪 Sair", style=discord.ButtonStyle.danger, custom_id="sair")
    async def sair(self, interaction: discord.Interaction, button: discord.ui.Button):
        session = self.session
        
        if session.closed:
            await interaction.response.send_message("❌ Esta lista já está fechada.", ephemeral=True)
            return

        await interaction.response.defer()

        removed = session.remove_player(interaction.user.id)
        promoted = None
        if not removed:
            removed = session.remove_from_waitlist(interaction.user.id)
            if not removed:
                await interaction.followup.send("⚠️ Você não está na lista nem na espera.", ephemeral=True)
                return
            audit_logger.info(f"[SAIR] {interaction.user.name} ({interaction.user.id}) SAIU da ESPERA da Lista #{session.id}")
            msg = "🔔 Você foi removido da espera."
        else:
            promoted = session.promote_waitlist()
            audit_logger.info(f"[SAIR] {interaction.user.name} ({interaction.user.id}) SAIU da LISTA da Lista #{session.id}")
            msg = "✅ Você saiu da lista."
            if promoted:
                audit_logger.info(f"[PROMOÇÃO] {promoted.name} ({promoted.id}) foi PROMOVIDO da espera para a lista na Lista #{session.id}")
                msg += f"\n🔔 **{promoted.display_name}** foi promovido da espera para a lista."

        await session.message.edit(embed=session.build_embed(), view=self)
        save_lobby_session(session)
        await interaction.followup.send(msg, ephemeral=True)

    @discord.ui.button(label="➕ Adicionar pessoa", style=discord.ButtonStyle.secondary, custom_id="adicionar")
    async def adicionar(self, interaction: discord.Interaction, button: discord.ui.Button):
        session = self.session
        if not is_authorized(interaction.user.id, session) and not interaction.user.guild_permissions.manage_messages:
            await interaction.response.send_message(
                "❌ Apenas o criador ou um administrador pode adicionar pessoas.", ephemeral=True
            )
            return

        view = AddUserView(session, self.active_lobbies)
        await interaction.response.send_message(
            "Selecione o usuário para adicionar:", view=view, ephemeral=True
        )

    @discord.ui.button(label="👤 Remover pessoa", style=discord.ButtonStyle.secondary, custom_id="remover_jogador")
    async def remover_jogador(self, interaction: discord.Interaction, button: discord.ui.Button):
        session = self.session
        if not is_authorized(interaction.user.id, session):
            await interaction.response.send_message(
                "❌ Apenas quem criou a lista ou um administrador pode remover pessoas.", ephemeral=True
            )
            return

        if not session.players and not session.waitlist:
            await interaction.response.send_message("⚠️ Não há ninguém na lista nem na espera.", ephemeral=True)
            return

        view = RemoveView(session, self.active_lobbies)
        await interaction.response.send_message(
            "Selecione quem deseja remover:", view=view, ephemeral=True
        )

    @discord.ui.button(label="🔒 Encerrar lista", style=discord.ButtonStyle.primary, custom_id="encerrar")
    async def encerrar(self, interaction: discord.Interaction, button: discord.ui.Button):
        from datetime import datetime
        session = self.session

        # Verificar autorização: host/admin OU tempo de espera suficiente
        is_host_or_admin = is_authorized(interaction.user.id, session)
        can_close_by_timeout = session.can_any_user_close()

        if not is_host_or_admin and not can_close_by_timeout:
            elapsed = (datetime.now() - session.created_at).total_seconds() / 60
            remaining = session.TIMEOUT_TO_ALLOW_ANY_CLOSE_MINUTES - elapsed
            await interaction.response.send_message(
                f"❌ Apenas quem criou a lista ou um administrador pode encerrar agora.\n"
                f"⏱️ Qualquer um poderá encerrar em {remaining:.0f} minuto(s).",
                ephemeral=True
            )
            return

        await interaction.response.defer()
        
        # Log de auditoria
        if can_close_by_timeout and not is_host_or_admin:
            audit_logger.info(f"[ENCERRAR] {interaction.user.name} ({interaction.user.id}) ENCERROU a Lista #{session.id} (timeout de 10 minutos expirado)")
        else:
            audit_logger.info(f"[ENCERRAR] {interaction.user.name} ({interaction.user.id}) SOLICITOU ENCERRAMENTO da Lista #{session.id}")

        for item in self.children:
            item.disabled = True

        await session.message.edit(embed=session.build_embed(), view=self)
        await close_session(session, self.active_lobbies, view_factory=lambda s, l: LobbyView(s, l))
