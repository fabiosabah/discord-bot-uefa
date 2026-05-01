# -*- coding: utf-8 -*-
import asyncio
import aiohttp
from discord.ext import commands
from core.config import GC_API_URL, is_admin


def setup_dota_gc_commands(bot: commands.Bot):
    @bot.command(name="criarlobby", aliases=["newlobby", "createlobby"])
    async def criar_lobby(ctx: commands.Context, nome: str = "Liga Fumos", senha: str = ""):
        """Cria um lobby Dota 2 via Game Coordinator. Uso: !criarlobby [nome] [senha]"""
        if not is_admin(ctx.author.id):
            await ctx.send("❌ Apenas administradores podem criar lobbies.", delete_after=10)
            return

        if not GC_API_URL:
            await ctx.send("❌ GC_API_URL não configurado.", delete_after=10)
            return

        msg = await ctx.send("⏳ Criando lobby no Dota 2...")

        try:
            async with aiohttp.ClientSession() as session:
                payload = {"name": nome, "password": senha, "mode": 1, "region": 7}
                async with session.post(
                    f"{GC_API_URL}/lobby",
                    json=payload,
                    timeout=aiohttp.ClientTimeout(total=35),
                ) as resp:
                    data = await resp.json()

            if resp.status == 200 and data.get("ok"):
                lobby_name = data.get("name", nome)
                lobby_pass = data.get("password", senha)
                lines = [f"✅ **Lobby criado!**", f"**Nome:** `{lobby_name}`"]
                if lobby_pass:
                    lines.append(f"**Senha:** `{lobby_pass}`")
                await msg.edit(content="\n".join(lines))
            else:
                err = data.get("error", "Erro desconhecido")
                await msg.edit(content=f"❌ Falha ao criar lobby: `{err}`")

        except asyncio.TimeoutError:
            await msg.edit(content="❌ Timeout ao conectar ao GC. Verifique se o serviço está rodando.")
        except aiohttp.ClientConnectorError:
            await msg.edit(content="❌ Não foi possível conectar ao serviço GC. Verifique `GC_API_URL`.")
        except Exception as e:
            await msg.edit(content=f"❌ Erro inesperado: `{e}`")

    @bot.command(name="fecharlobby", aliases=["destroylobby", "deletelobby"])
    async def fechar_lobby(ctx: commands.Context):
        """Fecha o lobby Dota 2 atual. Uso: !fecharlobby"""
        if not is_admin(ctx.author.id):
            await ctx.send("❌ Apenas administradores podem fechar lobbies.", delete_after=10)
            return

        if not GC_API_URL:
            await ctx.send("❌ GC_API_URL não configurado.", delete_after=10)
            return

        try:
            async with aiohttp.ClientSession() as session:
                async with session.delete(
                    f"{GC_API_URL}/lobby",
                    timeout=aiohttp.ClientTimeout(total=20),
                ) as resp:
                    data = await resp.json()

            if resp.status == 200 and data.get("ok"):
                await ctx.send("✅ Lobby encerrado.")
            else:
                err = data.get("error", "Erro desconhecido")
                await ctx.send(f"❌ Falha ao encerrar lobby: `{err}`")

        except (asyncio.TimeoutError, aiohttp.ClientConnectorError):
            await ctx.send("❌ Não foi possível conectar ao serviço GC.", delete_after=15)
        except Exception as e:
            await ctx.send(f"❌ Erro inesperado: `{e}`", delete_after=15)

    @bot.command(name="statusgc", aliases=["gcstatus"])
    async def status_gc(ctx: commands.Context):
        """Mostra o status do Game Coordinator. Uso: !statusgc"""
        if not GC_API_URL:
            await ctx.send("❌ GC_API_URL não configurado.", delete_after=10)
            return

        try:
            async with aiohttp.ClientSession() as session:
                async with session.get(
                    f"{GC_API_URL}/status",
                    timeout=aiohttp.ClientTimeout(total=5),
                ) as resp:
                    data = await resp.json()

            gc_ready = data.get("gc_ready", False)
            lobby = data.get("lobby")

            status_icon = "🟢" if gc_ready else "🔴"
            lines = [f"{status_icon} **GC:** {'Pronto' if gc_ready else 'Não disponível'}"]
            if lobby:
                lines.append(f"**Lobby ativo:** `{lobby['name']}`")
                if lobby.get("password"):
                    lines.append(f"**Senha:** `{lobby['password']}`")
            else:
                lines.append("**Lobby:** nenhum ativo")

            await ctx.send("\n".join(lines))

        except (asyncio.TimeoutError, aiohttp.ClientConnectorError):
            await ctx.send("❌ Serviço GC inacessível.", delete_after=15)
        except Exception as e:
            await ctx.send(f"❌ Erro: `{e}`", delete_after=15)
