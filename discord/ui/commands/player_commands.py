# -*- coding: utf-8 -*-
import secrets
import discord
import logging
from datetime import datetime as _dt
from discord.ext import commands

from core.db.free_players_repo import add_free_player, is_free_player, get_free_player, FREE_MATCH_LIMIT
from core.db.match_repo import (
    get_ranking_from_matches,
    get_streak_highlights_from_matches,
    get_last_ocr_match_info,
    get_player_match_stats_from_matches,
    get_player_match_history_from_matches,
    get_player_streak_from_matches,
    get_player_top_heroes_with_winrate_from_matches,
    get_player_head_to_head_from_matches,
    get_player_teammate_balance_from_matches,
    get_all_hero_stats_from_matches,
    get_hero_match_history,
    get_player_duo_stats,
    get_match_duration_extremes,
    get_match_players_bulk,
    get_recent_league_matches,
)
from core.db.pagantes_repo import list_pagantes, is_pagante
from core.db.player_repo import get_player, find_player_by_display_name, set_steam_friend_id, get_steam_friend_id
from core.db.season_repo import get_current_season
from core.db.suspension_repo import is_suspended
from core.db.bot_config_repo import is_final_phase_active
from core.db.final_repo import get_final_participants
from core.dota_heroes import resolve_hero_name, format_hero_suggestions
from core.utils.time import format_brazil_time
from ui.commands.score_helpers import is_admin, winrate_tier, build_footer


def setup_player_commands(bot: commands.Bot):
    logger = logging.getLogger("PlayerCommands")
    logger.info("Carregando comandos de jogadores...")

    @bot.command(name="tabela")
    async def cmd_tabela(ctx: commands.Context, season: int = None):
        current_season = get_current_season()
        if season is None:
            season = current_season

        ranking = get_ranking_from_matches(season)

        if not ranking:
            await ctx.send(f"📋 Nenhum jogador registrado na Temporada {season}.")
            return

        is_current = season == current_season

        embed = discord.Embed(title=f"🏆 Tabela — Temporada {season}", color=discord.Color.dark_gold())

        if is_current:
            streaks = get_streak_highlights_from_matches()
            cur_win_ids  = {pl["discord_id"] for pl in streaks["current_win"]["players"]}
            cur_loss_ids = {pl["discord_id"] for pl in streaks["current_loss"]["players"]}
        else:
            streaks = None
            cur_win_ids = cur_loss_ids = set()

        linhas = []
        for i, p in enumerate(ranking):
            prefix = "👑" if i == 0 else "🥈" if i == 1 else "🥉" if i == 2 else f"{i+1}."
            streak_tag = ""
            if streaks:
                if p["discord_id"] in cur_win_ids and streaks["current_win"]["count"] >= 2:
                    streak_tag = f" 🔥×{streaks['current_win']['count']}"
                elif p["discord_id"] in cur_loss_ids and streaks["current_loss"]["count"] >= 2:
                    streak_tag = f" 💩×{streaks['current_loss']['count']}"
            linhas.append(
                f"{prefix} **{p['display_name']}**{streak_tag} — "
                f"{p['points']} pts "
                f"(`{p['wins']}V / {p['losses']}D` — {p['games']} jogos)"
            )

        embed.description = "\n".join(linhas)

        if is_current and streaks:
            rec_w = streaks["record_win"]
            rec_l = streaks["record_loss"]
            records_lines = []
            if rec_w["players"]:
                nomes_w = ", ".join(f"**{pl['display_name']}**" for pl in rec_w["players"])
                records_lines.append(f"🏅 Recorde winstreak: {nomes_w} ({rec_w['count']} seguidas)")
            if rec_l["players"]:
                nomes_l = ", ".join(f"**{pl['display_name']}**" for pl in rec_l["players"])
                records_lines.append(f"💩 Recorde lossstreak: {nomes_l} ({rec_l['count']} seguidas)")
            if records_lines:
                embed.add_field(name="Recordes", value="\n".join(records_lines), inline=False)

        last = get_last_ocr_match_info()
        if last and is_current:
            try:
                ts = _dt.fromisoformat(last["created_at"])
                last_text = f"📌 Última partida: #{last['season_match_id']} • {format_brazil_time(ts.isoformat())}"
            except Exception:
                last_text = f"📌 Última partida: #{last['season_match_id']}"
        else:
            last_text = f"📌 Temporada {season}" if not is_current else "🕹️ Nenhuma partida importada ainda"

        embed.set_footer(text=f"{build_footer(include_rules=False)}\n{last_text}" if is_current else last_text)

        # Mostra top 15 elegíveis ao final da tabela atual
        if is_current:
            MIN_GAMES = 25
            elegíveis = [
                p for p in ranking
                if p["games"] >= MIN_GAMES and not is_suspended(p["discord_id"])
            ][:15]
            if is_final_phase_active():
                embed.add_field(
                    name="🔥 Fase Final em andamento",
                    value="Use `!final` para ver o ranking da Final Top 15.",
                    inline=False,
                )
            elif elegíveis:
                nomes = ", ".join(f"**{p['display_name']}**" for p in elegíveis)
                label = f"🏆 Classificados para a Final ({len(elegíveis)}/15)"
                embed.add_field(name=label, value=nomes, inline=False)
            else:
                embed.add_field(
                    name="🏆 Classificados para a Final",
                    value=f"Nenhum jogador com {MIN_GAMES}+ partidas ainda.",
                    inline=False,
                )

        await ctx.send(embed=embed)

    @bot.command(name="roll", aliases=["sortear"])
    async def cmd_roll(ctx: commands.Context):
        n = secrets.randbelow(100)
        await ctx.send(f"🎲 {ctx.author.mention} tirou **{n}**")

    @bot.command(name="top15", aliases=["classificados", "elegíveis"])
    async def cmd_top15(ctx: commands.Context):
        """Mostra quem está elegível para a Final Top 15 (fase classificatória)."""
        MIN_GAMES = 25
        MAX_CLASSIFIED = 15
        medals = {0: "🥇", 1: "🥈", 2: "🥉"}

        ranking = get_ranking_from_matches()

        if not ranking:
            await ctx.send("📋 Nenhum jogador registrado ainda.")
            return

        elegíveis = []
        inelegíveis = []
        for p in ranking:
            if p["games"] < MIN_GAMES:
                inelegíveis.append((p, f"apenas {p['games']} partidas"))
            elif is_suspended(p["discord_id"]):
                inelegíveis.append((p, "cumprindo suspensão"))
            else:
                elegíveis.append(p)

        top = elegíveis[:MAX_CLASSIFIED]

        embed = discord.Embed(
            title="🔥 Final Top 15 — Pré-classificação",
            description=(
                f"Os **{MAX_CLASSIFIED} melhores** com no mínimo **{MIN_GAMES} partidas** e sem suspensão ativa\n"
                f"vão disputar a fase final com **pontos zerados**."
            ),
            color=discord.Color.gold(),
        )

        if top:
            linhas = []
            for i, p in enumerate(top):
                prefix = medals.get(i, f"`{i+1:02d}.`")
                linhas.append(
                    f"{prefix} **{p['display_name']}** — "
                    f"{p['points']} pts · "
                    f"{p['wins']}V/{p['losses']}D · {p['games']} jogos"
                )
            embed.add_field(name=f"✅ Elegíveis ({len(top)}/{MAX_CLASSIFIED})", value="\n".join(linhas), inline=False)
        else:
            embed.add_field(name="✅ Elegíveis", value=f"Nenhum jogador com {MIN_GAMES}+ partidas ainda.", inline=False)

        if inelegíveis:
            linhas_in = [f"• **{p['display_name']}** — {motivo}" for p, motivo in inelegíveis[:8]]
            if len(inelegíveis) > 8:
                linhas_in.append(f"... e mais {len(inelegíveis) - 8} jogador(es)")
            embed.add_field(name="⛔ Ainda não elegíveis", value="\n".join(linhas_in), inline=False)

        if is_final_phase_active():
            embed.set_footer(text="⚠️ Fase final já iniciada — use !final para ver o ranking da final")
        else:
            embed.set_footer(text=f"Use !final para ver o ranking ao iniciar a fase final")
        await ctx.send(embed=embed)

    @bot.command(name="final", aliases=["top", "tabela15", "finalTop15"])
    async def cmd_final(ctx: commands.Context):
        """Ranking da Final Top 15 — pontos zerados no início da fase final."""
        medals = {0: "🥇", 1: "🥈", 2: "🥉"}

        if not is_final_phase_active():
            await ctx.send(
                "⚠️ A fase final ainda não foi iniciada.\n"
                "Use `!top15` para ver quem está elegível para a final.",
                delete_after=15,
            )
            return

        participants = get_final_participants()
        participant_ids = {p["discord_id"] for p in participants}

        final_ranking = get_ranking_from_matches(phase="final")
        ranked_ids = {p["discord_id"] for p in final_ranking}

        for part in participants:
            if part["discord_id"] not in ranked_ids:
                final_ranking.append({
                    "discord_id":   part["discord_id"],
                    "display_name": part["display_name"],
                    "wins": 0, "losses": 0, "points": 0, "games": 0,
                })

        final_ranking = sorted(
            [p for p in final_ranking if p["discord_id"] in participant_ids],
            key=lambda p: (p["points"], p["wins"]),
            reverse=True,
        )

        embed = discord.Embed(
            title="🏆 Final Top 15 — Ranking da Final",
            description="Pontos zerados no início da fase final. Apenas partidas da fase final contam.",
            color=discord.Color.from_rgb(255, 215, 0),
        )

        linhas = []
        for i, p in enumerate(final_ranking):
            prefix = medals.get(i, f"`{i+1:02d}.`")
            jogos = f"{p['wins']}V/{p['losses']}D" if p["games"] else "sem jogos"
            linhas.append(f"{prefix} **{p['display_name']}** — {p['points']} pts · {jogos}")

        embed.description = "\n".join(linhas) if linhas else "Nenhuma partida da final registrada ainda."
        embed.set_footer(text=f"🔥 {len(participants)} finalistas · Use !tabela para ver a fase classificatória")
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

        from core.db.pagantes_repo import get_pagante_mmr
        ranking  = get_ranking_from_matches()
        rank_pos = next((i + 1 for i, p in enumerate(ranking) if p["discord_id"] == target.id), None)
        rank_pts = next((p["points"] for p in ranking if p["discord_id"] == target.id), None)
        mmr = get_pagante_mmr(target.id)

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

        if mmr is not None:
            embed.add_field(name="📡 MMR", value=str(mmr), inline=True)

        from core.db.suspension_repo import get_suspension, list_point_penalties
        susp = get_suspension(target.id)
        penalties = list_point_penalties()
        player_penalties = [p for p in penalties if p["discord_id"] == target.id]

        punicoes = []
        if susp and is_suspended(target.id):
            remaining = susp.get("lists_remaining", "?")
            punicoes.append(f"🚫 Lobby: {remaining} lista(s) restante(s)")
        if player_penalties:
            total_pts = sum(p["points"] for p in player_penalties)
            punicoes.append(f"📉 Pontos: {total_pts} pts deduzidos ({len(player_penalties)} penalidade(s))")
        if punicoes:
            embed.add_field(name="⚠️ Punições Ativas", value="\n".join(punicoes), inline=False)

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
        eligible_wr   = [h for h in all_heroes if h["plays"] >= 3] or [h for h in all_heroes if h["plays"] >= 1]

        # Melhor WR: só heróis com pelo menos 1 vitória (WR > 0%)
        top3_best_wr  = sorted(
            [h for h in eligible_wr if h["winrate"] > 0],
            key=lambda x: (-x["winrate"], -x["plays"])
        )[:3]

        # Pior WR: só heróis com pelo menos 1 derrota (WR < 100%)
        # Empate no WR: quem perdeu mais jogos aparece primeiro
        top3_worst_wr = sorted(
            [h for h in eligible_wr if h["winrate"] < 100],
            key=lambda x: (x["winrate"], -(x["plays"] - x["wins"]))
        )[:3]

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
                lines.append(f"{icon} `#{r['season_match_id']}` {hero} · {kda}")
            embed.add_field(name="🕹️ Últimas 5 partidas", value="\n".join(lines), inline=False)

        embed.set_footer(text="Perfil gerado a partir de partidas importadas via OCR")
        await ctx.send(embed=embed)

    @bot.command(name="listarpartidas", aliases=["partidas", "matchlist"])
    async def cmd_listar_partidas(ctx, target: discord.Member = None):
        target = target or ctx.author

        history = get_player_match_history_from_matches(target.id, limit=None)
        if not history:
            await ctx.send(f"❌ Nenhuma partida OCR encontrada para **{target.display_name}**.")
            return

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
            lines.append(f"{icon} #{str(r['season_match_id']).ljust(4)} {hero} {kda.ljust(9)} {date}")

        header = (
            f"📜 **Partidas de {target.display_name}** — {len(history)} partidas "
            f"| `!id <número>` para detalhes"
        )

        chunk_size = 1800
        text = "\n".join(lines)
        chunks = [text[i:i+chunk_size] for i in range(0, len(text), chunk_size)]

        await ctx.send(header)
        for chunk in chunks:
            await ctx.send(f"```\n{chunk}\n```")

    @bot.command(name="ultimas", aliases=["ultimaspartidas", "recentes"])
    async def cmd_ultimas(ctx: commands.Context, member: discord.Member = None):
        if member is None and not ctx.message.mentions:
            matches = get_recent_league_matches(limit=20)
            if not matches:
                await ctx.send("📋 Nenhuma partida registrada ainda.")
                return
            match_ids = [m["league_match_id"] for m in matches]
            players_map = get_match_players_bulk(match_ids)
            lines = []
            for m in matches:
                dur = f" {m['duration']}" if m.get("duration") else ""
                winner = (m["winner_team"] or "?").capitalize()
                players = players_map.get(m["league_match_id"], [])
                radiant = [p["display_name"] for p in players if (p["team"] or "").lower() == "radiant"]
                dire    = [p["display_name"] for p in players if (p["team"] or "").lower() == "dire"]
                teams   = f"🟢 {', '.join(radiant)}  vs  🔴 {', '.join(dire)}" if radiant or dire else ""
                lines.append(f"#{m['season_match_id']} · {winner} venceu{dur}\n  {teams}")
            header = f"📋 **Últimas {len(matches)} partidas da liga** · use `!id <número>` para detalhes"
            text = "\n\n".join(lines)
            await ctx.send(header)
            chunk_size = 1800
            for i in range(0, len(text), chunk_size):
                await ctx.send(f"```\n{text[i:i+chunk_size]}\n```")
            return

        target = member or ctx.author
        history = get_player_match_history_from_matches(target.id, limit=200)

        if not history:
            who = f"**{target.display_name}**" if member else "você"
            await ctx.send(f"📋 Nenhuma partida encontrada para {who}.")
            return

        wins   = sum(1 for m in history if m["result"] == "win")
        losses = len(history) - wins

        lines = []
        for m in history:
            icon  = "✅" if m["result"] == "win" else "❌"
            hero  = (m["hero"] or "?").ljust(18)
            k, d, a = m.get("kills"), m.get("deaths"), m.get("assists")
            kda   = f"{k}/{d}/{a}" if k is not None and d is not None else "?/?/?"
            lines.append(f"{icon} #{str(m['season_match_id']).ljust(4)} {hero} {kda}")

        header = (
            f"📈 **Últimas {len(history)} partidas — {target.display_name}** "
            f"· {wins}V / {losses}D · use `!id <número>` para detalhes"
        )
        chunk_size = 1800
        text = "\n".join(lines)
        chunks = [text[i:i+chunk_size] for i in range(0, len(text), chunk_size)]

        await ctx.send(header)
        for chunk in chunks:
            await ctx.send(f"```\n{chunk}\n```")

    @bot.command(name="recordes", aliases=["tempos", "duracoes", "recordstime"])
    async def cmd_recordes(ctx: commands.Context):
        data    = get_match_duration_extremes(min_seconds=60)
        fastest = data["fastest"]
        longest = data["longest"]

        if not fastest and not longest:
            await ctx.send("📋 Nenhuma partida com duração registrada ainda.")
            return

        all_ids     = [m["league_match_id"] for m in fastest + longest]
        players_map = get_match_players_bulk(all_ids)

        def team_line(players: list[dict], team: str, winner: str) -> str:
            members = [p for p in players if (p["team"] or "").lower() == team.lower()]
            icon    = "🏆" if (winner or "").lower() == team.lower() else ("🟢" if team == "radiant" else "🔴")
            parts   = [
                f"{p['display_name']}({p['hero_name']})" if p.get("hero_name")
                else p["display_name"]
                for p in members
            ]
            return f"{icon} {' · '.join(parts)}" if parts else ""

        def match_field(m: dict, prefix: str) -> tuple[str, str]:
            score = ""
            if m.get("score_radiant") is not None and m.get("score_dire") is not None:
                score = f" · {m['score_radiant']}×{m['score_dire']}"
            winner  = (m["winner_team"] or "?").title()
            dur     = m.get("display_duration") or m["duration"]
            name    = f"{prefix} `#{m['season_match_id']}` · {dur} · {winner}{score}"
            players = players_map.get(m["league_match_id"], [])
            lines   = [l for l in [
                team_line(players, "radiant", m["winner_team"]),
                team_line(players, "dire",    m["winner_team"]),
            ] if l]
            value = "\n".join(lines) or "—"
            return name, value

        embed = discord.Embed(title="⏱️ Recordes de duração", color=discord.Color.teal())

        if fastest:
            embed.add_field(name="💥 STOMPS", value="​", inline=False)
            for i, m in enumerate(fastest):
                name, value = match_field(m, f"{i+1}.")
                embed.add_field(name=name, value=value, inline=False)

        if longest:
            embed.add_field(name="🐢 MAIS LONGAS", value="​", inline=False)
            for i, m in enumerate(longest):
                name, value = match_field(m, f"{i+1}.")
                embed.add_field(name=name, value=value, inline=False)

        embed.set_footer(text="Partidas com menos de 1 minuto ignoradas")
        await ctx.send(embed=embed)

    @bot.command(name="duelo", aliases=["vs", "versus", "rivalidade"])
    async def cmd_duelo(ctx: commands.Context, player_a: discord.Member, player_b: discord.Member = None):
        if player_b is None:
            player_a, player_b = ctx.author, player_a
        from collections import Counter

        matches = get_player_duo_stats(player_a.id, player_b.id)

        if not matches:
            await ctx.send(
                f"❌ **{player_a.display_name}** e **{player_b.display_name}** "
                f"ainda não jogaram nenhuma partida juntos."
            )
            return

        together = [m for m in matches if m["team_a"] == m["team_b"]]
        against  = [m for m in matches if m["team_a"] != m["team_b"]]

        # parceiros
        tog_wins   = sum(1 for m in together if m["winner_team"] == m["team_a"])
        tog_losses = len(together) - tog_wins
        tog_wr     = tog_wins * 100 / len(together) if together else 0

        # rivais
        a_wins = sum(1 for m in against if m["winner_team"] == m["team_a"])
        b_wins = len(against) - a_wins

        # sequência atual no confronto direto
        streak_name  = ""
        streak_count = 0
        if against:
            current = "a" if against[0]["winner_team"] == against[0]["team_a"] else "b"
            for m in against:
                w = "a" if m["winner_team"] == m["team_a"] else "b"
                if w == current:
                    streak_count += 1
                else:
                    break
            streak_name = player_a.display_name if current == "a" else player_b.display_name

        # par de heróis mais frequente
        top_pair  = Counter(
            (m["hero_a"] or "?", m["hero_b"] or "?") for m in together
        ).most_common(1)
        top_clash = Counter(
            (m["hero_a"] or "?", m["hero_b"] or "?") for m in against
        ).most_common(1)

        if a_wins > b_wins:
            color = discord.Color.blue()
        elif b_wins > a_wins:
            color = discord.Color.red()
        else:
            color = discord.Color.purple()

        embed = discord.Embed(
            title=f"⚔️  {player_a.display_name}  ×  {player_b.display_name}",
            description=f"**{len(matches)}** partidas em que se encontraram",
            color=color,
        )

        # campo parceiros
        if together:
            tog_lines = [f"**{len(together)}** jogos · {tog_wins}V / {tog_losses}D · {tog_wr:.0f}% WR"]
            if top_pair and top_pair[0][1] >= 2:
                (ha, hb), cnt = top_pair[0]
                tog_lines.append(f"Par favorito: **{ha}** + **{hb}** ({cnt}×)")
            embed.add_field(name="🤝 Como parceiros", value="\n".join(tog_lines), inline=True)
        else:
            embed.add_field(name="🤝 Como parceiros", value="Nunca jogaram juntos", inline=True)

        # campo rivais
        if against:
            diff = abs(a_wins - b_wins)
            if a_wins > b_wins:
                advantage = f"Vantagem: **{player_a.display_name}** (+{diff})"
            elif b_wins > a_wins:
                advantage = f"Vantagem: **{player_b.display_name}** (+{diff})"
            else:
                advantage = "Placar zerado ⚖️"
            a_wr = a_wins * 100 // len(against)
            b_wr = b_wins * 100 // len(against)
            against_lines = [
                f"**{len(against)}** jogos",
                f"**{player_a.display_name}**: {a_wins}V · {a_wr}% WR",
                f"**{player_b.display_name}**: {b_wins}V · {b_wr}% WR",
                advantage,
            ]
            if top_clash and top_clash[0][1] >= 2:
                (ha, hb), cnt = top_clash[0]
                against_lines.append(f"Duelo favorito: **{ha}** × **{hb}** ({cnt}×)")
            embed.add_field(name="⚔️ Como rivais", value="\n".join(against_lines), inline=True)
        else:
            embed.add_field(name="⚔️ Como rivais", value="Nunca jogaram contra", inline=True)

        # sequência
        if streak_count >= 2:
            embed.add_field(
                name="🔥 Sequência atual",
                value=f"**{streak_name}** venceu os últimos **{streak_count}** confrontos diretos",
                inline=False,
            )

        # últimas partidas
        recent = matches[:30]
        lines  = []
        for m in recent:
            same = m["team_a"] == m["team_b"]
            ha   = (m["hero_a"] or "?")[:12]
            hb   = (m["hero_b"] or "?")[:12]
            mid  = str(m["season_match_id"]).ljust(3)
            if same:
                icon = "✅" if m["winner_team"] == m["team_a"] else "❌"
                lines.append(f"{icon}`#{mid}` {ha}+{hb}")
            else:
                icon = "🔵" if m["winner_team"] == m["team_a"] else "🔴"
                lines.append(f"{icon}`#{mid}` {ha}×{hb}")

        FIELD_LIMIT = 1000
        chunks: list[list[str]] = []
        current: list[str] = []
        for line in lines:
            if len("\n".join(current + [line])) > FIELD_LIMIT:
                if current:
                    chunks.append(current)
                current = [line]
            else:
                current.append(line)
        if current:
            chunks.append(current)

        for i, chunk in enumerate(chunks):
            name = f"🕹️ Últimas {len(recent)} partidas" if i == 0 else "↪️ continuação"
            embed.add_field(name=name, value="\n".join(chunk), inline=False)
        embed.set_footer(
            text=f"🔵 {player_a.display_name} venceu · 🔴 {player_b.display_name} venceu · ✅/❌ juntos"
        )
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
                    f"{icon} **#{m['season_match_id']}** · {m['display_name']} ({team}) · `{kda}`"
                )

            field_chunks: list[list[str]] = [[]]
            for line in lines:
                current_val = "\n".join(field_chunks[-1])
                if len(current_val) + len(line) + 1 > 1000:
                    field_chunks.append([])
                field_chunks[-1].append(line)

            for idx, chunk in enumerate(field_chunks):
                name = "Partidas" if idx == 0 else "​"
                embed.add_field(name=name, value="\n".join(chunk) or "—", inline=False)

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

    @bot.command(name="free", aliases=["gratis", "gratuito", "trial"])
    async def cmd_free(ctx: commands.Context):
        user_id = ctx.author.id
        display_name = ctx.author.display_name
        season = get_current_season()

        if is_pagante(user_id):
            await ctx.send(
                f"✅ {ctx.author.mention} você já está registrado como pagante desta temporada — pode entrar nos lobbies normalmente!",
                delete_after=15,
            )
            return

        if is_free_player(user_id):
            fp = get_free_player(user_id)
            played = fp["matches_played"]
            remaining = max(0, FREE_MATCH_LIMIT - played)
            if remaining > 0:
                await ctx.send(
                    f"🎮 {ctx.author.mention} você já está no **trial gratuito**!\n"
                    f"📊 Partidas jogadas: **{played}/{FREE_MATCH_LIMIT}** · Restantes: **{remaining}**\n"
                    f"Entre em um lobby clicando em ✋ Entrar.",
                    delete_after=20,
                )
            else:
                await ctx.send(
                    f"❌ {ctx.author.mention} você já utilizou seus **{FREE_MATCH_LIMIT} jogos gratuitos** desta temporada.\n"
                    "Para continuar jogando, envie um Pix de **R$ 10,00** para Smith (chave: **(71) 99146-9980**) e avise um administrador.",
                    delete_after=20,
                )
            return

        add_free_player(user_id, display_name, season)
        logger.info(f"[Free] {ctx.author} ({user_id}) registrado no trial gratuito T{season}")
        await ctx.send(
            f"🎉 {ctx.author.mention} você foi registrado no **trial gratuito**!\n"
            f"Você pode participar de até **{FREE_MATCH_LIMIT} partidas** sem pagar.\n"
            f"Após isso, será necessário pagar para continuar jogando.\n\n"
            f"Entre em um lobby clicando em ✋ Entrar. Boa sorte! 🍀",
        )

    @bot.command(name="meusteam", aliases=["friendid", "steamid"])
    async def cmd_friendid(ctx: commands.Context, friend_id: str = None):
        if friend_id is None:
            current = get_steam_friend_id(ctx.author.id)
            if current is None:
                await ctx.send(
                    "❌ Você ainda não cadastrou seu Friend ID.\n\n"
                    "**Como encontrar:**\n"
                    "1. Abra o Steam\n"
                    "2. Clique em **Amigos e conversas** (canto inferior direito)\n"
                    "3. Clique no botão **Adicionar Amigo** (ícone de pessoa com +)\n"
                    "4. Seu **Friend Code** aparecerá em destaque — clique em Copiar\n"
                    "5. Use `!meusteam <seu friend code>` para cadastrar."
                )
            else:
                await ctx.send(f"🎮 Seu Steam Friend ID cadastrado: `{current}`")
            return

        try:
            fid = int(friend_id.strip())
        except ValueError:
            await ctx.send("❌ Friend ID inválido. Use apenas números.")
            return

        if fid <= 0:
            await ctx.send("❌ Friend ID inválido.")
            return

        try:
            set_steam_friend_id(ctx.author.id, fid)
            await ctx.send(f"✅ Steam Friend ID `{fid}` cadastrado! Use `!meusteam` para confirmar.")
            logger.info(f"[Steam] {ctx.author} ({ctx.author.id}) cadastrou friend_id={fid}")
        except ValueError as e:
            await ctx.send(f"❌ {e}")

    @bot.command(name="mmr", aliases=["mmrs", "vermmr", "mmrlist"])
    async def cmd_mmr(ctx: commands.Context):
        season = get_current_season()
        pagantes = list_pagantes(season)

        com_mmr = sorted(
            [p for p in pagantes if p.get("mmr") is not None],
            key=lambda p: p["mmr"],
            reverse=True,
        )
        sem_mmr = [p for p in pagantes if p.get("mmr") is None]

        embed = discord.Embed(
            title=f"📊 MMR dos Jogadores — Temporada {season}",
            color=discord.Color.blue(),
        )

        if not pagantes:
            embed.description = "Nenhum jogador registrado nesta temporada."
            await ctx.send(embed=embed)
            return

        medals = {0: "🥇", 1: "🥈", 2: "🥉"}
        lines = []
        for i, p in enumerate(com_mmr):
            prefix = medals.get(i, f"{i+1}.")
            lines.append(f"{prefix} <@{p['discord_id']}> — **{p['mmr']:,}** MMR".replace(",", "."))

        for p in sem_mmr:
            lines.append(f"• <@{p['discord_id']}> — ⚠️ sem MMR")

        embed.description = "\n".join(lines)
        embed.set_footer(text=f"{len(com_mmr)} jogador(es) com MMR · {len(sem_mmr)} sem MMR")
        await ctx.send(embed=embed)
