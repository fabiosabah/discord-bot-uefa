# -*- coding: utf-8 -*-
import discord
import logging
from discord.ext import commands
from domain.models import LobbySession
from ui.views.lobby_view import LobbyView
from services.state import get_next_id
from core.config import ADMIN_IDS, is_admin
from core.database import get_list_channel, set_list_channel, clear_list_channel, save_lobby_session, delete_lobby_session

logger = logging.getLogger("LobbyCommands")

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

    async def _create_list(ctx: commands.Context):
        session_id = get_next_id()
        session = LobbySession(host=ctx.author, session_id=session_id)
        logger.info(f"[Comando] 🆕 NOVA LISTA CRIADA | ID: #{session.id} | Host: {ctx.author.name}#{ctx.author.id}")
        view = LobbyView(session, active_lobbies)
        msg = await ctx.send(embed=session.build_embed(), view=view)
        session.message = msg
        active_lobbies[msg.id] = session
        save_lobby_session(session)
        await ctx.message.delete()

    @bot.command(name="lista", aliases=["lobby", "inhouse"])
    async def open_list(ctx: commands.Context):
        await _cleanup_stale_lobbies()

        if active_lobbies:
            existing_session = next(iter(active_lobbies.values()))
            existing_message = existing_session.message
            channel_mention = f" no canal <#{existing_message.channel.id}>" if existing_message else ""
            reply_text = f"⚠️ Já existe uma lista aberta{channel_mention}. Veja a lista atual abaixo."
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

    @bot.command(name="forcelista", aliases=["forcelobby", "forceopen"])
    async def force_list(ctx: commands.Context):
        if not is_admin(ctx.author.id):
            await ctx.message.delete()
            await ctx.send("❌ Apenas administradores podem forçar a abertura de uma nova lista.", delete_after=8)
            return

        await _cleanup_stale_lobbies()
        if ctx.guild:
            delete_lobby_session(ctx.guild.id)

        if active_lobbies:
            for session in list(active_lobbies.values()):
                try:
                    if session.message:
                        await session.message.delete()
                except discord.HTTPException:
                    pass
            active_lobbies.clear()
            await ctx.send("⚠️ Lista anterior removida e nova lista sendo criada.", delete_after=8)
        else:
            await ctx.send("✅ Nenhuma lista ativa encontrada. Abrindo nova lista.", delete_after=8)

        await _create_list(ctx)

    @bot.command(name="uefa", aliases=["liga", "comandos"])
    async def help_command(ctx: commands.Context):
        """Exibe a lista de comandos administrativos e informações da liga."""
        
        embed = discord.Embed(
            title="📖 Guia de Comandos - UEFA Fumos League",
            description="Aqui estão os comandos disponíveis para gerenciar a liga e as listas.",
            color=discord.Color.blue()
        )

        embed.add_field(
            name="🎮 Comandos de Jogador",
            value=(
                "`!lista` ou `!lobby`: Abre uma nova lista de presença.\n"
                "`!tabela`: Mostra o ranking atual da liga.\n"
                "`!perfil @usuario`: Mostra as estatísticas de um jogador.\n"
                "`!uefa` ou `!liga`: Abre este guia de ajuda."
            ),
            inline=False
        )

        # Configuração de canal de lista
        embed.add_field(
            name="🛠️ Comandos de Configuração (Apenas ADMs)",
            value=(
                "`!registrarcanal`: Registra o canal atual como canal exclusivo para abrir listas.\n"
                "`!limparcanal`: Remove a configuração e permite abrir listas em qualquer canal.\n"
                "`!forcelista`: Força a abertura de uma nova lista mesmo que uma antiga esteja registrada.\n"
                "`!registrarcanalimagem`: Registra o canal atual para leitura de imagens OCR.\n"
                "`!limparcanalimagem`: Remove a imagem OCR registrada e volta a usar apenas o ENV ou nenhum canal.\n"
                "`!canalimagem`: Mostra o canal de imagem OCR atualmente configurado."
            ),
            inline=False
        )

        # Comandos OCR / Score (Apenas ADMs)
        embed.add_field(
            name="📸 Comandos OCR e Importação",
            value=(
                "`!scanhistory [limite]`: Enfileira imagens antigas para OCR.\n"
                "`!pendenciaimagem`: Lista imagens aguardando processamento.\n"
                "`!detalhesimagem <job_id>`: Mostra o JSON extraído e os dados OCR do job.\n"
                "`!rawtextimagem <job_id>`: Exibe o texto bruto extraído para diagnóstico.\n"
                "`!importarimagem <job_id> <mapeamento>`: Registra a imagem como partida usando o mapeamento de nomes para IDs.\n"
                "`!confirmarimagem <job_id> <texto>`: Corrige manualmente os metadados OCR.\n"
                "`!fixhero <match_id> @usuario <herói>`: Ajusta o herói de um jogador em uma partida importada.\n"
                "`!devhelp`: Obtenha documentação técnica de fluxo e esquema do banco."
            ),
            inline=False
        )

        # Comandos Administrativos
        embed.add_field(
            name="🛠️ Comandos Administrativos (Apenas ADMs)",
            value=(
                "`!venceu @u1 @u2...`: Adiciona 1 vitória (+3 pts) para os jogadores.\n"
                "`!perdeu @u1 @u2...`: Adiciona 1 derrota (-1 pt) para os jogadores.\n"
                "`!registrar @u <V> <D>`: Define manualmente o score de um jogador.\n"
                "`!registrarmatch <id> @win... -- @loss...`: Registra partida manual sem imagem.\n"
                "`!desfazer` ou `!undo`: Desfaz sua última ação de vitória/derrota.\n"
                "**Botões da Lista:** ADMs podem adicionar/remover pessoas e encerrar qualquer lista."
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
            title="🛠️ Documentação Técnica - Liga Dota",
            description="Informações detalhadas do fluxo de OCR, revisão e esquema de banco de dados.",
            color=discord.Color.dark_blue()
        )

        embed.add_field(
            name="🔍 Fluxo de verificação de imagem",
            value=(
                "1. A imagem é enviada no canal configurado ou enfileirada com `!scanhistory`.\n"
                "2. O job entra em `match_screenshots` como `pending`.\n"
                "3. O worker processa a imagem e tenta extrair texto com o LLM/Gemini.\n"
                "4. O LLM tenta retornar JSON estruturado em `match_info`/`teams`. Se não for um placar de Dota válido, o job é rejeitado.\n"
                "5. O resultado é salvo em `metadata` e o job vira `processed` ou `failed`.\n"
                "6. Admins revisam com `!detalhesimagem` e `!rawtextimagem` antes de importar."
            ),
            inline=False
        )

        embed.add_field(
            name="🗄️ Tabelas SQLite e relações",
            value=(
                "`players`: jogadores do Discord com `discord_id`, `display_name`, `wins`, `losses`.\n"
                "`player_aliases`: aliases de nicknames ligados a um mesmo `discord_id`.\n"
                "`match_screenshots`: jobs de OCR com `image_url`, `status`, `metadata`, `created_at` e `processed_at`.\n"
                "`match_imports`: partidas importadas com dados resumo e `raw_metadata` em JSON.\n"
                "`match_history`: histórico detalhado por jogador em cada `match_id`.\n"
                "`audit_log`: rastreia comandos administrativos e IDs afetados.\n"
                "`server_config`: configura canais por guild, incluindo `list_channel_id` e `image_channel_id`."
            ),
            inline=False
        )

        embed.add_field(
            name="⚙️ Comandos avançados de processo",
            value=(
                "`!detalhesimagem <job_id>`: Veja o JSON extraído e possíveis falhas de OCR.\n"
                "`!rawtextimagem <job_id>`: Verifique o texto bruto extraído do screenshot.\n"
                "`!confirmarimagem <job_id> <texto>`: Substitui metadados OCR por um JSON/texto corrigido.\n"
                "`!importarimagem <job_id> <mapeamento>`: Importa a partida após revisão.\n"
                "`!registrarcanalimagem`: Registra o canal atual para leitura de imagens OCR.\n"
                "`!limparcanalimagem`: Limpa a configuração de canal de imagem OCR.\n"
                "`!canalimagem`: Mostra o canal de imagem OCR configurado.\n"
                "`!addalias` / `!removealias`: Mapear aliases de nick para um mesmo Discord.\n"
                "`!aliases @user`: Lista os aliases registrados para o usuário."
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
