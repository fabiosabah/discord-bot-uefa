# -*- coding: utf-8 -*-
import discord
import logging
from discord.ext import commands
from ui.commands.score_helpers import is_admin

logger = logging.getLogger("DotaGCCommands")


def setup_dota_gc_commands(bot: commands.Bot) -> None:
    try:
        from gc_client import (
            get_gc_manager,
            GAME_MODE_LABELS,
            REGION_LABELS,
            GAME_MODES,
            SERVER_REGIONS,
        )
    except ImportError:
        logger.warning("[GC] Pacotes steam/dota2 não instalados — comandos GC desativados.")
        return

    _MODO_OPCOES = " | ".join(f"`{k}`" for k in GAME_MODES)
    _REGIAO_OPCOES = " | ".join(f"`{k}`" for k in SERVER_REGIONS)

    @bot.command(name="criarlobby", aliases=["crlobby", "novolobby", "gclobby"])
    async def create_dota_lobby(
        ctx: commands.Context,
        nome: str = None,
        senha: str = None,
        modo: str = "ap",
        regiao: str = "sa",
    ):
        gc = get_gc_manager()
        if gc is None:
            await ctx.send(
                "❌ Game Coordinator não configurado. Configure `STEAM_USERNAME` e `STEAM_PASSWORD` no `.env`.",
                delete_after=15,
            )
            return

        if not gc.is_ready:
            await ctx.send(
                "⏳ Game Coordinator ainda está conectando ao Steam. Aguarde alguns segundos e tente novamente.",
                delete_after=15,
            )
            return

        if gc.current_lobby:
            lb = gc.current_lobby
            await ctx.send(
                f"⚠️ Já existe um lobby ativo: **{lb.name}** — senha `{lb.password}`.\n"
                f"Use `!fecharlobby` para encerrar o lobby atual antes de criar um novo.",
                delete_after=20,
            )
            return

        if modo.lower() not in GAME_MODES:
            await ctx.send(f"❌ Modo inválido `{modo}`. Opções: {_MODO_OPCOES}", delete_after=12)
            return

        if regiao.lower() not in SERVER_REGIONS:
            await ctx.send(f"❌ Região inválida `{regiao}`. Opções: {_REGIAO_OPCOES}", delete_after=12)
            return

        guild_name = ctx.guild.name if ctx.guild else "Liga"
        lobby_name = nome or f"{guild_name[:20]} Inhouse"
        lobby_password = senha or "fumos"

        status_msg = await ctx.send("🎮 Criando lobby no Dota 2, aguarde...")

        lobby = await gc.create_lobby(lobby_name, lobby_password, modo, regiao)

        if lobby is None:
            await status_msg.edit(content="❌ Falha ao criar o lobby. O GC não respondeu a tempo — tente novamente.")
            return

        mode_label = GAME_MODE_LABELS.get(modo.lower(), modo)
        region_label = REGION_LABELS.get(regiao.lower(), regiao)

        embed = discord.Embed(title="🎮 Lobby Dota 2 Criado!", color=discord.Color.green())
        embed.add_field(name="Nome", value=lobby.name, inline=True)
        embed.add_field(name="Senha", value=f"`{lobby.password}`", inline=True)
        embed.add_field(name="Modo de Jogo", value=mode_label, inline=True)
        embed.add_field(name="Servidor", value=region_label, inline=True)
        embed.add_field(
            name="Como entrar",
            value=(
                "1. Abra o **Dota 2**\n"
                "2. Clique em **Jogar** → **Lobbies Personalizados**\n"
                f"3. Procure: **{lobby.name}**\n"
                f"4. Senha: `{lobby.password}`"
            ),
            inline=False,
        )
        embed.set_footer(text=f"Lobby criado por {ctx.author.display_name}")

        await status_msg.edit(content=None, embed=embed)
        try:
            await ctx.message.delete()
        except discord.HTTPException:
            pass

        logger.info(f"[GC] Lobby criado por {ctx.author} | nome={lobby.name} | modo={modo} | regiao={regiao}")

    @bot.command(name="fecharlobby", aliases=["deletarlobby", "closelobby", "gcfechar"])
    async def destroy_dota_lobby(ctx: commands.Context):
        if not is_admin(ctx.author.id):
            await ctx.send("❌ Apenas administradores podem fechar o lobby.", delete_after=8)
            return

        gc = get_gc_manager()
        if gc is None or gc.current_lobby is None:
            await ctx.send("⚠️ Não há lobby ativo para encerrar.", delete_after=10)
            try:
                await ctx.message.delete()
            except discord.HTTPException:
                pass
            return

        lobby_name = gc.current_lobby.name
        success = await gc.destroy_lobby()

        if success:
            await ctx.send(f"✅ Lobby **{lobby_name}** encerrado.", delete_after=15)
        else:
            await ctx.send("❌ Falha ao encerrar o lobby.", delete_after=10)

        try:
            await ctx.message.delete()
        except discord.HTTPException:
            pass

    @bot.command(name="statusgc", aliases=["gcstatus", "lobbyinfo", "statuslobby"])
    async def gc_status(ctx: commands.Context):
        gc = get_gc_manager()

        embed = discord.Embed(title="📡 Dota 2 Game Coordinator", color=discord.Color.blue())

        if gc is None:
            embed.add_field(name="Status", value="⚫ Não configurado", inline=False)
            await ctx.send(embed=embed, delete_after=20)
            return

        embed.add_field(name="GC", value="🟢 Conectado" if gc.is_ready else "🔴 Conectando...", inline=False)

        if gc.current_lobby:
            lb = gc.current_lobby
            embed.add_field(name="Lobby Ativo", value=lb.name, inline=True)
            embed.add_field(name="Senha", value=f"`{lb.password}`", inline=True)
            embed.add_field(name="Modo", value=GAME_MODE_LABELS.get(lb.game_mode, lb.game_mode), inline=True)
            embed.add_field(name="Servidor", value=REGION_LABELS.get(lb.server_region, lb.server_region), inline=True)
            if lb.members:
                embed.add_field(
                    name=f"Jogadores no lobby ({len(lb.members)})",
                    value="\n".join(lb.members) or "—",
                    inline=False,
                )
        else:
            embed.add_field(name="Lobby Ativo", value="Nenhum", inline=False)
            if gc.is_ready:
                embed.set_footer(text=f"Use !criarlobby  |  modos: {_MODO_OPCOES}")

        await ctx.send(embed=embed, delete_after=30)
        try:
            await ctx.message.delete()
        except discord.HTTPException:
            pass
