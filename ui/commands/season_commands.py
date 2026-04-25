# -*- coding: utf-8 -*-
import asyncio
import logging
from datetime import datetime as _dt

import discord
from discord.ext import commands

from core.db.match_repo import (
    get_ranking_from_matches,
    get_season_summary_stats,
    get_mvp_award_stats,
    get_pairwise_head_to_head,
)
from core.utils.time import format_brazil_time


def setup_season_commands(bot: commands.Bot):
    logger = logging.getLogger("SeasonCommands")
    logger.info("Carregando comandos de temporada...")

    # ──────────────────────────────────────────────────────────
    # !mvp — prêmios por categoria
    # ──────────────────────────────────────────────────────────

    @bot.command(name="mvp", aliases=["premios", "awards", "premiacao"])
    async def cmd_mvp(ctx: commands.Context):
        awards = get_mvp_award_stats()
        ranking = get_ranking_from_matches()

        if not ranking:
            await ctx.send("📋 Nenhuma partida registrada ainda.")
            return

        embed = discord.Embed(
            title="🏅 Premiação da Temporada",
            description="Os melhores da liga em cada categoria",
            color=discord.Color.gold(),
        )

        mvp_overall = ranking[0]
        wr = mvp_overall["wins"] / mvp_overall["games"] * 100 if mvp_overall["games"] else 0
        embed.add_field(
            name="👑 MVP Geral",
            value=(
                f"**{mvp_overall['display_name']}** — {mvp_overall['points']} pts · "
                f"{mvp_overall['wins']}V/{mvp_overall['losses']}D · {wr:.0f}% WR"
            ),
            inline=False,
        )

        if awards["top_killer"]:
            a = awards["top_killer"]
            embed.add_field(
                name="🗡️ Maior Destruidor",
                value=f"**{a['display_name']}** — {a['value']} kills totais em {a['games']} jogos",
                inline=False,
            )

        if awards["top_deaths"]:
            a = awards["top_deaths"]
            embed.add_field(
                name="💀 Maior Isco",
                value=f"**{a['display_name']}** — {a['value']} mortes totais em {a['games']} jogos",
                inline=False,
            )

        if awards["top_assists"]:
            a = awards["top_assists"]
            embed.add_field(
                name="🤝 Suporte Dedicado",
                value=f"**{a['display_name']}** — {a['value']} assists totais em {a['games']} jogos",
                inline=False,
            )

        if awards["top_winrate"]:
            a = awards["top_winrate"]
            embed.add_field(
                name="📈 Maior Winrate",
                value=f"**{a['display_name']}** — {a['value']:.1f}% WR ({a['wins']}V em {a['games']} jogos)",
                inline=False,
            )

        if awards["top_kda"]:
            a = awards["top_kda"]
            n = a["games"]
            embed.add_field(
                name="⚔️ Melhor KDA Médio",
                value=(
                    f"**{a['display_name']}** — "
                    f"{a['kills']/n:.1f}/{a['deaths']/n:.1f}/{a['assists']/n:.1f} "
                    f"em {n} jogos (ratio {a['kda_ratio']:.2f})"
                ),
                inline=False,
            )

        embed.set_footer(text="Mínimo 3 jogos para kills/mortes/assists · mínimo 5 para winrate e KDA")
        await ctx.send(embed=embed)

    # ──────────────────────────────────────────────────────────
    # !historia — recap narrativo da temporada
    # ──────────────────────────────────────────────────────────

    @bot.command(name="historia", aliases=["recap", "temporada", "season"])
    async def cmd_historia(ctx: commands.Context):
        stats = get_season_summary_stats()
        ranking = get_ranking_from_matches()

        if not stats["total_matches"]:
            await ctx.send("📋 Nenhuma partida registrada ainda.")
            return

        secs = stats["total_seconds"]
        hours = secs // 3600
        minutes = (secs % 3600) // 60
        time_str = f"{hours}h{minutes:02d}min" if hours else f"{minutes}min"

        desc_parts = [
            f"A liga disputou **{stats['total_matches']} partidas** ao longo da temporada,",
            f"reunindo **{stats['total_players']} jogadores** nas batalhas.",
        ]
        if secs:
            desc_parts.append(f"No total foram **{time_str}** de Dota puro.")
        if stats["top_hero"]:
            desc_parts.append(
                f"O herói mais convocado foi **{stats['top_hero']}**, escolhido {stats['top_hero_picks']}×."
            )

        embed = discord.Embed(
            title="📜 História da Temporada",
            description=" ".join(desc_parts),
            color=discord.Color.blurple(),
        )

        kills_line = []
        if stats["total_kills"]:
            kills_line.append(f"🗡️ **{stats['total_kills']}** kills")
        if stats["total_deaths"]:
            kills_line.append(f"💀 **{stats['total_deaths']}** mortes")
        if stats["total_assists"]:
            kills_line.append(f"🤝 **{stats['total_assists']}** assists")
        if kills_line:
            embed.add_field(name="📊 KDA da Liga", value=" · ".join(kills_line), inline=False)

        if ranking:
            medals = ["🥇", "🥈", "🥉"]
            podium = []
            for i, p in enumerate(ranking[:3]):
                wr = p["wins"] / p["games"] * 100 if p["games"] else 0
                podium.append(
                    f"{medals[i]} **{p['display_name']}** — {p['points']}pts · "
                    f"{p['wins']}V/{p['losses']}D · {wr:.0f}% WR"
                )
            embed.add_field(name="🏆 Pódio Final", value="\n".join(podium), inline=False)

        if stats["first_match"] and stats["last_match"]:
            try:
                first = format_brazil_time(stats["first_match"])
                last = format_brazil_time(stats["last_match"])
                embed.add_field(
                    name="📅 Período da Temporada",
                    value=f"Início: {first}\nFim: {last}",
                    inline=False,
                )
            except Exception:
                pass

        embed.set_footer(text="Uma temporada memorável. Até a próxima! 🎮")
        await ctx.send(embed=embed)

    # ──────────────────────────────────────────────────────────
    # !bracket — confrontos históricos entre os top 8
    # ──────────────────────────────────────────────────────────

    @bot.command(name="bracket", aliases=["chaveamento", "playoffs"])
    async def cmd_bracket(ctx: commands.Context):
        ranking = get_ranking_from_matches()

        if len(ranking) < 4:
            await ctx.send("❌ Jogadores insuficientes para montar o bracket (mínimo 4).")
            return

        top8 = ranking[:8]
        ids = [p["discord_id"] for p in top8]
        h2h = get_pairwise_head_to_head(ids)
        n = len(top8)

        embed = discord.Embed(
            title=f"🏟️ Bracket da Liga — Top {n}",
            description="Confrontos históricos entre os melhores da temporada",
            color=discord.Color.dark_purple(),
        )

        for i in range(n // 2):
            a = top8[i]
            b = top8[n - 1 - i]
            id_a, id_b = a["discord_id"], b["discord_id"]
            rank_a, rank_b = i + 1, n - i

            id_low, id_high = min(id_a, id_b), max(id_a, id_b)
            matchup = h2h.get((id_low, id_high), {})
            total = matchup.get("total", 0)

            if id_a == id_low:
                wins_a = matchup.get("wins_low", 0)
                wins_b = matchup.get("wins_high", 0)
            else:
                wins_a = matchup.get("wins_high", 0)
                wins_b = matchup.get("wins_low", 0)

            if total == 0:
                icon = "🔘"
                result_line = "Sem confronto direto na temporada"
            elif wins_a > wins_b:
                icon = "🔵"
                result_line = f"Vantagem **{a['display_name']}** ({wins_a}×{wins_b}) em {total} confrontos"
            elif wins_b > wins_a:
                icon = "🔴"
                result_line = f"Vantagem **{b['display_name']}** ({wins_b}×{wins_a}) em {total} confrontos"
            else:
                icon = "⚖️"
                result_line = f"Empate perfeito ({wins_a}×{wins_b}) em {total} confrontos"

            field_name = (
                f"{icon} #{rank_a} {a['display_name']} × #{rank_b} {b['display_name']}"
            )
            pts_line = (
                f"{a['display_name']}: {a['points']}pts · "
                f"{b['display_name']}: {b['points']}pts"
            )
            embed.add_field(name=field_name, value=f"{result_line}\n{pts_line}", inline=False)

        embed.set_footer(text="🔵 primeiro lidera · 🔴 segundo lidera · ⚖️ empate · 🔘 sem dados")
        await ctx.send(embed=embed)

    # ──────────────────────────────────────────────────────────
    # !campeon — reveal dramático do campeão
    # ──────────────────────────────────────────────────────────

    @bot.command(name="campeon", aliases=["campeao", "campeão", "champion", "reveal"])
    async def cmd_campeon(ctx: commands.Context):
        ranking = get_ranking_from_matches()

        if not ranking:
            await ctx.send("❌ Nenhum jogador registrado ainda.")
            return

        await ctx.send(
            "🎺 **Senhoras e senhores... chegou o momento!**\n"
            "A temporada chegou ao fim. Revelando o pódio da liga..."
        )
        await asyncio.sleep(3)

        if len(ranking) >= 3:
            p3 = ranking[2]
            wr3 = p3["wins"] / p3["games"] * 100 if p3["games"] else 0
            embed3 = discord.Embed(
                title="🥉 3º Lugar",
                description=f"**{p3['display_name']}**",
                color=discord.Color.from_rgb(205, 127, 50),
            )
            embed3.add_field(name="Pontuação", value=f"{p3['points']} pts", inline=True)
            embed3.add_field(name="Partidas", value=f"{p3['wins']}V / {p3['losses']}D", inline=True)
            embed3.add_field(name="Winrate", value=f"{wr3:.0f}%", inline=True)
            await ctx.send(embed=embed3)
            await asyncio.sleep(4)

        if len(ranking) >= 2:
            p2 = ranking[1]
            wr2 = p2["wins"] / p2["games"] * 100 if p2["games"] else 0
            embed2 = discord.Embed(
                title="🥈 2º Lugar",
                description=f"**{p2['display_name']}**",
                color=discord.Color.light_grey(),
            )
            embed2.add_field(name="Pontuação", value=f"{p2['points']} pts", inline=True)
            embed2.add_field(name="Partidas", value=f"{p2['wins']}V / {p2['losses']}D", inline=True)
            embed2.add_field(name="Winrate", value=f"{wr2:.0f}%", inline=True)
            await ctx.send(embed=embed2)
            await asyncio.sleep(5)

        p1 = ranking[0]
        wr1 = p1["wins"] / p1["games"] * 100 if p1["games"] else 0
        embed1 = discord.Embed(
            title="🏆 CAMPEÃO DA LIGA!",
            description=f"# {p1['display_name']}",
            color=discord.Color.gold(),
        )
        embed1.add_field(name="Pontuação Final", value=f"**{p1['points']} pts**", inline=True)
        embed1.add_field(name="Partidas", value=f"**{p1['wins']}V / {p1['losses']}D**", inline=True)
        embed1.add_field(name="Winrate", value=f"**{wr1:.0f}%**", inline=True)
        embed1.set_footer(text="Parabéns ao campeão da temporada!")
        await ctx.send("🎊 🎊 🎊", embed=embed1)
