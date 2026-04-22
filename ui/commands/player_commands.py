# -*- coding: utf-8 -*-
import discord
import logging
from datetime import datetime as _dt
from discord.ext import commands

from core.database import (
    get_player,
    get_ranking,
    get_player_streak,
    get_player_match_history,
    get_player_top_opponents,
    get_match_summary,
    find_player_by_display_name,
    get_ranking_from_matches,
    get_streak_highlights_from_matches,
    get_league_hero_winrates_from_matches,
    get_last_ocr_match_info,
    get_player_match_stats_from_matches,
    get_player_match_history_from_matches,
    get_player_streak_from_matches,
    get_player_top_heroes_with_winrate_from_matches,
    get_player_head_to_head_from_matches,
    get_player_teammate_balance_from_matches,
    get_all_hero_stats_from_matches,
    get_hero_match_history,
)
from core.dota_heroes import resolve_hero_name, format_hero_suggestions
from core.utils.time import format_brazil_time
from ui.commands.score_helpers import is_admin, points, winrate_tier, build_footer


def setup_player_commands(bot: commands.Bot):
    logger = logging.getLogger("PlayerCommands")
    logger.info("Carregando comandos de jogadores...")

    @bot.command(name="tabela1", aliases=["tabelamanual"])
    async def cmd_tabela1(ctx: commands.Context):
        ranking = get_ranking()

        if not ranking:
            await ctx.send("📋 Nenhum jogador registrado ainda.")
            return

        embed = discord.Embed(title="🏆 Tabela do Campeonato (manual)", color=discord.Color.gold())

        linhas = []
        for i, p in enumerate(ranking):
            prefix = "👑" if i == 0 else "🥈" if i == 1 else "🥉" if i == 2 else f"{i+1}."
            linhas.append(
                f"{prefix} **{p['display_name']}** — "
                f"{p['points']} pts "
                f"(`{p['wins']}V / {p['losses']}D` — {p['games']} jogos)"
            )

        embed.description = "\n".join(linhas)
        embed.set_footer(text=build_footer())

        await ctx.send(embed=embed)

    @bot.command(name="tabela", aliases=["tabela2"])
    async def cmd_tabela(ctx: commands.Context):
        ranking = get_ranking_from_matches()

        if not ranking:
            await ctx.send("📋 Nenhum jogador registrado ainda nas partidas importadas.")
            return

        streaks = get_streak_highlights_from_matches()
        cur_win_id  = streaks["current_win"]["discord_id"]
        cur_loss_id = streaks["current_loss"]["discord_id"]

        embed = discord.Embed(title="🏆 Tabela do Campeonato", color=discord.Color.dark_gold())

        linhas = []
        for i, p in enumerate(ranking):
            prefix = "👑" if i == 0 else "🥈" if i == 1 else "🥉" if i == 2 else f"{i+1}."
            streak_tag = ""
            if p["discord_id"] == cur_win_id and streaks["current_win"]["count"] >= 2:
                streak_tag = f" 🔥×{streaks['current_win']['count']}"
            elif p["discord_id"] == cur_loss_id and streaks["current_loss"]["count"] >= 2:
                streak_tag = f" 💀×{streaks['current_loss']['count']}"
            linhas.append(
                f"{prefix} **{p['display_name']}**{streak_tag} — "
                f"{p['points']} pts "
                f"(`{p['wins']}V / {p['losses']}D` — {p['games']} jogos)"
            )

        embed.description = "\n".join(linhas)

        rec_w = streaks["record_win"]
        rec_l = streaks["record_loss"]
        records_lines = []
        if rec_w["display_name"]:
            records_lines.append(f"🏅 Recorde winstreak: **{rec_w['display_name']}** ({rec_w['count']} seguidas)")
        if rec_l["display_name"]:
            records_lines.append(f"💔 Recorde lossstreak: **{rec_l['display_name']}** ({rec_l['count']} seguidas)")
        if records_lines:
            embed.add_field(name="Recordes", value="\n".join(records_lines), inline=False)

        hero_wr = get_league_hero_winrates_from_matches(min_games=2)
        if hero_wr:
            top3 = sorted(hero_wr, key=lambda h: (-h["winrate"], -h["games"]))[:3]
            bot3 = sorted(hero_wr, key=lambda h: (h["winrate"], -h["games"]))[:3]
            embed.add_field(
                name="🏹 Melhores heróis",
                value="\n".join(
                    f"{i+1}. **{h['hero']}** — {h['winrate']:.0f}% ({h['games']} jogos)"
                    for i, h in enumerate(top3)
                ),
                inline=True,
            )
            embed.add_field(
                name="💀 Piores heróis",
                value="\n".join(
                    f"{i+1}. **{h['hero']}** — {h['winrate']:.0f}% ({h['games']} jogos)"
                    for i, h in enumerate(bot3)
                ),
                inline=True,
            )

        last = get_last_ocr_match_info()
        if last:
            try:
                ts = _dt.fromisoformat(last["created_at"])
                last_text = f"📌 Última partida: #{last['league_match_id']} • {format_brazil_time(ts.isoformat())}"
            except Exception:
                last_text = f"📌 Última partida: #{last['league_match_id']}"
        else:
            last_text = "🕹️ Nenhuma partida importada ainda"

        embed.set_footer(text=f"⚖️ Vitória +3 pts | Derrota -1 pt\n{last_text}")

        await ctx.send(embed=embed)

    @bot.command(name="top")
    async def cmd_top(ctx: commands.Context, n: int = 10):
        if n < 1 or n > 15:
            await ctx.send("❌ Escolha um número entre 1 e 15.")
            return

        ranking = get_ranking()

        if not ranking:
            await ctx.send("📋 Nenhum jogador registrado ainda.")
            return

        top_players = ranking[:n]

        embed = discord.Embed(
            title=f"🏆 Top {n} Jogadores",
            color=discord.Color.gold()
        )

        linhas = []
        for i, p in enumerate(top_players):
            if i == 0:
                prefix = "👑"
            elif i == 1:
                prefix = "🥈"
            elif i == 2:
                prefix = "🥉"
            else:
                prefix = f"`{i+1:02d}.`"

            linhas.append(
                f"{prefix} **{p['display_name']}** — "
                f"{p['points']} pts "
                f"(`{p['wins']}V/{p['losses']}D`)"
            )

        embed.description = "🔥 **Melhores jogadores da liga**\n\n" + "\n".join(linhas)
        embed.set_footer(text=build_footer())

        await ctx.send(embed=embed)

    @bot.command(name="perfil1")
    async def cmd_perfil1(ctx, target: discord.Member = None):
        target = target or ctx.author
        player = get_player(target.id)

        if not player:
            await ctx.send("❌ Esse jogador ainda não possui dados.")
            return

        wins = player["wins"]
        losses = player["losses"]
        games = wins + losses
        pts = points(wins, losses)
        winrate = (wins / games * 100) if games else 0

        streak = get_player_streak(target.id)
        streak_type = streak["streak_type"]
        streak_count = streak["streak_count"]

        recent_matches = get_player_match_history(target.id, limit=3)
        win_opponents = get_player_top_opponents(target.id, "win", limit=3)
        loss_opponents = get_player_top_opponents(target.id, "loss", limit=3)

        ranking = get_ranking()
        pos = next((i+1 for i, p in enumerate(ranking) if p["discord_id"] == target.id), None)

        if winrate >= 60:
            color = discord.Color.green()
        elif winrate >= 40:
            color = discord.Color.orange()
        else:
            color = discord.Color.red()

        if winrate >= 70:
            msg = "🔥 Jogando demais!"
        elif winrate >= 50:
            msg = "⚖️ Equilibrado"
        else:
            msg = "📉 Precisa melhorar"

        if streak_type == "win":
            streak_text = f"🔥 Win streak de {streak_count}! Duplinha maldita na área."
        elif streak_type == "loss":
            streak_text = f"📉 Loss streak de {streak_count}. Segura a onda, parceiro."
        else:
            streak_text = "⚖️ Ainda sem sequência definida."

        title = f"📊 Perfil de {target.display_name}"
        if pos == 1:
            title = f"👑 Líder da Liga: {target.display_name}"

        embed = discord.Embed(title=title, description=f"{msg}\n{streak_text}", color=color)
        embed.set_thumbnail(url=target.display_avatar.url)

        embed.add_field(name="🏆 Vitórias", value=wins, inline=True)
        embed.add_field(name="💀 Derrotas", value=losses, inline=True)
        embed.add_field(name="🎯 Pontos", value=pts, inline=True)

        embed.add_field(name="🎮 Jogos", value=games, inline=True)
        embed.add_field(name="📈 Winrate", value=f"{winrate:.1f}%", inline=True)

        if streak_type and streak_count:
            if streak_type == "win":
                embed.add_field(name="🔥 Sequência", value=f"{streak_count} vitória(s) seguida(s)", inline=True)
            else:
                embed.add_field(name="📉 Sequência", value=f"{streak_count} derrota(s) seguida(s)", inline=True)

        if pos:
            embed.add_field(name="🥇 Ranking", value=f"#{pos}", inline=True)

        if recent_matches:
            recent_lines = []
            for item in recent_matches:
                summary = get_match_summary(item["match_id"])
                if not summary:
                    continue
                recent_lines.append(
                    f"`#{summary['match_id']:03d}` — "
                    f"🏆 {', '.join(summary['winners'])} | 💀 {', '.join(summary['losers'])}"
                )
            if recent_lines:
                embed.add_field(name="🕹️ Últimas 3 partidas", value="\n".join(recent_lines), inline=False)

        if win_opponents:
            win_lines = [f"{i+1}. {opp['display_name']} — {opp['count']}x" for i, opp in enumerate(win_opponents)]
            win_lines.append("\nMeu freguês: inimigo que você mais derrotou.")
            embed.add_field(name="😎 Meu freguês", value="\n".join(win_lines), inline=False)

        if loss_opponents:
            loss_lines = [f"{i+1}. {opp['display_name']} — {opp['count']}x" for i, opp in enumerate(loss_opponents)]
            loss_lines.append("\nMeu nemesis: inimigo que mais te derrotou.")
            embed.add_field(name="☠️ Meu nemesis", value="\n".join(loss_lines), inline=False)

        embed.set_footer(text="Sistema de Liga • UEFA Bot")

        await ctx.send(embed=embed)

    @bot.command(name="perfil", aliases=["perfil2"])
    async def cmd_perfil(ctx, target: discord.Member = None):
        target = target or ctx.author
        stats = get_player_match_stats_from_matches(target.id)

        if stats["matches"] == 0:
            await ctx.send(f"❌ Nenhum histórico OCR encontrado para **{target.display_name}**.")
            return

        all_heroes       = get_player_top_heroes_with_winrate_from_matches(target.id, limit=50)
        head_to_head     = get_player_head_to_head_from_matches(target.id)
        teammate_balance = get_player_teammate_balance_from_matches(target.id, min_games=3, limit=3)
        streak           = get_player_streak_from_matches(target.id)
        recent           = get_player_match_history_from_matches(target.id, limit=5)

        ranking  = get_ranking_from_matches()
        rank_pos = next((i + 1 for i, p in enumerate(ranking) if p["discord_id"] == target.id), None)
        rank_pts = next((p["points"] for p in ranking if p["discord_id"] == target.id), None)

        wins    = stats["wins"]
        losses  = stats["losses"]
        winrate = stats["winrate"]

        defuntos = sorted(
            [h for h in head_to_head if h["player_wins"] > h["opponent_wins"]],
            key=lambda x: x["player_wins"] - x["opponent_wins"],
            reverse=True
        )[:3]

        nemesis = sorted(
            [h for h in head_to_head if h["opponent_wins"] > h["player_wins"]],
            key=lambda x: x["opponent_wins"] - x["player_wins"],
            reverse=True
        )[:3]

        if winrate >= 60:
            color = discord.Color.green()
        elif winrate >= 40:
            color = discord.Color.gold()
        else:
            color = discord.Color.red()

        embed = discord.Embed(
            title=f"📊 Perfil de {target.display_name}",
            description=winrate_tier(winrate),
            color=color
        )
        embed.set_thumbnail(url=target.display_avatar.url)

        embed.add_field(name="🎮 Partidas", value=str(stats["matches"]), inline=True)
        embed.add_field(name="🏆 Vitórias",  value=str(wins),           inline=True)
        embed.add_field(name="💀 Derrotas",  value=str(losses),         inline=True)
        embed.add_field(name="📈 Winrate",   value=f"{winrate:.1f}%",   inline=True)

        if rank_pos is not None:
            embed.add_field(name="🥇 Ranking", value=f"#{rank_pos} — {rank_pts} pts", inline=True)

        if stats["kda_rows"]:
            n = stats["kda_rows"]
            embed.add_field(
                name="⚔️ KDA Médio",
                value=f"{stats['total_kills']/n:.1f} / {stats['total_deaths']/n:.1f} / {stats['total_assists']/n:.1f}",
                inline=True
            )

        s_type  = streak["streak_type"]
        s_count = streak["streak_count"]
        if s_type == "win" and s_count >= 2:
            embed.add_field(name="🔥 Sequência", value=f"{s_count} vitórias seguidas", inline=True)
        elif s_type == "loss" and s_count >= 2:
            embed.add_field(name="📉 Sequência", value=f"{s_count} derrotas seguidas", inline=True)

        top5_played   = all_heroes[:5]
        eligible_wr   = [h for h in all_heroes if h["plays"] >= 3]
        top3_best_wr  = sorted(eligible_wr, key=lambda x: (-x["winrate"], -x["plays"]))[:3]
        top3_worst_wr = sorted(eligible_wr, key=lambda x: (x["winrate"], -x["plays"]))[:3]

        if top5_played:
            lines = [
                f"{i+1}. **{h['hero']}** — {h['plays']} jogos · {h['winrate']:.0f}% WR"
                for i, h in enumerate(top5_played)
            ]
            embed.add_field(name="🦸 Top 5 mais jogados", value="\n".join(lines), inline=False)

        if top3_best_wr:
            lines = [
                f"{i+1}. **{h['hero']}** — {h['winrate']:.0f}% WR ({h['plays']} jogos)"
                for i, h in enumerate(top3_best_wr)
            ]
            embed.add_field(name="📈 Melhor winrate", value="\n".join(lines), inline=True)

        if top3_worst_wr:
            lines = [
                f"{i+1}. **{h['hero']}** — {h['winrate']:.0f}% WR ({h['plays']} jogos)"
                for i, h in enumerate(top3_worst_wr)
            ]
            embed.add_field(name="📉 Pior winrate", value="\n".join(lines), inline=True)

        if teammate_balance:
            best  = teammate_balance[:3]
            worst = list(reversed(teammate_balance[-3:]))
            worst = [t for t in worst if t not in best]

            def _fmt_teammate(t: dict) -> str:
                sign = "+" if t["balance"] >= 0 else ""
                return f"**{t['display_name']}** — {sign}{t['balance']} ({t['wins']}V/{t['losses']}D em {t['games']} jogos)"

            if best:
                embed.add_field(
                    name="🤝 Melhores parceiros",
                    value="\n".join(_fmt_teammate(t) for t in best),
                    inline=False
                )
            if worst:
                embed.add_field(
                    name="😤 Piores parceiros",
                    value="\n".join(_fmt_teammate(t) for t in worst),
                    inline=False
                )

        if defuntos:
            lines = []
            for i, d in enumerate(defuntos):
                diff = d["player_wins"] - d["opponent_wins"]
                lines.append(
                    f"{i+1}. **{d['display_name']}** — "
                    f"{d['player_wins']}V/{d['opponent_wins']}D em {d['total']} confrontos (+{diff})"
                )
            embed.add_field(name="⚰️ Meu Defunto", value="\n".join(lines), inline=False)

        if nemesis:
            lines = []
            for i, m in enumerate(nemesis):
                diff = m["opponent_wins"] - m["player_wins"]
                lines.append(
                    f"{i+1}. **{m['display_name']}** — "
                    f"{m['player_wins']}V/{m['opponent_wins']}D em {m['total']} confrontos (−{diff})"
                )
            embed.add_field(name="👹 Meu Nemesis", value="\n".join(lines), inline=False)

        if recent:
            lines = []
            for r in recent:
                icon  = "✅" if r["result"] == "win" else "❌"
                hero  = r["hero"] or "?"
                k, d, a = r.get("kills"), r.get("deaths"), r.get("assists")
                kda   = f"{k}/{d}/{a}" if k is not None and d is not None and a is not None else "?/?/?"
                lines.append(f"{icon} `#{r['league_match_id']}` {hero} · {kda}")
            embed.add_field(name="🕹️ Últimas 5 partidas", value="\n".join(lines), inline=False)

        embed.set_footer(text="Perfil gerado a partir de partidas importadas via OCR")
        await ctx.send(embed=embed)

    @bot.command(name="listarpartidas", aliases=["partidas", "matchlist"])
    async def cmd_listar_partidas(ctx, target: discord.Member = None, limit: int = 30):
        target = target or ctx.author
        if limit < 1 or limit > 200:
            limit = 30

        history = get_player_match_history_from_matches(target.id, limit=limit)
        if not history:
            await ctx.send(f"❌ Nenhuma partida OCR encontrada para **{target.display_name}**.")
            return

        total = stats["matches"] if (stats := get_player_match_stats_from_matches(target.id)) else len(history)

        lines = []
        for r in history:
            icon = "✅" if r["result"] == "win" else "❌"
            hero = (r["hero"] or "?").ljust(18)
            k, d, a = r.get("kills"), r.get("deaths"), r.get("assists")
            kda  = f"{k}/{d}/{a}" if k is not None and d is not None and a is not None else "?/?/?"
            date = ""
            if r.get("created_at"):
                try:
                    date = _dt.fromisoformat(r["created_at"]).strftime("%d/%m")
                except Exception:
                    pass
            lines.append(f"{icon} #{str(r['league_match_id']).ljust(4)} {hero} {kda.ljust(9)} {date}")

        header = (
            f"📜 **Partidas de {target.display_name}** — {total} no total "
            f"| mostrando últimas {len(history)} | `!id <número>` para detalhes"
        )

        chunk_size = 1800
        text = "\n".join(lines)
        chunks = [text[i:i+chunk_size] for i in range(0, len(text), chunk_size)]

        await ctx.send(header)
        for chunk in chunks:
            await ctx.send(f"```\n{chunk}\n```")

    @bot.command(name="historico", aliases=["history"])
    async def cmd_historico(ctx: commands.Context, member: discord.Member = None, limit: int = 8):
        target = member or ctx.author
        history = get_player_match_history(target.id, limit)

        if not history:
            await ctx.send(f"📋 Nenhum histórico encontrado para {target.display_name}.")
            return

        lines = []
        for item in history:
            summary = get_match_summary(item["match_id"])
            if not summary:
                continue

            winners = summary["winners"] or ["(não registrado)"]
            losers = summary["losers"] or ["(não registrado)"]
            lines.append(
                f"`#{summary['match_id']:03d}` — "
                f"🏆 {', '.join(winners)} | 💀 {', '.join(losers)}"
            )

        embed = discord.Embed(
            title=f"📜 Histórico de partidas de {target.display_name}",
            description="\n".join(lines[:limit]),
            color=discord.Color.blurple()
        )
        embed.set_footer(text=f"Últimas {min(len(lines), limit)} partidas")
        await ctx.send(embed=embed)

    @bot.command(name="ultimas", aliases=["ultimaspartidas", "recentes"])
    async def cmd_ultimas(ctx: commands.Context, member: discord.Member = None):
        target = member or ctx.author
        history = get_player_match_history_from_matches(target.id, limit=30)

        if not history:
            who = f"**{target.display_name}**" if member else "você"
            await ctx.send(f"📋 Nenhuma partida encontrada para {who}.")
            return

        lines = []
        for m in history:
            icon  = "✅" if m["result"] == "win" else "❌"
            hero  = m["hero"] or "?"
            k, d, a = m.get("kills"), m.get("deaths"), m.get("assists")
            kda   = f"{k}/{d}/{a}" if k is not None and d is not None else "?/?/?"
            lines.append(f"{icon} **#{m['league_match_id']}** · {hero} · `{kda}`")

        wins   = sum(1 for m in history if m["result"] == "win")
        losses = len(history) - wins

        embed = discord.Embed(
            title=f"📈 Últimas {len(history)} partidas — {target.display_name}",
            description="\n".join(lines),
            color=discord.Color.gold(),
        )
        embed.set_footer(text=f"{wins}V / {losses}D nas últimas {len(history)} partidas")
        await ctx.send(embed=embed)

    @bot.command(name="heroes", aliases=["herois", "herostat", "heropool"])
    async def cmd_heroes(ctx: commands.Context, *, hero: str = ""):
        if hero.strip():
            resolved, suggestions, status = resolve_hero_name(hero.strip())
            if status == "ambiguous":
                await ctx.send(
                    f"❓ Herói ambíguo. Você quis dizer: {', '.join(f'`{s}`' for s in suggestions)}?",
                    delete_after=20
                )
                return
            if not resolved:
                msg = f"❌ Herói `{hero}` não encontrado."
                if suggestions:
                    msg += f" Sugestões: {', '.join(f'`{s}`' for s in suggestions)}"
                await ctx.send(msg, delete_after=20)
                return

            matches = get_hero_match_history(resolved)
            if not matches:
                await ctx.send(f"📋 **{resolved}** ainda não foi jogado no campeonato.")
                return

            wins   = sum(1 for m in matches if m["result"] == "win")
            losses = len(matches) - wins
            wr     = wins * 100 / len(matches)

            embed = discord.Embed(
                title=f"🗡️ {resolved}",
                description=f"**{len(matches)} partidas** · {wins}V / {losses}D · {wr:.0f}% WR",
                color=discord.Color.dark_teal(),
            )

            lines = []
            for m in matches:
                icon  = "✅" if m["result"] == "win" else "❌"
                k, d, a = m["kills"], m["deaths"], m["assists"]
                kda   = f"{k}/{d}/{a}" if k is not None and d is not None else "?/?/?"
                team  = m["team"].title() if m["team"] else "?"
                lines.append(
                    f"{icon} **#{m['league_match_id']}** · {m['display_name']} ({team}) · `{kda}`"
                )

            embed.add_field(name="Partidas", value="\n".join(lines) or "—", inline=False)
            await ctx.send(embed=embed)
            return

        stats = get_all_hero_stats_from_matches()

        if not stats:
            await ctx.send("📋 Nenhum herói registrado ainda nas partidas importadas.")
            return

        embed = discord.Embed(
            title="🗡️ Heróis do Campeonato",
            color=discord.Color.dark_teal(),
        )

        def fmt(i: int, h: dict) -> str:
            return f"`{i:>2}.` **{h['hero']}** — {h['picks']} jogos · {h['winrate']:.0f}%"

        FIELD_LIMIT = 1000
        chunks: list[list[str]] = [[]]
        for i, h in enumerate(stats):
            line = fmt(i + 1, h)
            current = "\n".join(chunks[-1])
            if len(current) + len(line) + 1 > FIELD_LIMIT:
                chunks.append([])
            chunks[-1].append(line)

        start = 1
        for chunk in chunks:
            end = start + len(chunk) - 1
            embed.add_field(
                name=f"Picks {start}–{end}",
                value="\n".join(chunk),
                inline=True,
            )
            start = end + 1

        unpicked = [h["hero"] for h in stats if h["picks"] == 0]
        picked   = [h for h in stats if h["picks"] > 0]
        total_picks = sum(h["picks"] for h in picked)

        if unpicked:
            embed.add_field(
                name=f"⛔ Nunca pickados ({len(unpicked)})",
                value=", ".join(unpicked),
                inline=False,
            )

        embed.set_footer(text=f"{len(picked)} heróis pickados · {total_picks} picks totais · use !heroes <nome> para detalhes")

        await ctx.send(embed=embed)
