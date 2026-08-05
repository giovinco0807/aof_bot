"""The three-row OFC board, in ``code`` integers.

Deliberately dumb: rows are plain lists, and the only rules it enforces are
row capacities. Strength, royalties and fouling all live in ``evaluator``,
so this object stays cheap enough to copy inside a rollout loop.
"""

from dataclasses import dataclass, field
from typing import List, Sequence

from . import evaluator as ev
from .cards import codes_to_texts, texts_to_codes

TOP, MIDDLE, BOTTOM = "top", "middle", "bottom"
ROWS = (TOP, MIDDLE, BOTTOM)
CAPACITY = {TOP: 3, MIDDLE: 5, BOTTOM: 5}


@dataclass
class Board:
    top: List[int] = field(default_factory=list)
    middle: List[int] = field(default_factory=list)
    bottom: List[int] = field(default_factory=list)

    # ------------------------------------------------------------- factories
    @classmethod
    def from_texts(cls, top: Sequence[str] = (), middle: Sequence[str] = (),
                   bottom: Sequence[str] = ()) -> "Board":
        return cls(texts_to_codes(top), texts_to_codes(middle), texts_to_codes(bottom))

    def copy(self) -> "Board":
        return Board(self.top[:], self.middle[:], self.bottom[:])

    # --------------------------------------------------------------- access
    def row(self, name: str) -> List[int]:
        if name == TOP:
            return self.top
        if name == MIDDLE:
            return self.middle
        if name == BOTTOM:
            return self.bottom
        raise ValueError(f"no such row: {name!r}")

    def free(self, name: str) -> int:
        """Open seats left in a row."""
        return CAPACITY[name] - len(self.row(name))

    def open_rows(self) -> List[str]:
        return [r for r in ROWS if self.free(r) > 0]

    def card_count(self) -> int:
        return len(self.top) + len(self.middle) + len(self.bottom)

    def all_cards(self) -> List[int]:
        return self.top + self.middle + self.bottom

    def is_complete(self) -> bool:
        return self.card_count() == 13

    def place(self, name: str, card: int) -> None:
        """Add a card. Raises when the row is already full."""
        row = self.row(name)
        if len(row) >= CAPACITY[name]:
            raise ValueError(f"{name} row is full")
        row.append(card)

    # --------------------------------------------------------------- rules
    def is_foul(self) -> bool:
        return ev.is_foul(self.top, self.middle, self.bottom)

    def royalties(self) -> dict:
        return ev.royalty_breakdown(self.top, self.middle, self.bottom)

    def fantasyland_entry(self) -> int:
        """Cards dealt if this board wins Fantasyland, else 0.

        A board that is unfinished has not won anything yet, and one that
        fouls is dead however good its top row looks — so both report 0,
        unlike the row-only rule in ``evaluator``, which cannot see them.
        """
        if not self.is_complete() or self.is_foul():
            return 0
        return ev.fantasyland_entry(self.top)

    def fantasyland_stay(self) -> bool:
        """True when a finished, clean board keeps Fantasyland."""
        if not self.is_complete() or self.is_foul():
            return False
        return ev.fantasyland_stay(self.top, self.bottom)

    def doomed(self) -> bool:
        """True when the board already fouls on the rows that are finished.

        Checked between streets to throw away candidate placements that can
        no longer recover, which the reference action generator does not do.
        A partial row cannot be judged, so only complete pairs are compared.
        """
        if len(self.top) == 3 and len(self.middle) == 5:
            if ev.eval3(self.top) > ev.eval5(self.middle):
                return True
        if len(self.middle) == 5 and len(self.bottom) == 5:
            if ev.eval5(self.middle) > ev.eval5(self.bottom):
                return True
        if len(self.top) == 3 and len(self.bottom) == 5:
            # Top beating a finished bottom cannot be rescued: the middle
            # would have to sit above the bottom, which is itself a foul.
            if ev.eval3(self.top) > ev.eval5(self.bottom):
                return True
        return False

    # -------------------------------------------------------------- display
    def to_texts(self) -> dict:
        return {
            "top": codes_to_texts(self.top),
            "middle": codes_to_texts(self.middle),
            "bottom": codes_to_texts(self.bottom),
        }

    def __str__(self) -> str:
        t = self.to_texts()
        return (f"T[{' '.join(t['top']) or '-'}] "
                f"M[{' '.join(t['middle']) or '-'}] "
                f"B[{' '.join(t['bottom']) or '-'}]")


__all__ = ["Board", "TOP", "MIDDLE", "BOTTOM", "ROWS", "CAPACITY"]
