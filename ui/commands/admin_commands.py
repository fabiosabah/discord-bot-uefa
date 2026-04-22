# -*- coding: utf-8 -*-
import logging

import discord
from discord.ext import commands

from core.config import IMAGE_CHANNEL_ID
from core.db.audit_repo import log_action
from core.db.lobby_repo import get_image_channel, set_image_channel, clear_image_channel
from core.db.match_repo import find_unregistered_match_players, diagnose_and_fix_kda_data, get_ranking_from_matches
from core.db.player_repo import add_player_alias, remove_player_alias, get_player_aliases, get_player, upsert_player, get_all_player_aliases
from ui.commands.score_helpers import is_admin

audit_logger = logging.getLogger("Audit")


def setup_admin_commands(bot: commands.Bot):
    logger = logging.getLogger("AdminCommands")
    logger.info("Carregando comandos administrativos...")

    @bot.command(name="registrarcanalimagem", aliases=["registrarcanalocr"])
    async def cmd_register_image_channel(ctx: commands.Context):
        if not is_admin(ctx.author.id):
            await ctx.message.delete()
            await ctx.send("❌ Apenas administradores.", delete_after=5)
            return

        if not ctx.guild:
            await ctx.message.delete()
            await ctx.send("❌ Este comando só pode ser usado em um servidor.", delete_after=8)
            return

        old_channel_id = get_image_channel(ctx.guild.id)
        set_image_channel(ctx.guild.id, ctx.channel.id)
        await ctx.message.delete()

        if old_channel_id and old_channel_id != ctx.channel.id:
            await ctx.send(
                f"✅ Canal de imagens atualizado: <#{old_channel_id}> → <#{ctx.channel.id}>",
                delete_after=20
            )
        else:
            await ctx.send(
                f"✅ Canal <#{ctx.channel.id}> registrado para leitura de imagens de partida.",
                delete_after=20
            )

    @bot.command(name="limparcanalimagem", aliases=["limparcanalocr"])
    async def cmd_clear_image_channel(ctx: commands.Context):
        if not is_admin(ctx.author.id):
            await ctx.message.delete()
            await ctx.send("❌ Apenas administradores.", delete_after=5)
            return

        if not ctx.guild:
            await ctx.message.delete()
            await ctx.send("❌ Este comando só pode ser usado em um servidor.", delete_after=8)
            return

        clear_image_channel(ctx.guild.id)
        await ctx.message.delete()
        await ctx.send(
            "✅ Canal de imagem OCR foi removido. Imagens só serão processadas em canais explicitamente configurados.",
            delete_after=15
        )

    @bot.command(name="canalimagem", aliases=["imagemcanal"])
    async def cmd_show_image_channel(ctx: commands.Context):
        if not is_admin(ctx.author.id):
            await ctx.message.delete()
            await ctx.send("❌ Apenas administradores.", delete_after=5)
            return

        if not ctx.guild:
            await ctx.message.delete()
            await ctx.send("❌ Este comando só pode ser usado em um servidor.", delete_after=8)
            return

        image_channel_id = get_image_channel(ctx.guild.id)
        if image_channel_id:
            await ctx.send(f"📌 Canal de imagem OCR registrado: <#{image_channel_id}>")
        elif IMAGE_CHANNEL_ID:
            await ctx.send(f"📌 Canal de imagem OCR definido via ENV: <#{IMAGE_CHANNEL_ID}>")
        else:
            await ctx.send("⚠️ Nenhum canal de imagem OCR registrado.")

    @bot.command(name="addalias", aliases=["aliasadd", "alias"])
    async def cmd_add_alias(ctx: commands.Context, member: discord.Member, *, alias: str):
        if not is_admin(ctx.author.id):
            await ctx.message.delete()
            await ctx.send("❌ Apenas administradores.", delete_after=5)
            return

        alias = alias.strip()
        if not alias:
            await ctx.send("❌ Informe o apelido após o membro.", delete_after=10)
            return

        add_player_alias(member.id, alias)
        log_action(
            ctx.author.id,
            ctx.author.display_name,
            "!addalias",
            f"discord_id={member.id} alias={alias}",
            affected_ids=[member.id]
        )

        await ctx.message.delete()
        await ctx.send(f"✅ Alias `{alias}` adicionado para {member.mention}.")

    @bot.command(name="cadastro")
    async def cmd_cadastro(ctx: commands.Context, nick: str, member: discord.Member = None):
        if not is_admin(ctx.author.id):
            await ctx.message.delete()
            await ctx.send("❌ Apenas administradores.", delete_after=5)
            return

        if member is None:
            await ctx.message.delete()
            await ctx.send(
                f"❌ Informe o usuário Discord.\n"
                f"→ `!cadastro {nick} @usuario`",
                delete_after=600
            )
            return

        existing = get_player(member.id)
        if existing:
            upsert_player(member.id, existing["display_name"], existing["wins"], existing["losses"])
        else:
            upsert_player(member.id, member.display_name, 0, 0)

        add_player_alias(member.id, nick)
        log_action(
            ctx.author.id, ctx.author.display_name,
            "!cadastro",
            f"discord_id={member.id} nick={nick}",
            affected_ids=[member.id]
        )

        await ctx.message.delete()
        await ctx.send(f"✅ `{nick}` cadastrado como nick de {member.mention}.", delete_after=600)

    @bot.command(name="removealias", aliases=["delalias"])
    async def cmd_remove_alias(ctx: commands.Context, member: discord.Member, *, alias: str):
        if not is_admin(ctx.author.id):
            await ctx.message.delete()
            await ctx.send("❌ Apenas administradores.", delete_after=5)
            return

        alias = alias.strip()
        if not alias:
            await ctx.send("❌ Informe o alias a ser removido.", delete_after=10)
            return

        remove_player_alias(member.id, alias)
        log_action(
            ctx.author.id,
            ctx.author.display_name,
            "!removealias",
            f"discord_id={member.id} alias={alias}",
            affected_ids=[member.id]
        )

        await ctx.message.delete()
        await ctx.send(f"✅ Alias `{alias}` removido para {member.mention}.")

    @bot.command(name="aliases", aliases=["aliaslist"])
    async def cmd_alias_list(ctx: commands.Context, member: discord.Member):
        if not is_admin(ctx.author.id):
            await ctx.message.delete()
            await ctx.send("❌ Apenas administradores.", delete_after=5)
            return

        aliases = get_player_aliases(member.id)
        if not aliases:
            await ctx.send(f"⚠️ Nenhum alias registrado para {member.mention}.", delete_after=10)
            return

        alias_list = '\n'.join(f"- `{alias}`" for alias in aliases)
        await ctx.send(f"📝 Aliases para {member.mention}:\n{alias_list}")

    @bot.command(name="listaraliases", aliases=["listalias", "allaliases", "todosaliases"])
    async def cmd_listar_aliases(ctx: commands.Context):
        if not is_admin(ctx.author.id):
            await ctx.message.delete()
            await ctx.send("❌ Apenas administradores.", delete_after=5)
            return

        rows = get_all_player_aliases()
        if not rows:
            await ctx.send("⚠️ Nenhum alias cadastrado.")
            return

        lines = [
            f"{r['alias']:<25} → {r['display_name'] or ('<@' + str(r['discord_id']) + '>')}"
            for r in rows
        ]

        header = f"📋 **{len(lines)} alias(es) cadastrados:**"
        chunk_size = 1800
        text = "\n".join(lines)
        chunks = [text[i:i+chunk_size] for i in range(0, len(text), chunk_size)]

        await ctx.send(header)
        for chunk in chunks:
            await ctx.send(f"```\n{chunk}\n```")

    @bot.command(name="jogadoresfaltando", aliases=["missingplayers", "faltando"])
    async def cmd_jogadores_faltando(ctx: commands.Context):
        if not is_admin(ctx.author.id):
            await ctx.message.delete()
            await ctx.send("❌ Apenas administradores.", delete_after=5)
            return

        missing = find_unregistered_match_players()
        if not missing:
            await ctx.send("✅ Todos os jogadores das partidas importadas estão registrados.")
            return

        lines = [f"⚠️ **{len(missing)} jogador(es) em partidas mas sem registro na tabela `players`:**\n"]
        for m in missing:
            lines.append(
                f"• `{m['player_name']}` — discord_id: `{m['discord_id']}` — {m['partidas']} partida(s)\n"
                f"  → `!registrar <@{m['discord_id']}> 0 0` para registrar"
            )
        await ctx.send("\n".join(lines))

    @bot.command(name="fixkda", aliases=["corrigirkda"])
    async def cmd_fix_kda(ctx: commands.Context, confirm: str = ""):
        if not is_admin(ctx.author.id):
            await ctx.message.delete()
            await ctx.send("❌ Apenas administradores.", delete_after=5)
            return

        do_fix = confirm.lower() in {"sim", "yes", "ok", "fix"}
        result = diagnose_and_fix_kda_data(fix=do_fix)
        bad_rows = result["bad_rows"]

        if not bad_rows:
            await ctx.send("✅ Nenhum valor inválido de KDA encontrado nas partidas.")
            return

        lines = [f"⚠️ Encontrados **{len(bad_rows)}** registros com KDA inválido:"]
        for r in bad_rows[:20]:
            lines.append(
                f"• Partida {r['league_match_id']}, slot {r['slot']}, `{r['player_name']}` → "
                f"kills={r['kills']}({r['kills_type']}), deaths={r['deaths']}({r['deaths_type']}), assists={r['assists']}({r['assists_type']})"
            )
        if len(bad_rows) > 20:
            lines.append(f"... e mais {len(bad_rows) - 20} registros.")

        if do_fix:
            lines.append("\n✅ **Valores corrigidos para 0.**")
        else:
            lines.append("\n💡 Use `!fixkda sim` para corrigir os valores para 0.")

        await ctx.send("\n".join(lines))

    @bot.command(name="diagtabela2", aliases=["debugtabela2", "diagtab2"])
    async def cmd_diag_tabela2(ctx: commands.Context):
        if not is_admin(ctx.author.id):
            await ctx.message.delete()
            await ctx.send("❌ Apenas administradores.", delete_after=5)
            return

        import traceback as _tb
        try:
            ranking = get_ranking_from_matches()
            await ctx.send(f"✅ `get_ranking_from_matches()` OK — {len(ranking)} jogadores sem erro.")
        except Exception as exc:
            tb_text = _tb.format_exc()
            short_tb = tb_text[-1800:] if len(tb_text) > 1800 else tb_text
            await ctx.send(
                f"❌ Erro em `get_ranking_from_matches()`:\n"
                f"```\n{type(exc).__name__}: {exc}\n```\n"
                f"```\n{short_tb}\n```"
            )
