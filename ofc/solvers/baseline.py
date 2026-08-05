"""A placeholder solver, so the pipeline can be exercised end to end.

This is not a good OFC player and is not trying to be. It exists so that the
capture, the GUI and the replay tool have something to show before the real
solver is written, and so that a new solver has a working example of the
contract to copy. It ranks placements by a shallow heuristic — put strength
low, keep matched cards together, take a royalty when a row completes — and
never looks ahead, never samples the deck, and never weighs foul risk beyond
the legality pruning it gets for free from ``legal_actions``.

Replace it by registering your own under a different name; nothing else in
the package needs to change.
"""

from itertools import combinations
from typing import Dict, List, Sequence

from .. import evaluator as ev
from ..actions import Action
from ..board import BOTTOM, Board, MIDDLE, TOP
from ..solver import Advice, Candidate, SolveRequest, register

# Where a rank wants to sit: the bottom row takes the strength, the top gives
# it up. Pairs outweigh raw rank because every OFC royalty comes from matched
# or connected cards; suits only pay in the five-card rows.
_RANK_WEIGHT = {BOTTOM: 0.50, MIDDLE: 0.18, TOP: -0.22}
_PAIR_BONUS = 6.0
_SUIT_BONUS = 1.1
_ROYALTY_WEIGHT = 1.5


def _placement_bonus(row: str, card: int, existing: Sequence[int]) -> float:
    rank, suit = card >> 2, card & 3
    score = rank * _RANK_WEIGHT[row]
    for other in existing:
        if other >> 2 == rank:
            score += _PAIR_BONUS
        if row != TOP and (other & 3) == suit:
            score += _SUIT_BONUS
    return score


def score_action(board: Board, action: Action) -> float:
    """Shallow desirability of one placement."""
    working: Dict[str, List[int]] = {
        TOP: list(board.top), MIDDLE: list(board.middle), BOTTOM: list(board.bottom)
    }
    score = 0.0
    for card, row in action.placements:
        score += _placement_bonus(row, card, working[row])
        working[row].append(card)

    if len(working[TOP]) == 3:
        score += ev.top_royalty(working[TOP]) * _ROYALTY_WEIGHT
    if len(working[MIDDLE]) == 5:
        score += ev.middle_royalty(working[MIDDLE]) * _ROYALTY_WEIGHT
    if len(working[BOTTOM]) == 5:
        score += ev.bottom_royalty(working[BOTTOM]) * _ROYALTY_WEIGHT
    return score


def _solve_fantasyland(request: SolveRequest) -> Advice:
    """Greedy Fantasyland arrangement: strongest legal bottom, then middle, then top.

    Bounded on purpose — a full search over a seventeen-card deal is a real
    piece of work and belongs in the actual solver.
    """
    pool = list(request.dealt)
    if len(pool) < 13:
        return Advice(solver="baseline", note=f"fantasyland needs 13+ cards, got {len(pool)}")

    best_board = None
    best_key = (-1, -1)
    for bottom in sorted(combinations(pool, 5), key=ev.eval5, reverse=True)[:60]:
        bottom_value = ev.eval5(bottom)
        rest = [c for c in pool if c not in bottom]
        for middle in combinations(rest, 5):
            if ev.eval5(middle) > bottom_value:
                continue
            leftover = [c for c in rest if c not in middle]
            for top in combinations(leftover, 3):
                if ev.eval3(top) > ev.eval5(middle):
                    continue
                royalty = (ev.bottom_royalty(bottom) + ev.middle_royalty(middle)
                           + ev.top_royalty(top))
                keeps = ev.fantasyland_stay(top, bottom)
                key = (1 if keeps else 0, royalty)
                if key > best_key:
                    best_key = key
                    best_board = (list(top), list(middle), list(bottom))

    if best_board is None:
        return Advice(solver="baseline", note="no non-fouling arrangement found")

    top, middle, bottom = best_board
    placements = ([(c, TOP) for c in top] + [(c, MIDDLE) for c in middle]
                  + [(c, BOTTOM) for c in bottom])
    used = set(top) | set(middle) | set(bottom)
    dropped = tuple(c for c in pool if c not in used)
    action = Action(tuple(placements),
                    discard=dropped[0] if dropped else None,
                    discards=dropped)
    candidate = Candidate(
        action=action,
        ev=float(best_key[1]),
        detail={"royalty": float(best_key[1]), "keeps_fantasyland": float(best_key[0])},
    )
    return Advice.of(request, [candidate], solver="baseline")


def solve(request: SolveRequest) -> Advice:
    """Rank this street's legal placements by the shallow heuristic."""
    if request.in_fantasyland:
        return _solve_fantasyland(request)

    actions = request.legal_actions()
    if not actions:
        return Advice(solver="baseline", note="no legal placement")

    candidates = [
        Candidate(action=a, ev=score_action(request.board, a), detail={})
        for a in actions
    ]
    candidates.sort(key=lambda c: c.ev, reverse=True)
    return Advice.of(request, candidates[:8], solver="baseline")


register("baseline", solve)
