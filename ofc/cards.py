"""Card encoding for the OFC bot.

This module is the only place in the OFC package that converts between the
three card encodings this project has to deal with. Mixing them up produces
wrong cards silently instead of raising, so nothing outside this module
should do arithmetic on a card value.

  wire  PPPoker packet integer, ``(suit << 8) | rank``, rank 2..14,
        suit 1=d 2=c 3=h 4=s.  This is what the Frida hook delivers and
        what ``hook/packet_capture.decode_card`` already understands.

  text  Two characters, ``'Ah'`` / ``'Td'`` / ``'2s'``.  The portable form:
        it is what goes in the log, the GUI, the hand database, and what
        the pineapple engine's ``Card.from_string`` accepts.

  code  ``rank_index * 4 + suit_index``, 0..51.  Rank ``'2'``->0 .. ``'A'``->12,
        suit ``'s'``->0 ``'h'``->1 ``'d'``->2 ``'c'``->3.  The compact form
        the evaluator and the Monte-Carlo rollout work in, chosen so that
        ``code // 4`` is the rank and ``code % 4`` is the suit.

Jokers are deliberately NOT representable as a ``code``.  The stock PPPoker
OFC tables this bot targets are jokerless, and a silent joker would corrupt
every rollout.  ``wire_to_text`` returns ``''`` for anything it cannot decode
so callers can detect and refuse the hand rather than play it blind.
"""

from typing import Iterable, List, Optional, Sequence

RANKS = "23456789TJQKA"
SUITS = "shdc"

RANK_INDEX = {r: i for i, r in enumerate(RANKS)}
SUIT_INDEX = {s: i for i, s in enumerate(SUITS)}

# Wire format, mirroring hook/packet_capture.decode_card.
_WIRE_RANK = {2: "2", 3: "3", 4: "4", 5: "5", 6: "6", 7: "7", 8: "8", 9: "9",
              10: "T", 11: "J", 12: "Q", 13: "K", 14: "A"}
_WIRE_SUIT = {1: "d", 2: "c", 3: "h", 4: "s"}

FULL_DECK: List[int] = list(range(52))


# --------------------------------------------------------------- wire -> text

def wire_to_text(raw: int) -> str:
    """Decode one PPPoker packet integer into ``'Ah'`` form.

    Returns ``''`` when the value is not a card this bot can play with —
    padding zeros, jokers, or anything else unexpected. Callers must treat
    an empty string as "unknown card" and refuse to solve, never as a card.
    """
    if not isinstance(raw, int) or raw <= 0:
        return ""
    rank = _WIRE_RANK.get(raw & 0xFF)
    suit = _WIRE_SUIT.get((raw >> 8) & 0xFF)
    if rank is None or suit is None:
        return ""
    return rank + suit


def wire_list_to_text(raws: Iterable[int]) -> List[str]:
    """Decode a packet's card array, dropping padding but keeping unknowns.

    Zeros are padding and are dropped. A non-zero value that fails to decode
    is kept as ``'??'`` so the caller can see that the hand is incomplete
    rather than silently solving a short hand.
    """
    out: List[str] = []
    for raw in raws or ():
        if not isinstance(raw, int) or raw <= 0:
            continue
        text = wire_to_text(raw)
        out.append(text if text else "??")
    return out


# --------------------------------------------------------------- text <-> code

def text_to_code(text: str) -> int:
    """``'Ah'`` -> 0..51. Raises ValueError on anything else."""
    if not isinstance(text, str) or len(text) != 2:
        raise ValueError(f"not a card: {text!r}")
    rank = RANK_INDEX.get(text[0].upper())
    suit = SUIT_INDEX.get(text[1].lower())
    if rank is None or suit is None:
        raise ValueError(f"not a card: {text!r}")
    return rank * 4 + suit


def code_to_text(code: int) -> str:
    """0..51 -> ``'Ah'``. Raises ValueError outside the deck."""
    if not isinstance(code, int) or not 0 <= code < 52:
        raise ValueError(f"card code out of range: {code!r}")
    return RANKS[code // 4] + SUITS[code % 4]


def texts_to_codes(texts: Sequence[str]) -> List[int]:
    return [text_to_code(t) for t in texts]


def codes_to_texts(codes: Sequence[int]) -> List[str]:
    return [code_to_text(c) for c in codes]


def is_playable(texts: Iterable[str]) -> bool:
    """True when every entry decodes to a real card.

    The solver refuses to run on a hand that fails this: an unknown card
    means the packet carried something the decoder does not model, and a
    guess would be worse than no advice.
    """
    for t in texts:
        try:
            text_to_code(t)
        except ValueError:
            return False
    return True


# --------------------------------------------------------------- deck helpers

def remaining_deck(seen: Iterable[int]) -> List[int]:
    """Every card not in ``seen``, as codes.

    ``seen`` is the dead-card set: hero's own cards, hero's discards, and —
    the part that makes this bot better informed than an offline solver —
    every card visible on an opponent's board. In OFC those are public the
    moment they are placed, and the packet feed delivers them immediately.
    """
    dead = set(seen)
    return [c for c in FULL_DECK if c not in dead]


def format_cards(texts: Sequence[str]) -> str:
    """Human-readable row, e.g. ``'Ah Kd 7c'``. Empty rows render as ``'-'``."""
    return " ".join(texts) if texts else "-"


# --------------------------------------------------------------- pretty print

_SUIT_SYMBOL = {"s": "♠", "h": "♥", "d": "♦", "c": "♣"}


def pretty(text: str) -> str:
    """``'Ah'`` -> ``'A♥'`` for display only. Passes unknowns through."""
    if len(text) != 2 or text[1] not in _SUIT_SYMBOL:
        return text
    return text[0] + _SUIT_SYMBOL[text[1]]


def pretty_row(texts: Sequence[str]) -> str:
    return " ".join(pretty(t) for t in texts) if texts else "-"


def is_red(text: str) -> bool:
    return len(text) == 2 and text[1] in ("h", "d")


__all__ = [
    "RANKS", "SUITS", "RANK_INDEX", "SUIT_INDEX", "FULL_DECK",
    "wire_to_text", "wire_list_to_text",
    "text_to_code", "code_to_text", "texts_to_codes", "codes_to_texts",
    "is_playable", "remaining_deck", "format_cards",
    "pretty", "pretty_row", "is_red",
]
