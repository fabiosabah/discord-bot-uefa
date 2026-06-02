# -*- coding: utf-8 -*-
import asyncio
import discord
import logging
from core.db.lobby_repo import save_lobby_session
from core.db.pagantes_repo import is_pagante, get_pagante_mmr
from core.db.admins_repo import is_sub_admin_db
from core.db.bot_config_repo import is_lobby_integration_enabled
from core.db.player_repo import get_steam_friend_id
from core.db.suspension_repo import is_suspended, get_suspension
from domain.models import LobbySession
from core.config import GC_API_URL
from services.lobby_service import close_session, _trigger_dota_lobby
from ui.commands.score_helpers import is_admin

# Logger específico para auditoria de ações
audit_logger = logging.getLogger("Audit")

def is_authorized(user_id: int, session: LobbySession) -> bool:
    return user_id == session.host.id or is_admin(user_id)

def is_authorized_remove(user_id: int, session: LobbySession) -> bool:
    return user_id == session.host.id or is_admin(user_id) or is_sub_admin_db(user_id)

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
        
        if not is_authorized_remove(interaction.user.id, session):
            await interaction.response.send_message(
                "❌ Apenas quem criou a lista, um administrador ou ajudante pode remover pessoas.", ephemeral=True
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

        if is_suspended(member.id):
            susp = get_suspension(member.id)
            motivo = susp["reason"] if susp else "motivo não especificado"
            remaining = susp.get("lists_remaining", "?") if susp else "?"
            await interaction.followup.send(
                f"🚫 {member.mention} está suspenso e não pode ser adicionado à lista.\n📝 Motivo: {motivo}\n⏳ {remaining} lista(s) restante(s).",
                ephemeral=True,
            )
            return

        if session.add_player(member):
            if session.is_full() and not session.dota_lobby_triggered:
                session.dota_lobby_triggered = True
                if is_lobby_integration_enabled() and GC_API_URL:
                    asyncio.create_task(_trigger_dota_lobby(session, session.message.channel))
            list_type = "lista"
            response = f"✅ {member.mention} foi adicionado à lista."
        elif session.add_to_waitlist(member):
            list_type = "espera"
            response = f"🔔 {member.mention} foi adicionado à espera (posição {len(session.waitlist)})."
        else:
            await interaction.followup.send("⚠️ Esse usuário já está na lista ou na espera.", ephemeral=True)
            return

        # LOG DE AUDITORIA
        audit_logger.info(f"[ADIÇÃO] {interaction.user.name} ({interaction.user.id}) ADICIONOU {member.name} ({member.id}) à {list_type} na Lista #{session.id}")

        await session.message.edit(embed=session.build_embed(), view=LobbyView(session, self.active_lobbies))
        save_lobby_session(session)
        await interaction.followup.send(response, ephemeral=True)


class AddUserView(discord.ui.View):
    def __init__(self, session: LobbySession, active_lobbies: dict):
        super().__init__(timeout=30)
        self.add_item(AddUserSelect(session, active_lobbies))


class ConfirmCloseView(discord.ui.View):
    def __init__(self, session: LobbySession, active_lobbies: dict, lobby_view: "LobbyView"):
        super().__init__(timeout=30)
        self.session = session
        self.active_lobbies = active_lobbies
        self.lobby_view = lobby_view

    @discord.ui.button(label="✅ Confirmar", style=discord.ButtonStyle.danger)
    async def confirm(self, interaction: discord.Interaction, button: discord.ui.Button):
        audit_logger.info(f"[ENCERRAR] {interaction.user.name} ({interaction.user.id}) CONFIRMOU encerramento da Lista #{self.session.id}")

        for item in self.lobby_view.children:
            item.disabled = True

        await self.session.message.edit(embed=self.session.build_embed(), view=self.lobby_view)
        await interaction.response.edit_message(content="🔒 Lista encerrada.", view=None)
        await self.session.message.channel.send(
            f"🔒 **Lista #{self.session.id}** foi encerrada por {interaction.user.mention}."
        )
        await close_session(self.session, self.active_lobbies, view_factory=lambda s, l: LobbyView(s, l))

    @discord.ui.button(label="❌ Cancelar", style=discord.ButtonStyle.secondary)
    async def cancel(self, interaction: discord.Interaction, button: discord.ui.Button):
        audit_logger.info(f"[ENCERRAR] {interaction.user.name} ({interaction.user.id}) CANCELOU encerramento da Lista #{self.session.id}")
        await interaction.response.edit_message(content="Encerramento cancelado.", view=None)


class LobbyView(discord.ui.View):
    def __init__(self, session: LobbySession, active_lobbies: dict):
        super().__init__(timeout=None)
        self.session = session
        self.active_lobbies = active_lobbies

        if session.closed:
            for child in self.children:
                child.disabled = True
        else:
            for child in self.children:
                if getattr(child, 'custom_id', None) == 'congelar':
                    if session.frozen:
                        child.label = "🔥 Descongelar"
                        child.style = discord.ButtonStyle.danger
                    break

    @discord.ui.button(label="✋ Entrar", style=discord.ButtonStyle.success, custom_id="entrar")
    async def entrar(self, interaction: discord.Interaction, button: discord.ui.Button):
        session = self.session
        
        if session.closed:
            await interaction.response.send_message("❌ Esta lista já está fechada.", ephemeral=True)
            return

        if session.frozen:
            await interaction.response.send_message("❄️ A lista está congelada. Aguarde.", ephemeral=True)
            return

        if interaction.user.id in session.player_ids or interaction.user.id in session.waitlist_ids:
            await interaction.response.send_message("⚠️ Você já está na lista ou na espera!", ephemeral=True)
            return

        if not is_pagante(interaction.user.id):
            await interaction.response.send_message(
                "❌ Você não está na lista de pagantes desta temporada.\n"
                "Para participar, envie um Pix de **R$ 10,00** para Smith (chave: **(71) 99146-9980**) e avise um administrador.\n\n"
                "Caso já tenha pago, peça a um administrador para atualizar a lista de pagantes no bot.",
                ephemeral=True,
            )
            return

        if get_pagante_mmr(interaction.user.id) is None:
            await interaction.response.send_message(
                "❌ Seu MMR não está registrado para esta temporada.\n"
                "Peça a um administrador para registrá-lo com `!addpagante @você <mmr>`.",
                ephemeral=True,
            )
            return

        if is_suspended(interaction.user.id):
            susp = get_suspension(interaction.user.id)
            motivo = susp["reason"] if susp else "motivo não especificado"
            remaining = susp.get("lists_remaining", "?") if susp else "?"
            await interaction.response.send_message(
                f"🚫 Você está suspenso e não pode entrar na lista.\n📝 Motivo: {motivo}\n⏳ {remaining} lista(s) restante(s).",
                ephemeral=True,
            )
            return

        if is_lobby_integration_enabled() and not get_steam_friend_id(interaction.user.id):
            await interaction.response.send_message(
                "🎮 **O bot agora te convida direto pro lobby do Dota!** Cadastre seu Steam Friend ID antes de entrar na lista.\n\n"
                "**Como encontrar seu Friend ID:**\n"
                "1. Abra o Steam\n"
                "2. Clique em **Amigos e conversas** (canto inferior direito)\n"
                "3. Clique no botão **Adicionar Amigo** (ícone de pessoa com +)\n"
                "4. Seu **Friend Code** aparecerá em destaque — clique em Copiar\n"
                "5. Digite aqui no Discord: `!meusteam <seu friend code>`",
                ephemeral=True,
            )
            return

        await interaction.response.defer()

        if session.add_player(interaction.user):
            audit_logger.info(f"[ENTRAR] {interaction.user.name} ({interaction.user.id}) ENTROU na LISTA da Lista #{session.id}")

            if session.is_full() and not session.dota_lobby_triggered:
                session.dota_lobby_triggered = True
                if is_lobby_integration_enabled() and GC_API_URL:
                    asyncio.create_task(_trigger_dota_lobby(session, session.message.channel))

            await session.message.edit(embed=session.build_embed(), view=self)
            save_lobby_session(session)

            if session.is_full():
                await session.message.channel.send(
                    "🔒 **Lista completa! (10/10)**\n\n"
                    "📋 **Regras de prioridade:**\n"
                    "- Quem já está na lista tem prioridade para permanecer.\n"
                    "- Quem ganhou a última partida tem prioridade sobre quem perdeu — válido por **1 minuto** a partir desta mensagem.\n"
                    "- Quem estiver em partida no momento em que a lista fechar será removido.\n\n"
                    "⏱️ Você tem **3 minutos** para entrar no lobby do Dota. Quem não entrar poderá ser removido da lista.\n\n"
                    "🎙️ É obrigatório estar na chamada de voz do Discord e **sem mudo**. Se não puder estar, avise alguém.\n\n"
                    "Boa sorte a todos! 🍀"
                )

        elif session.add_to_waitlist(interaction.user):
            audit_logger.info(f"[ENTRAR] {interaction.user.name} ({interaction.user.id}) ENTROU na ESPERA da Lista #{session.id}")
            await session.message.edit(embed=session.build_embed(), view=self)
            save_lobby_session(session)
            await interaction.followup.send(
                f"🔔 Lista cheia! Você foi adicionado na espera (posição {len(session.waitlist)}).",
                ephemeral=True
            )

    @discord.ui.button(label="🚪 Sair", style=discord.ButtonStyle.danger, custom_id="sair")
    async def sair(self, interaction: discord.Interaction, button: discord.ui.Button):
        session = self.session
        
        if session.closed:
            await interaction.response.send_message("❌ Esta lista já está fechada.", ephemeral=True)
            return

        if session.frozen:
            await interaction.response.send_message("❄️ A lista está congelada. Aguarde.", ephemeral=True)
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
        if not is_authorized_remove(interaction.user.id, session):
            await interaction.response.send_message(
                "❌ Apenas quem criou a lista, um administrador ou ajudante pode remover pessoas.", ephemeral=True
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
        session = self.session

        if session.frozen:
            await interaction.response.send_message("❄️ A lista está congelada. Descongele antes de encerrar.", ephemeral=True)
            return

        is_adm = is_admin(interaction.user.id)
        can_close = session.can_any_user_close()

        if not is_adm and not can_close:
            from datetime import datetime
            elapsed = (datetime.now() - session.created_at).total_seconds() / 60
            remaining_min = session.TIMEOUT_TO_ALLOW_ANY_CLOSE_MINUTES - elapsed
            hours = int(remaining_min // 60)
            minutes = int(remaining_min % 60)
            tempo = f"{hours}h{minutes:02d}min" if hours else f"{minutes} minuto(s)"
            await interaction.response.send_message(
                f"❌ Apenas administradores podem encerrar a lista agora.\n"
                f"⏱️ Qualquer um poderá encerrar em {tempo}.",
                ephemeral=True
            )
            return

        if is_adm:
            audit_logger.info(f"[ENCERRAR] {interaction.user.name} ({interaction.user.id}) solicitou encerramento da Lista #{session.id} — aguardando confirmação")
        else:
            audit_logger.info(f"[ENCERRAR] {interaction.user.name} ({interaction.user.id}) solicitou encerramento da Lista #{session.id} — timeout de 2h expirado, aguardando confirmação")

        view = ConfirmCloseView(session, self.active_lobbies, self)
        await interaction.response.send_message(
            f"⚠️ Tem certeza que deseja encerrar a **Lista #{session.id}**?",
            view=view,
            ephemeral=True
        )

    @discord.ui.button(label="❄️ Congelar", style=discord.ButtonStyle.secondary, custom_id="congelar", row=1)
    async def congelar(self, interaction: discord.Interaction, button: discord.ui.Button):
        session = self.session

        if not is_admin(interaction.user.id):
            await interaction.response.send_message(
                "❌ Apenas administradores podem congelar/descongelar a lista.",
                ephemeral=True
            )
            return

        session.frozen = not session.frozen

        if session.frozen:
            audit_logger.info(f"[CONGELAR] {interaction.user.name} ({interaction.user.id}) CONGELOU a Lista #{session.id}")
            msg = "❄️ Lista congelada. Ninguém pode entrar, sair ou encerrar."
        else:
            audit_logger.info(f"[CONGELAR] {interaction.user.name} ({interaction.user.id}) DESCONGELOU a Lista #{session.id}")
            msg = "🔥 Lista descongelada."

        save_lobby_session(session)
        await session.message.edit(embed=session.build_embed(), view=LobbyView(session, self.active_lobbies))
        await interaction.response.send_message(msg, ephemeral=True)
