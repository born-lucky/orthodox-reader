"""Shuffled decks, so the readings never repeat until every one has been read.

True randomness repeats itself: with a few hundred passages, the same one comes back
within days. A deck is shuffled once and dealt in order; a card is not seen again
until the whole deck has been dealt, and the next shuffle never starts with the card
that ended the last one. The decks (and reading positions, like where the Gospels
were left off) are kept in %APPDATA%\\OrthodoxReader\\decks.json, so this holds across
breaks, restarts and days.

Cards are strings. A deck grows by itself when new cards appear (more days of the
calendar fetched): new cards are shuffled into what is left, nothing is reset.
"""

from __future__ import annotations

import json
import random
import threading

from . import settings

PATH = settings.HOME / "decks.json"
_lock = threading.Lock()


def _load() -> dict:
    try:
        return json.loads(PATH.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}


def _save(data: dict) -> None:
    tmp = PATH.with_suffix(".tmp")
    tmp.write_text(json.dumps(data), encoding="utf-8")
    tmp.replace(PATH)


def deal(name: str, cards: list[str], rng: random.Random, fits=None) -> str | None:
    """The next card of the deck `name` drawn from `cards`; None if there are none.
    A card for which fits(card) is false (say, too long for the time left) is passed
    over and stays in the deck for later."""
    if not cards:
        return None
    fits = fits or (lambda card: True)
    with _lock:
        data = _load()
        deck = data.setdefault(name, {"order": [], "used": [], "last": None})
        known = set(cards)
        used = [c for c in deck["used"] if c in known]
        used_set = set(used)
        order = [c for c in deck["order"] if c in known and c not in used_set]
        in_order = set(order)
        fresh = [c for c in cards if c not in used_set and c not in in_order]
        for card in fresh:  # new cards go into the unread part at random places
            order.insert(rng.randint(0, len(order)), card)
        if not order:  # every card has been read: shuffle again, not starting with the last
            order = list(cards)
            rng.shuffle(order)
            if len(order) > 1 and order[0] == deck.get("last"):
                order.append(order.pop(0))
            used = []
        card = next((c for c in order[:400] if fits(c)), None)
        if card is None:
            deck.update(order=order, used=used)
            _save(data)
            return None
        order.remove(card)
        used.append(card)
        deck.update(order=order, used=used, last=card)
        _save(data)
        return card


def position(name: str, step: int = 1) -> int:
    """A reading position that moves on each time (the next kathisma, the next chapter)."""
    with _lock:
        data = _load()
        places = data.setdefault("_positions", {})
        here = int(places.get(name, 0))
        places[name] = here + step
        _save(data)
        return here
