"""Hand evaluation, royalties and foul detection for OFC Pineapple.

The rules encoded here are the standard PPPoker/Pineapple ones, matching the
royalty tables in the pineapple project's ``game/royalty.py``. They are
re-implemented rather than imported for two reasons: this package must run
without the sibling repository present, and the Monte-Carlo solver calls
these functions hundreds of thousands of times per decision, which rules out
the object-per-card representation the reference uses.

Cards are ``code`` integers (see ``cards.py``): ``rank * 4 + suit``, so
``code >> 2`` is the rank 0..12 and ``code & 3`` is the suit.

Hand strength is a single int, comparable with ``<`` and ``>``::

    category << 20 | k1 << 16 | k2 << 12 | k3 << 8 | k4 << 4 | k5

Three-card hands (the top row) are scored on the *same* scale as five-card
hands, with their unused kicker slots left at zero. That makes the foul test
a plain ``top > middle`` comparison, and it makes a top-row trips beat a
middle-row two pair while still losing to a higher middle-row trips — which
is how real OFC plays and where the reference implementation's flat "trips
counts as 2.5" shortcut gets it wrong.
"""

from typing import List, Sequence, Tuple

# Hand categories, low to high.
HIGH_CARD = 0
PAIR = 1
TWO_PAIR = 2
TRIPS = 3
STRAIGHT = 4
FLUSH = 5
FULL_HOUSE = 6
QUADS = 7
STRAIGHT_FLUSH = 8

CATEGORY_NAMES = {
    HIGH_CARD: "High Card",
    PAIR: "Pair",
    TWO_PAIR: "Two Pair",
    TRIPS: "Trips",
    STRAIGHT: "Straight",
    FLUSH: "Flush",
    FULL_HOUSE: "Full House",
    QUADS: "Quads",
    STRAIGHT_FLUSH: "Straight Flush",
}

# Rank indices: '2' -> 0 ... 'A' -> 12.
_ACE = 12
_FIVE = 3

# Every five-rank run, as a bitmask -> the rank that names the straight.
# The wheel A-2-3-4-5 is a straight headed by the five, not by the ace.
_STRAIGHTS = {}
for _low in range(0, 9):                       # 2-high .. T-high runs
    _STRAIGHTS[sum(1 << (_low + i) for i in range(5))] = _low + 4
_STRAIGHTS[(1 << _ACE) | 0b1111] = _FIVE       # A-2-3-4-5


def _encode(category: int, kickers: Sequence[int]) -> int:
    """Pack a category and up to five ordered ranks into one comparable int."""
    value = category << 20
    shift = 16
    for k in kickers:
        value |= k << shift
        shift -= 4
        if shift < 0:
            break
    return value


# --------------------------------------------------------------- evaluation

def eval5(codes: Sequence[int]) -> int:
    """Strength of a five-card hand. Higher is better."""
    r0 = codes[0] >> 2
    r1 = codes[1] >> 2
    r2 = codes[2] >> 2
    r3 = codes[3] >> 2
    r4 = codes[4] >> 2

    s0 = codes[0] & 3
    flush = (s0 == (codes[1] & 3) and s0 == (codes[2] & 3)
             and s0 == (codes[3] & 3) and s0 == (codes[4] & 3))

    mask = (1 << r0) | (1 << r1) | (1 << r2) | (1 << r3) | (1 << r4)
    straight_high = _STRAIGHTS.get(mask)          # None unless all five distinct and consecutive

    if straight_high is not None:
        # Five distinct consecutive ranks: straight, or straight flush.
        return _encode(STRAIGHT_FLUSH if flush else STRAIGHT, (straight_high,))
    if flush:
        return _encode(FLUSH, sorted((r0, r1, r2, r3, r4), reverse=True))

    counts = {}
    for r in (r0, r1, r2, r3, r4):
        counts[r] = counts.get(r, 0) + 1

    # Most frequent first, then highest rank first.
    groups = sorted(counts.items(), key=lambda rc: (rc[1], rc[0]), reverse=True)
    shape = groups[0][1]

    if shape == 4:
        return _encode(QUADS, (groups[0][0], groups[1][0]))
    if shape == 3:
        if groups[1][1] == 2:
            return _encode(FULL_HOUSE, (groups[0][0], groups[1][0]))
        return _encode(TRIPS, (groups[0][0], groups[1][0], groups[2][0]))
    if shape == 2:
        if groups[1][1] == 2:
            return _encode(TWO_PAIR, (groups[0][0], groups[1][0], groups[2][0]))
        return _encode(PAIR, (groups[0][0], groups[1][0], groups[2][0], groups[3][0]))
    return _encode(HIGH_CARD, sorted((r0, r1, r2, r3, r4), reverse=True))


def eval3(codes: Sequence[int]) -> int:
    """Strength of the three-card top row, on the same scale as ``eval5``.

    A three-card hand can only be high card, a pair, or trips, so it maps
    onto the five-card categories directly and compares against a middle row
    without any special-casing at the call site.
    """
    r0 = codes[0] >> 2
    r1 = codes[1] >> 2
    r2 = codes[2] >> 2

    if r0 == r1 == r2:
        return _encode(TRIPS, (r0,))
    if r0 == r1:
        return _encode(PAIR, (r0, r2))
    if r0 == r2:
        return _encode(PAIR, (r0, r1))
    if r1 == r2:
        return _encode(PAIR, (r1, r0))
    return _encode(HIGH_CARD, sorted((r0, r1, r2), reverse=True))


def category_of(value: int) -> int:
    """Extract the hand category from a packed strength value."""
    return value >> 20


# --------------------------------------------------------------- royalties

# Top row pairs: 66 scores 1 through AA scoring 9. Anything below 66 scores 0.
_TOP_PAIR_MIN_RANK = 4                                  # index of '6'

_MIDDLE_ROYALTY = {TRIPS: 2, STRAIGHT: 4, FLUSH: 8, FULL_HOUSE: 12, QUADS: 20}
_BOTTOM_ROYALTY = {STRAIGHT: 2, FLUSH: 4, FULL_HOUSE: 6, QUADS: 10}

_MIDDLE_SF, _MIDDLE_RF = 30, 50
_BOTTOM_SF, _BOTTOM_RF = 15, 25


def top_royalty(codes: Sequence[int]) -> int:
    """Royalty for a complete three-card top row."""
    if len(codes) != 3:
        return 0
    value = eval3(codes)
    category = value >> 20
    rank = (value >> 16) & 0xF
    if category == TRIPS:
        return 10 + rank                                # 222 = 10 ... AAA = 22
    if category == PAIR and rank >= _TOP_PAIR_MIN_RANK:
        return rank - 3                                 # 66 = 1 ... AA = 9
    return 0


def _row_royalty(codes: Sequence[int], table: dict, sf: int, rf: int) -> int:
    if len(codes) != 5:
        return 0
    value = eval5(codes)
    category = value >> 20
    if category == STRAIGHT_FLUSH:
        return rf if ((value >> 16) & 0xF) == _ACE else sf
    return table.get(category, 0)


def middle_royalty(codes: Sequence[int]) -> int:
    return _row_royalty(codes, _MIDDLE_ROYALTY, _MIDDLE_SF, _MIDDLE_RF)


def bottom_royalty(codes: Sequence[int]) -> int:
    return _row_royalty(codes, _BOTTOM_ROYALTY, _BOTTOM_SF, _BOTTOM_RF)


# --------------------------------------------------------------- board rules

def is_foul(top: Sequence[int], middle: Sequence[int], bottom: Sequence[int]) -> bool:
    """True when the board breaks the top <= middle <= bottom ordering.

    Only meaningful for a complete board; an incomplete one cannot foul yet
    and is reported as clean.
    """
    if len(top) != 3 or len(middle) != 5 or len(bottom) != 5:
        return False
    return eval3(top) > eval5(middle) or eval5(middle) > eval5(bottom)


def total_royalty(top: Sequence[int], middle: Sequence[int],
                  bottom: Sequence[int]) -> int:
    """Royalties for a complete board, or 0 if it fouls."""
    if is_foul(top, middle, bottom):
        return 0
    return top_royalty(top) + middle_royalty(middle) + bottom_royalty(bottom)


def royalty_breakdown(top: Sequence[int], middle: Sequence[int],
                      bottom: Sequence[int]) -> dict:
    """Per-row royalties plus the total, all zero when the board fouls."""
    if is_foul(top, middle, bottom):
        return {"top": 0, "middle": 0, "bottom": 0, "total": 0, "foul": True}
    t, m, b = top_royalty(top), middle_royalty(middle), bottom_royalty(bottom)
    return {"top": t, "middle": m, "bottom": b, "total": t + m + b, "foul": False}


def fantasyland_entry(top: Sequence[int]) -> int:
    """Cards dealt if this top row wins Fantasyland, else 0.

    QQ deals 14, KK 15, AA 16, and any trips 17.
    """
    if len(top) != 3:
        return 0
    value = eval3(top)
    category = value >> 20
    rank = (value >> 16) & 0xF
    if category == TRIPS:
        return 17
    if category == PAIR:
        if rank == _ACE:
            return 16
        if rank == _ACE - 1:
            return 15
        if rank == _ACE - 2:
            return 14
    return 0


def fantasyland_stay(top: Sequence[int], bottom: Sequence[int]) -> bool:
    """True when a completed board keeps Fantasyland: top trips, or bottom quads+."""
    if len(top) != 3 or len(bottom) != 5:
        return False
    if (eval3(top) >> 20) == TRIPS:
        return True
    return (eval5(bottom) >> 20) >= QUADS


# --------------------------------------------------------------- scoring

SCOOP_BONUS = 3
FOUL_PENALTY = 6


def compare_boards(top_a: Sequence[int], mid_a: Sequence[int], bot_a: Sequence[int],
                   top_b: Sequence[int], mid_b: Sequence[int], bot_b: Sequence[int]) -> int:
    """Points hero (board A) wins from one opponent (board B).

    One point per row won, a three-point scoop bonus for taking all three,
    plus the royalty difference. A fouled board pays its opponent six points
    plus that opponent's royalties and collects nothing.
    """
    a_foul = is_foul(top_a, mid_a, bot_a)
    b_foul = is_foul(top_b, mid_b, bot_b)

    if a_foul and b_foul:
        return 0
    if a_foul:
        return -(FOUL_PENALTY + total_royalty(top_b, mid_b, bot_b))
    if b_foul:
        return FOUL_PENALTY + total_royalty(top_a, mid_a, bot_a)

    rows = 0
    for va, vb in ((eval3(top_a), eval3(top_b)),
                   (eval5(mid_a), eval5(mid_b)),
                   (eval5(bot_a), eval5(bot_b))):
        if va > vb:
            rows += 1
        elif va < vb:
            rows -= 1

    if rows == 3:
        rows += SCOOP_BONUS
    elif rows == -3:
        rows -= SCOOP_BONUS

    return rows + total_royalty(top_a, mid_a, bot_a) - total_royalty(top_b, mid_b, bot_b)


def hand_name(codes: Sequence[int]) -> str:
    """Readable category for a three- or five-card row, for the GUI."""
    if len(codes) == 3:
        return CATEGORY_NAMES.get(eval3(codes) >> 20, "?")
    if len(codes) == 5:
        value = eval5(codes)
        category = value >> 20
        if category == STRAIGHT_FLUSH and ((value >> 16) & 0xF) == _ACE:
            return "Royal Flush"
        return CATEGORY_NAMES.get(category, "?")
    return ""


__all__ = [
    "HIGH_CARD", "PAIR", "TWO_PAIR", "TRIPS", "STRAIGHT", "FLUSH",
    "FULL_HOUSE", "QUADS", "STRAIGHT_FLUSH", "CATEGORY_NAMES",
    "eval5", "eval3", "category_of",
    "top_royalty", "middle_royalty", "bottom_royalty",
    "is_foul", "total_royalty", "royalty_breakdown",
    "fantasyland_entry", "fantasyland_stay",
    "compare_boards", "hand_name",
    "SCOOP_BONUS", "FOUL_PENALTY",
]
