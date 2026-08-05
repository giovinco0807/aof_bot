"""Legal placement enumeration for one OFC street.

An ``Action`` is a complete answer to "what do I do with the cards I was just
dealt": which card goes in which row, and — from the second street onward —
which card gets thrown away.

Two things here that the pineapple reference generator does not do:

* candidates that leave the board already fouled are dropped, so the solver
  never spends rollouts on a line that has provably lost; and
* the initial five-card street is generated as a partition, so the 232
  distinct boards come out without a deduplication pass.
"""

from dataclasses import dataclass
from itertools import product
from typing import List, Optional, Sequence, Tuple

from .board import Board, BOTTOM, CAPACITY, MIDDLE, ROWS, TOP
from .cards import code_to_text


@dataclass(frozen=True)
class Action:
    """One street's decision.

    ``placements`` pairs each card with the row it goes to. ``discard`` is the
    card thrown away, and is ``None`` only on the opening five-card street.
    """
    placements: Tuple[Tuple[int, str], ...]
    discard: Optional[int] = None

    def apply(self, board: Board) -> Board:
        """A new board with this action played. The original is untouched."""
        out = board.copy()
        for card, row in self.placements:
            out.place(row, card)
        return out

    def to_texts(self) -> dict:
        return {
            "placements": [{"card": code_to_text(c), "row": r} for c, r in self.placements],
            "discard": code_to_text(self.discard) if self.discard is not None else None,
        }

    def __str__(self) -> str:
        moves = ", ".join(f"{code_to_text(c)}->{r}" for c, r in self.placements)
        if self.discard is None:
            return moves
        return f"{moves}, discard {code_to_text(self.discard)}"


def _fits(board: Board, assignment: Sequence[str]) -> bool:
    """True when a row assignment respects every row's remaining capacity."""
    need = {TOP: 0, MIDDLE: 0, BOTTOM: 0}
    for row in assignment:
        need[row] += 1
    return all(need[r] <= board.free(r) for r in ROWS)


def initial_actions(dealt: Sequence[int], board: Board,
                    prune_fouled: bool = True) -> List[Action]:
    """Every way to place the opening five cards. 232 on an empty board."""
    if len(dealt) != 5:
        raise ValueError(f"the opening street deals 5 cards, got {len(dealt)}")

    out: List[Action] = []
    for assignment in product(ROWS, repeat=5):
        if not _fits(board, assignment):
            continue
        action = Action(tuple(zip(dealt, assignment)))
        if prune_fouled and action.apply(board).doomed():
            continue
        out.append(action)
    return out


def street_actions(dealt: Sequence[int], board: Board,
                   prune_fouled: bool = True) -> List[Action]:
    """Every way to place two of three dealt cards and discard the third.

    27 while all three rows have room, fewer as the board fills up.
    """
    if len(dealt) != 3:
        raise ValueError(f"a pineapple street deals 3 cards, got {len(dealt)}")

    out: List[Action] = []
    for skip in range(3):
        discard = dealt[skip]
        keep = [dealt[i] for i in range(3) if i != skip]
        for assignment in product(ROWS, repeat=2):
            if not _fits(board, assignment):
                continue
            action = Action(tuple(zip(keep, assignment)), discard)
            if prune_fouled and action.apply(board).doomed():
                continue
            out.append(action)
    return out


def actions_for(dealt: Sequence[int], board: Board,
                prune_fouled: bool = True) -> List[Action]:
    """Dispatch on how many cards were dealt.

    Falls back to unpruned generation when pruning leaves nothing at all —
    a board that fouls on every line still has to place its cards.
    """
    generate = initial_actions if len(dealt) == 5 else street_actions
    out = generate(dealt, board, prune_fouled=prune_fouled)
    if not out and prune_fouled:
        out = generate(dealt, board, prune_fouled=False)
    return out


__all__ = ["Action", "initial_actions", "street_actions", "actions_for"]
