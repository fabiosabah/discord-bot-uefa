# -*- coding: utf-8 -*-
import re
from difflib import get_close_matches
from typing import Any

HERO_NAMES = [
    "Abaddon",
    "Alchemist",
    "Ancient Apparition",
    "Anti-Mage",
    "Arc Warden",
    "Axe",
    "Bane",
    "Batrider",
    "Beastmaster",
    "Bloodseeker",
    "Bounty Hunter",
    "Brewmaster",
    "Bristleback",
    "Broodmother",
    "Centaur Warrunner",
    "Chaos Knight",
    "Chen",
    "Clinkz",
    "Clockwerk",
    "Crystal Maiden",
    "Dark Seer",
    "Dark Willow",
    "Dazzle",
    "Dawnbreaker",
    "Death Prophet",
    "Disruptor",
    "Doom",
    "Dragon Knight",
    "Drow Ranger",
    "Earth Spirit",
    "Earthshaker",
    "Elder Titan",
    "Ember Spirit",
    "Enchantress",
    "Enigma",
    "Faceless Void",
    "Grimstroke",
    "Gyrocopter",
    "Hoodwink",
    "Huskar",
    "Invoker",
    "Io",
    "Jakiro",
    "Juggernaut",
    "Keeper of the Light",
    "Kez",
    "Kunkka",
    "Largo",
    "Legion Commander",
    "Leshrac",
    "Lich",
    "Lifestealer",
    "Lina",
    "Lion",
    "Lone Druid",
    "Luna",
    "Lycan",
    "Magnus",
    "Marci",
    "Mars",
    "Medusa",
    "Meepo",
    "Mirana",
    "Monkey King",
    "Morphling",
    "Muerta",
    "Naga Siren",
    "Nature's Prophet",
    "Necrophos",
    "Night Stalker",
    "Nyx Assassin",
    "Ogre Magi",
    "Omniknight",
    "Oracle",
    "Outworld Destroyer",
    "Pangolier",
    "Phantom Assassin",
    "Phantom Lancer",
    "Phoenix",
    "Primal Beast",
    "Puck",
    "Pudge",
    "Pugna",
    "Queen of Pain",
    "Razor",
    "Ringmaster",
    "Riki",
    "Rubick",
    "Sand King",
    "Shadow Demon",
    "Shadow Fiend",
    "Shadow Shaman",
    "Silencer",
    "Skywrath Mage",
    "Slardar",
    "Slark",
    "Snapfire",
    "Sniper",
    "Spectre",
    "Spirit Breaker",
    "Storm Spirit",
    "Sven",
    "Techies",
    "Templar Assassin",
    "Terrorblade",
    "Tidehunter",
    "Timbersaw",
    "Tinker",
    "Tiny",
    "Treant Protector",
    "Troll Warlord",
    "Tusk",
    "Underlord",
    "Undying",
    "Ursa",
    "Vengeful Spirit",
    "Venomancer",
    "Viper",
    "Visage",
    "Void Spirit",
    "Warlock",
    "Weaver",
    "Windranger",
    "Winter Wyvern",
    "Witch Doctor",
    "Wraith King",
    "Zeus",
]

NORMALIZED_HERO_LOOKUP = {
    re.sub(r"[^a-z0-9]+", "", hero.lower()): hero
    for hero in HERO_NAMES
}

STOP_WORDS = {"of", "the", "and", "a", "an", "to", "in", "for", "with", "by", "on", "at", "from", "into", "s"}


def normalize_hero_name(value: str) -> str:
    return re.sub(r"[^a-z0-9]+", "", value.strip().lower())


def hero_initials(value: str) -> str:
    words = re.findall(r"[a-z0-9]+", value.lower())
    initials = "".join(word[0] for word in words if word not in STOP_WORDS)
    if not initials:
        initials = "".join(word[0] for word in words)
    return initials


HERO_INITIALS_LOOKUP: dict[str, list[str]] = {}
for hero in HERO_NAMES:
    initials = hero_initials(hero)
    if initials:
        HERO_INITIALS_LOOKUP.setdefault(initials, []).append(hero)


def resolve_hero_name(value: str, min_characters: int = 2) -> tuple[str | None, list[str], str]:
    normalized = normalize_hero_name(value)
    if not normalized:
        return None, [], "empty"

    exact = NORMALIZED_HERO_LOOKUP.get(normalized)
    if exact:
        return exact, [], "exact"

    prefix_matches = [hero for key, hero in NORMALIZED_HERO_LOOKUP.items() if key.startswith(normalized)]
    if len(prefix_matches) == 1:
        return prefix_matches[0], [], "exact"
    if len(prefix_matches) > 1:
        if len(normalized) < min_characters or len(prefix_matches) > 5:
            return None, prefix_matches[:5], "ambiguous"
        return None, prefix_matches[:5], "ambiguous"

    initials_matches = HERO_INITIALS_LOOKUP.get(normalized, [])
    if len(initials_matches) == 1:
        return initials_matches[0], [], "exact"
    if len(initials_matches) > 1:
        return None, initials_matches[:5], "ambiguous"

    initials_prefix_matches = [hero for key, heroes in HERO_INITIALS_LOOKUP.items() if key.startswith(normalized) for hero in heroes]
    if len(initials_prefix_matches) == 1:
        return initials_prefix_matches[0], [], "exact"
    if len(initials_prefix_matches) > 1:
        if len(normalized) < min_characters or len(initials_prefix_matches) > 5:
            return None, initials_prefix_matches[:5], "ambiguous"
        return None, initials_prefix_matches[:5], "ambiguous"

    close_matches = get_close_matches(normalized, NORMALIZED_HERO_LOOKUP.keys(), n=5, cutoff=0.6)
    suggestions = [NORMALIZED_HERO_LOOKUP[key] for key in close_matches]
    if len(suggestions) == 1:
        return suggestions[0], [], "exact"
    if suggestions:
        return None, suggestions, "none"

    return None, [], "none"


def format_hero_suggestions(suggestions: list[str]) -> str:
    if not suggestions:
        return "Nenhum herói semelhante encontrado."
    return ", ".join(suggestions)
