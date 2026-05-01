# -*- coding: utf-8 -*-
import asyncio
import aiohttp
from discord.ext import commands
from core.config import GC_API_URL, is_admin

DEFAULT_PASSWORD = "1234"


async def _criar_lobby_request(preset: str, senha: str) -> dict:
    async with aiohttp.ClientSession() as session:
        async with session.post(
            f"{GC_API_URL}/lobby",
            json={"preset": preset, "password": senha},
            timeout=aiohttp.ClientTimeout(total=35),
        ) as resp:
            data = await resp.json()
            return resp.status, data


def setup_dota_gc_commands(bot: commands.Bot):

    @bot.command(name="criarlobby", aliases=["createlobby", "newlobby"])
    async def criar_lobby(ctx: commands.Context, senha: str = DEFAULT_PASSWORD):
        """Cria lobby Captains Mode (10 jogadores). Uso: !criarlobby [senha]"""
        if not is_admin(ctx.author.id):
            await ctx.send("❌ Apenas administradores podem criar lobbies.", delete_after=10)
            return
        if not GC_API_URL:
            await ctx.send("❌ GC_API_URL não configurado.", delete_after=10)
            return

        msg = await ctx.send("⏳ Criando lobby **Captains Mode** (10 jogadores)...")
        try:
            status, data = await _criar_lobby_request("inhouse", senha)
            if status == 200 and data.get("ok"):
                await msg.edit(content="\n".join([
                    "✅ **Lobby criado!** — Captains Mode",
                    f"**Nome:** `{data.get('name', 'UEFA FUMOS LEAGUE')}`",
                    f"**Senha:** `{data.get('password', senha)}`",
                ]))
            else:
                await msg.edit(content=f"❌ Falha ao criar lobby: `{data.get('error', 'Erro desconhecido')}`")
        except asyncio.TimeoutError:
            await msg.edit(content="❌ Timeout — verifique se o serviço GC está rodando.")
        except aiohttp.ClientConnectorError:
            await msg.edit(content="❌ Não foi possível conectar ao serviço GC.")
        except Exception as e:
            await msg.edit(content=f"❌ Erro inesperado: `{e}`")

    @bot.command(name="criarlobby1x1", aliases=["createlobby1v1"])
    async def criar_lobby_1v1(ctx: commands.Context, senha: str = DEFAULT_PASSWORD):
        """Cria lobby 1v1 Solo Mid (teste). Uso: !criarlobby1x1 [senha]"""
        if not is_admin(ctx.author.id):
            await ctx.send("❌ Apenas administradores podem criar lobbies.", delete_after=10)
            return
        if not GC_API_URL:
            await ctx.send("❌ GC_API_URL não configurado.", delete_after=10)
            return

        msg = await ctx.send("⏳ Criando lobby **1v1 Solo Mid**...")
        try:
            status, data = await _criar_lobby_request("1v1", senha)
            if status == 200 and data.get("ok"):
                await msg.edit(content="\n".join([
                    "✅ **Lobby criado!** — 1v1 Solo Mid",
                    f"**Nome:** `{data.get('name', 'UEFA FUMOS 1v1')}`",
                    f"**Senha:** `{data.get('password', senha)}`",
                ]))
            else:
                await msg.edit(content=f"❌ Falha ao criar lobby: `{data.get('error', 'Erro desconhecido')}`")
        except asyncio.TimeoutError:
            await msg.edit(content="❌ Timeout — verifique se o serviço GC está rodando.")
        except aiohttp.ClientConnectorError:
            await msg.edit(content="❌ Não foi possível conectar ao serviço GC.")
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
                await ctx.send(f"❌ Falha: `{data.get('error', 'Erro desconhecido')}`")
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
                preset_label = {"inhouse": "CM 10j", "1v1": "Solo Mid"}.get(lobby.get("preset", ""), "")
                lines.append(f"**Lobby ativo:** `{lobby['name']}`" + (f" ({preset_label})" if preset_label else ""))
                lines.append(f"**Senha:** `{lobby.get('password', '—')}`")
            else:
                lines.append("**Lobby:** nenhum ativo")
            await ctx.send("\n".join(lines))

        except (asyncio.TimeoutError, aiohttp.ClientConnectorError):
            await ctx.send("❌ Serviço GC inacessível.", delete_after=15)
        except Exception as e:
            await ctx.send(f"❌ Erro: `{e}`", delete_after=15)
