"""Market categories for the category_picker control — the live pylzt ``Category`` enum, labelled.

The slug set comes from pylzt itself (the identifier the market API uses, so no guessed numeric
ids); only the human labels are ours. A pylzt upgrade that adds a category surfaces it
automatically with a title-cased fallback label, and ``test_market_categories`` fails loudly so the
label can be written properly.

Lives in the market domain because pylzt is a marketplace dependency: it is the market layer's job
to know what the marketplace sells, and the API layer's job to serialize the answer.
"""

from __future__ import annotations

from enum import StrEnum
from typing import NamedTuple

import pylzt


class MarketCategory(NamedTuple):
    slug: str
    label: str


class SearchableCategory(StrEnum):
    """The categories ``market.search`` can actually query.

    Narrower than ``MARKET_CATEGORIES`` on purpose: the label map covers everything the market
    sells, but the pylzt facade only exposes a ``category_*`` method for these. ``mihoyo``, ``wot``,
    ``wotblitz``, ``vkontakte`` and ``other`` are labelled and unsearchable — listing them here
    would surface a picker entry that fails at call time.

    Being an enum is what makes it one source of truth: the JSON schema emits the choices for the
    picker, ``MarketAdapter._CATEGORY_METHODS`` is keyed by it, and ``SearchableCategory(raw)``
    rejects an unknown slug in the node — no parallel frozenset to drift.
    """

    STEAM = "steam"
    FORTNITE = "fortnite"
    RIOT = "riot"
    TELEGRAM = "telegram"
    DISCORD = "discord"
    ROBLOX = "roblox"
    EPICGAMES = "epicgames"
    BATTLENET = "battlenet"
    EA = "ea"
    ESCAPEFROMTARKOV = "escapefromtarkov"
    GIFTS = "gifts"
    INSTAGRAM = "instagram"
    MINECRAFT = "minecraft"
    SOCIALCLUB = "socialclub"
    SUPERCELL = "supercell"
    TIKTOK = "tiktok"
    UPLAY = "uplay"
    VPN = "vpn"
    WARFACE = "warface"
    HYTALE = "hytale"
    LLM = "llm"


# Keyed by the pylzt Category value (slug). Order is display order in the picker.
MARKET_CATEGORIES: tuple[MarketCategory, ...] = (
    MarketCategory("steam", "Steam"),
    MarketCategory("fortnite", "Fortnite"),
    MarketCategory("riot", "Riot (Valorant / LoL)"),
    MarketCategory("telegram", "Telegram"),
    MarketCategory("discord", "Discord"),
    MarketCategory("roblox", "Roblox"),
    MarketCategory("epicgames", "Epic Games"),
    MarketCategory("battlenet", "Battle.net"),
    MarketCategory("ea", "EA"),
    MarketCategory("escapefromtarkov", "Escape from Tarkov"),
    MarketCategory("gifts", "Gifts"),
    MarketCategory("instagram", "Instagram"),
    MarketCategory("minecraft", "Minecraft"),
    MarketCategory("mihoyo", "miHoYo (Genshin / HSR)"),
    MarketCategory("socialclub", "Social Club"),
    MarketCategory("supercell", "Supercell"),
    MarketCategory("tiktok", "TikTok"),
    MarketCategory("uplay", "Uplay"),
    MarketCategory("vpn", "VPN"),
    MarketCategory("warface", "Warface"),
    MarketCategory("wot", "World of Tanks"),
    MarketCategory("wotblitz", "WoT Blitz"),
    MarketCategory("hytale", "Hytale"),
    MarketCategory("llm", "LLM / AI"),
    MarketCategory("vkontakte", "VK"),
    MarketCategory("other", "Other"),
)

_LABELS = {c.slug: c.label for c in MARKET_CATEGORIES}


def list_categories() -> list[MarketCategory]:
    """Every category pylzt knows, labelled. Unknown slugs fall back to a title-cased slug rather
    than being dropped — a category the picker cannot name is still a category flows can target."""
    return [
        MarketCategory(slug=cat.value, label=_LABELS.get(cat.value, cat.value.title()))
        for cat in pylzt.Category
    ]
