# -*- coding: utf-8 -*-
import discord
import logging
from datetime import datetime
from discord.ext import commands
from domain.models import LobbySession
from ui.views.lobby_view import LobbyView
from services.state import get_next_id
from core.config import ADMIN_IDS, is_admin
from core.database import get_list_channel, set_list_channel, clear_list_channel, save_lobby_session, delete_lobby_session, get_lobby_sessions

logger = logging.getLogger("LobbyCommands")

class PartialMember:
    def __init__(self, id: int, display_name: str):
        self.id = id
        self.display_name = display_name
        self.name = display_name

    @property
    def mention(self):
        return f"<@{self.id}>"

def setup_lobby_commands(bot: commands.Bot, active_lobbies: dict):
    async def _cleanup_stale_lobbies():
        stale_ids = []
        for msg_id, session in list(active_lobbies.items()):
            if session.closed or session.message is None:
                stale_ids.append(msg_id)
                continue

            try:
                await session.message.channel.fetch_message(session.message.id)
            except (discord.NotFound, discord.Forbidden, discord.HTTPException):
                stale_ids.append(msg_id)

        for msg_id in stale_ids:
            session = active_lobbies.pop(msg_id, None)
            if session and session.message and session.message.guild:
                delete_lobby_session(session.message.guild.id)

    async def _resolve_member(guild: discord.Guild, member_id: int):
        member = guild.get_member(member_id)
        if member:
            return member
        try:
            return await guild.fetch_member(member_id)
        except discord.NotFound:
            return PartialMember(member_id, f"Usuário {member_id}")

    async def _try_restore_saved_guild_lobby(ctx: commands.Context):
        if not ctx.guild:
            return None

        saved_sessions = get_lobby_sessions()
        row = next((r for r in saved_sessions if r["guild_id"] == ctx.guild.id), None)
        if not row:
            return None

        if any(
            session.message and session.message.guild and session.message.guild.id == ctx.guild.id
            for session in active_lobbies.values()
        ):
            return None

        try:
            channel = bot.get_channel(row["channel_id"]) or await bot.fetch_channel(row["channel_id"])
        except (discord.NotFound, discord.Forbidden, discord.HTTPException):
            delete_lobby_session(ctx.guild.id)
            return None

        if channel is None:
            delete_lobby_session(ctx.guild.id)
            return None

        try:
            message = await channel.fetch_message(row["message_id"])
        except (discord.NotFound, discord.Forbidden, discord.HTTPException):
            delete_lobby_session(ctx.guild.id)
            return None

        host = await _resolve_member(ctx.guild, row["host_id"])
        session = LobbySession(host=host, session_id=row["session_id"])
        session.message = message
        session.players = [await _resolve_member(ctx.guild, pid) for pid in row["player_ids"]]
        session.player_ids = set(row["player_ids"])
        session.waitlist = [await _resolve_member(ctx.guild, wid) for wid in row["waitlist_ids"]]
        session.waitlist_ids = set(row["waitlist_ids"])
        session.closed = bool(row["closed"])
        session.auto_close_at = datetime.fromisoformat(row["auto_close_at"]) if row["auto_close_at"] else None

        if session.closed:
            delete_lobby_session(ctx.guild.id)
            return None

        active_lobbies[message.id] = session
        await message.edit(view=LobbyView(session, active_lobbies))

        if session.auto_close_at or session.is_full():
            session.schedule_auto_close(active_lobbies)

        return session

    class ConfirmNewListView(discord.ui.View):
        def __init__(self, ctx: commands.Context, old_session: LobbySession):
            super().__init__(timeout=60)
            self.ctx = ctx
            self.old_session = old_session

        async def interaction_check(self, interaction: discord.Interaction) -> bool:
            if interaction.user.id != self.ctx.author.id:
                await interaction.response.send_message(
                    "❌ Apenas quem executou o comando pode confirmar a criação da nova lista.",
                    ephemeral=True
                )
                return False
            return True

        @discord.ui.button(label="Criar nova lista vazia", style=discord.ButtonStyle.danger, custom_id="confirm_new_list")
        async def confirm(self, interaction: discord.Interaction, button: discord.ui.Button):
            guild_id = self.ctx.guild.id if self.ctx.guild else None
            if guild_id is not None:
                delete_lobby_session(guild_id)

            if self.old_session.message:
                try:
                    await self.old_session.message.delete()
                except discord.HTTPException:
                    pass

            for msg_id, session in list(active_lobbies.items()):
                if session.message and session.message.guild and self.ctx.guild and session.message.guild.id == self.ctx.guild.id:
                    active_lobbies.pop(msg_id, None)

            for child in self.children:
                child.disabled = True

            await interaction.response.edit_message(
                content="⚠️ Lista anterior removida. Criando nova lista vazia...",
                view=self
            )
            await _create_list(self.ctx)

        @discord.ui.button(label="Recriar lista atual", style=discord.ButtonStyle.secondary, custom_id="cancel_new_list")
        async def cancel(self, interaction: discord.Interaction, button: discord.ui.Button):
            guild_id = self.ctx.guild.id if self.ctx.guild else None
            if guild_id is not None:
                delete_lobby_session(guild_id)

            if self.old_session.message:
                try:
                    await self.old_session.message.delete()
                except discord.HTTPException:
                    pass

            for msg_id, session in list(active_lobbies.items()):
                if session.message and session.message.guild and self.ctx.guild and session.message.guild.id == self.ctx.guild.id:
                    active_lobbies.pop(msg_id, None)

            for child in self.children:
                child.disabled = True

            await interaction.response.edit_message(
                content="🔄 Recriando a lista anterior com os mesmos jogadores...",
                view=self
            )
            await _create_list(self.ctx, self.old_session)

    async def _create_list(ctx: commands.Context, previous_session: LobbySession | None = None):
        session_id = get_next_id()
        session = LobbySession(host=ctx.author, session_id=session_id)

        if previous_session is not None:
            for player in previous_session.players:
                session.add_player(player)
            for waiting in previous_session.waitlist:
                session.add_to_waitlist(waiting)

        logger.info(f"[Comando] 🆕 NOVA LISTA CRIADA | ID: #{session.id} | Host: {ctx.author.name}#{ctx.author.id}")
        view = LobbyView(session, active_lobbies)
        msg = await ctx.send(embed=session.build_embed(), view=view)
        session.message = msg
        active_lobbies[msg.id] = session
        save_lobby_session(session)
        try:
            await ctx.message.delete()
        except discord.errors.NotFound:
            pass

    @bot.command(name="lista", aliases=["lobby", "inhouse"])
    async def open_list(ctx: commands.Context):
        await _cleanup_stale_lobbies()

        restored_session = await _try_restore_saved_guild_lobby(ctx)
        if restored_session:
            existing_session = restored_session
        else:
            existing_session = next(
                (session for session in active_lobbies.values()
                 if session.message and session.message.guild and ctx.guild and session.message.guild.id == ctx.guild.id),
                None
            )

        if existing_session and existing_session.closed:
            existing_session = None

        if existing_session:
            existing_message = existing_session.message
            channel_mention = f" no canal <#{existing_message.channel.id}>" if existing_message else ""
            reply_text = f"⚠️ Já existe uma lista aberta{channel_mention}. Veja a lista atual abaixo."

            if is_admin(ctx.author.id):
                prompt_text = (
                    f"⚠️ Já existe uma lista aberta{channel_mention}. "
                    "Escolha se deseja criar uma nova lista vazia ou recriar a lista atual com os mesmos jogadores."
                )
                view = ConfirmNewListView(ctx, existing_session)
                if existing_message and existing_message.channel.id == ctx.channel.id:
                    try:
                        await ctx.send(prompt_text, reference=existing_message.to_reference(), view=view)
                    except discord.HTTPException:
                        await ctx.send(prompt_text, view=view)
                elif existing_message:
                    await ctx.send(f"{prompt_text}\nAcesse: {existing_message.jump_url}", view=view)
                else:
                    await ctx.send(prompt_text, view=view)
                await ctx.message.delete()
                return

            if existing_message and existing_message.channel.id == ctx.channel.id:
                try:
                    await ctx.send(reply_text, reference=existing_message.to_reference())
                except discord.HTTPException:
                    await ctx.send(reply_text)
            elif existing_message:
                reply_text = (
                    f"⚠️ Já existe uma lista aberta{channel_mention}. "
                    f"Acesse: {existing_message.jump_url}"
                )
                await ctx.send(reply_text)
            else:
                await ctx.send(reply_text)
            await ctx.message.delete()
            return

        guild = ctx.guild
        if guild:
            allowed_channel = get_list_channel(guild.id)
            if allowed_channel and ctx.channel.id != allowed_channel:
                await ctx.send(
                    f"❌ Lista só pode ser aberta no canal <#{allowed_channel}>.", delete_after=10
                )
                await ctx.message.delete()
                return

        await _create_list(ctx)

    @bot.command(name="uefa", aliases=["liga", "comandos"])
    async def help_command(ctx: commands.Context):
        """Exibe a lista de comandos e informações da liga."""

        embed = discord.Embed(
            title="📖 Guia de Comandos — UEFA Fumos League",
            description="Todos os comandos disponíveis para jogadores e administradores.",
            color=discord.Color.blue()
        )

        embed.add_field(
            name="🎮 Jogadores",
            value=(
                "`!lista` / `!lobby` — Abre uma lista de presença para o inhouse.\n"
                "`!tabela` — Ranking OCR com streaks atuais, recordes e top/bottom heróis do campeonato.\n"
                "`!tabela1` — Ranking de pontuação manual (vitórias/derrotas registradas por ADM).\n"
                "`!perfil @jogador` — Estatísticas detalhadas: winrate, heróis favoritos, adversários.\n"
                "`!ultimas @jogador` — Últimas 30 partidas OCR com herói e KDA.\n"
                "`!heroes` — Pool de heróis do campeonato: total de picks e winrate de cada um.\n"
                "`!heroes <nome>` — Todas as partidas em que um herói específico foi jogado.\n"
                "`!uefa` / `!liga` / `!comandos` — Exibe este guia."
            ),
            inline=False
        )

        embed.add_field(
            name="📸 OCR e Importação de Partidas (ADMs)",
            value=(
                "`!ok <id> [MM:SS]` — Aprova e importa uma partida OCR. Se a duração não foi detectada automaticamente, informe no formato `5:23`.\n"
                "`!cadastro <nick> @jogador` — Mapeia um nick de jogo (como aparece no placar) a um jogador do Discord.\n"
                "`!fixhero <match_id> <slot> <herói>` — Corrige o herói de um slot em uma partida já importada.\n"
                "`!apagarid <id>` — Remove uma partida importada recentemente (limite: **1 por dia**, apenas nas últimas 24h).\n"
                "`!registrarcanalimagem` — Define o canal atual como canal de recebimento de screenshots.\n"
                "`!pendenciaimagem` — Lista jobs OCR aguardando aprovação.\n"
                "`!detalhesimagem <id>` — Mostra os dados extraídos de um job OCR.\n"
                "`!scanhistory [n]` — Reenfileira as últimas *n* imagens do canal para reprocessamento OCR."
            ),
            inline=False
        )

        embed.add_field(
            name="📊 Partidas Manuais (ADMs)",
            value=(
                "`!venceu @u1 @u2 ...` — Registra vitória (+3 pts) para cada jogador mencionado.\n"
                "`!perdeu @u1 @u2 ...` — Registra derrota (-1 pt) para cada jogador mencionado.\n"
                "`!registrar @jogador <V> <D>` — Define vitórias e derrotas manualmente para um jogador.\n"
                "`!registrarmatch <id> @venc... -- @derrot...` — Registra uma partida completa sem imagem.\n"
                "`!desfazer` / `!undo` — Desfaz a última ação de vitória/derrota.\n"
                "**Botões da Lista:** ADMs podem adicionar/remover jogadores e encerrar qualquer lista."
            ),
            inline=False
        )

        embed.add_field(
            name="⚙️ Configuração (ADMs)",
            value=(
                "`!registrarcanal` — Define o canal atual como exclusivo para abertura de listas.\n"
                "`!limparcanal` — Remove a restrição; listas podem ser abertas em qualquer canal.\n"
                "`!limparcanalimagem` — Remove a configuração de canal OCR.\n"
                "`!canalimagem` — Mostra o canal OCR configurado atualmente.\n"
                "`!devhelp` — Documentação técnica do fluxo OCR e schema do banco de dados."
            ),
            inline=False
        )

        admin_mentions = [f"<@{admin_id}>" for admin_id in ADMIN_IDS]
        admins_text = ", ".join(admin_mentions) if admin_mentions else "Nenhum administrador configurado no .env"

        embed.add_field(
            name="👑 Administradores da Liga",
            value=f"Os seguintes usuários têm permissão administrativa:\n{admins_text}",
            inline=False
        )

        embed.set_footer(text="Dúvidas? Entre em contato com um administrador.")

        await ctx.send(embed=embed)

    @bot.command(name="devhelp", aliases=["debughelp", "devdocs", "techhelp"])
    async def dev_help_command(ctx: commands.Context):
        if not is_admin(ctx.author.id):
            await ctx.message.delete()
            await ctx.send("❌ Apenas administradores podem acessar a ajuda técnica.", delete_after=8)
            return

        embed = discord.Embed(
            title="🛠️ Documentação Técnica — Liga Dota",
            description="Fluxo OCR completo, schema do banco e comandos avançados.",
            color=discord.Color.dark_blue()
        )

        embed.add_field(
            name="🔍 Fluxo OCR — passo a passo",
            value=(
                "1. Upload de screenshot no canal OCR configurado (ou `!scanhistory` para reenfileirar imagens antigas).\n"
                "2. Job entra em `match_screenshots` como `pending`.\n"
                "3. Worker processa com Gemini/LLM e extrai JSON estruturado.\n"
                "4. Se não for placar Dota válido → job rejeitado, nenhuma mensagem enviada.\n"
                "5. Resumo enviado no canal — **auto-apagado em 2 min**, revise antes que suma!\n"
                "6. Corrija erros com `!ocrhero`, `!ocrnick`, `!setjobwinner` ou `!cadastro`.\n"
                "7. Importe com `!ok <id>` — ou `!ok <id> MM:SS` se duração não foi detectada.\n"
                "8. Após importar, consulte os dados com `!detalhesimagem <id>`."
            ),
            inline=False
        )

        embed.add_field(
            name="🗄️ Tabelas SQLite",
            value=(
                "`players` — discord_id, display_name, wins, losses (score manual).\n"
                "`player_aliases` — mapeamento nick_jogo → discord_id.\n"
                "`matches` — league_match_id, winner_team, duration, created_at.\n"
                "`match_players` — stats por jogador: discord_id, hero_name, k/d/a, team.\n"
                "`heroes` — lista canônica de heróis (referência para LEFT JOIN).\n"
                "`match_screenshots` — jobs OCR: image_url, status, metadata, created_at.\n"
                "`audit_log` — log de ações admin (import, delete, etc.).\n"
                "`server_config` — configurações por servidor: list_channel_id, image_channel_id."
            ),
            inline=False
        )

        embed.add_field(
            name="⚙️ Comandos avançados",
            value=(
                "`!detalhesimagem <id>` — JSON extraído do job OCR.\n"
                "`!rawtextimagem <id>` — Texto bruto extraído pelo OCR.\n"
                "`!pendenciaimagem` — Lista jobs aguardando revisão/aprovação.\n"
                "`!ocrhero <id> <slot> <herói>` — Corrige herói **antes** de importar.\n"
                "`!ocrnick <id> <slot> <nick>` — Corrige nick **antes** de importar.\n"
                "`!setjobwinner <id> radiant|dire` — Define vencedor **antes** de importar.\n"
                "`!cadastro <nick> @user` — Mapeia nick de jogo a um Discord.\n"
                "`!fixhero <match_id> <slot> <herói>` — Corrige herói **após** importar.\n"
                "`!apagarid <id>` — Remove partida importada (limite: 1/dia, 24h).\n"
                "`!addalias` / `!removealias` / `!aliases @user` — Gerencia aliases de nick."
            ),
            inline=False
        )

        await ctx.send(embed=embed)

    @bot.command(name="registrarcanal")
    async def register_channel(ctx: commands.Context):
        if not is_admin(ctx.author.id):
            await ctx.message.delete()
            await ctx.send("❌ Apenas administradores podem registrar o canal.", delete_after=8)
            return

        if not ctx.guild:
            await ctx.message.delete()
            await ctx.send("❌ Este comando só pode ser usado em um servidor.", delete_after=8)
            return

        set_list_channel(ctx.guild.id, ctx.channel.id)
        await ctx.send(
            f"✅ Canal <#{ctx.channel.id}> registrado como canal exclusivo para abrir listas.", delete_after=15
        )
        await ctx.message.delete()

    @bot.command(name="limparcanal")
    async def clear_channel(ctx: commands.Context):
        if not is_admin(ctx.author.id):
            await ctx.message.delete()
            await ctx.send("❌ Apenas administradores podem limpar o canal registrado.", delete_after=8)
            return

        if not ctx.guild:
            await ctx.message.delete()
            await ctx.send("❌ Este comando só pode ser usado em um servidor.", delete_after=8)
            return

        allowed_channel = get_list_channel(ctx.guild.id)
        if not allowed_channel:
            await ctx.message.delete()
            await ctx.send("⚠️ Não há canal de lista registrado.", delete_after=10)
            return

        clear_list_channel(ctx.guild.id)
        await ctx.send("✅ Configuração de canal apagada. Listas agora podem ser abertas em qualquer canal.", delete_after=15)
        await ctx.message.delete()
