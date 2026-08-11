"""The solver contract.

This module defines the boundary between the bot's plumbing and whatever
placement logic gets plugged into it. Everything upstream — packet capture,
table-state tracking, dead-card bookkeeping — produces a :class:`SolveRequest`.
Everything downstream — the GUI, the hand log, the automation layer —
consumes an :class:`Advice`. A solver is anything that turns one into the
other.

To add a solver, write a callable taking a request and returning an advice,
then register it::

    from ofc.solver import register, SolveRequest, Advice, Candidate

    def my_solver(req: SolveRequest) -> Advice:
        best = pick_something(req.legal_actions())
        return Advice.of(req, [Candidate(action=best, ev=0.0)], solver="mine")

    register("mine", my_solver)

Select it by name (``--solver mine``, or the GUI dropdown) and the rest of
the bot does not change.

The request carries everything a solver could want, already derived:
hero's rows, the cards to place, hero's own discards, every opponent board
visible at the table, and — the part worth building around — the true
remaining deck. OFC is an open-information game: an opponent's placed cards
are public the moment they land, and the packet feed delivers them
instantly, so ``req.deck`` is the real distribution to sample from rather
than a guess made from hero's cards alone.
"""

import time
from dataclasses import dataclass, field
from typing import Callable, Dict, List, Optional, Sequence

from . import evaluator as ev
from .actions import Action, actions_for
from .board import Board, CAPACITY, ROWS
from .cards import code_to_text, codes_to_texts, remaining_deck

# Streets. The opening deal places five cards; each of the three that follow
# deals three and places two. Fantasyland places thirteen out of one deal.
STREET_INITIAL = 0
STREET_FANTASYLAND = -1


# ------------------------------------------------------------------- request

@dataclass(frozen=True)
class OpponentView:
    """What is publicly known about one opponent, mid-hand.

    The rows are exactly what they have placed so far, which in OFC is
    public. ``discards`` is not modelled because an opponent's discard is
    never shown.
    """
    seat_id: int
    name: str = ""
    board: Board = field(default_factory=Board)
    in_fantasyland: bool = False


@dataclass(frozen=True)
class SolveRequest:
    """One decision to make, with everything known about it.

    Cards are ``code`` integers throughout — see :mod:`ofc.cards`. Use
    :meth:`texts` when a human needs to read them.
    """
    board: Board
    dealt: List[int]
    street: int = STREET_INITIAL
    discards: List[int] = field(default_factory=list)
    opponents: List[OpponentView] = field(default_factory=list)
    in_fantasyland: bool = False
    #: Seconds the solver may spend on this decision. Already trimmed to what
    #: the table's clock allows — see :mod:`ofc.budget`.
    time_budget: float = 4.0
    seed: int = 0
    table_id: int = 0
    hero_seat: int = -1
    #: Seconds the table said hero has left, when it said. Informational: the
    #: budget has already accounted for it.
    action_left: Optional[float] = None
    #: When this request was built, on the monotonic clock. Set automatically.
    created: float = field(default_factory=time.monotonic)

    # ------------------------------------------------------------ derived
    @property
    def dead_cards(self) -> List[int]:
        """Every card hero can account for, and so cannot draw.

        Hero's own rows and discards, the cards in hand right now, and every
        card showing on an opponent's board. Deduplicated, so its length is
        always ``52 - len(deck)``; a card seen in two places is still one
        card gone from the pack.
        """
        dead: List[int] = list(self.board.all_cards())
        dead += list(self.dealt)
        dead += list(self.discards)
        for opponent in self.opponents:
            dead += opponent.board.all_cards()

        seen = set()
        unique = []
        for card in dead:
            if card not in seen:
                seen.add(card)
                unique.append(card)
        return unique

    @property
    def deck(self) -> List[int]:
        """The true remaining deck, dead cards removed."""
        return remaining_deck(self.dead_cards)

    @property
    def deadline(self) -> float:
        """The monotonic time this decision should be finished by.

        Convenience for a searching solver, which can loop on
        ``while time.monotonic() < req.deadline:`` rather than doing the
        arithmetic itself and getting the start time subtly wrong.
        """
        return self.created + self.time_budget

    def time_left(self) -> float:
        """Seconds of budget remaining, never below zero."""
        return max(0.0, self.deadline - time.monotonic())

    def legal_actions(self) -> List[Action]:
        """Every legal way to play this street.

        Lines that already foul are dropped — *unless* every line fouls, in
        which case the full set comes back, because the cards still have to
        go somewhere. On randomly generated late-street boards that happens
        roughly half the time, so do not read a non-empty result as a promise
        that any of it is clean; check with ``Board.is_foul`` if it matters.

        Not meaningful in Fantasyland, where all thirteen cards are placed at
        once; that case returns an empty list and the solver is expected to
        build the board itself.
        """
        if self.in_fantasyland or len(self.dealt) not in (3, 5):
            return []
        return actions_for(self.dealt, self.board)

    def texts(self) -> dict:
        """The whole request in readable card strings, for logs and the GUI."""
        return {
            "street": self.street,
            "table_id": self.table_id,
            "hero_seat": self.hero_seat,
            "in_fantasyland": self.in_fantasyland,
            "time_budget": round(self.time_budget, 2),
            "action_left": self.action_left,
            "board": self.board.to_texts(),
            "dealt": codes_to_texts(self.dealt),
            "discards": codes_to_texts(self.discards),
            "deck_size": len(self.deck),
            "opponents": [
                {"seat_id": o.seat_id, "name": o.name,
                 "board": o.board.to_texts(), "in_fantasyland": o.in_fantasyland}
                for o in self.opponents
            ],
        }


# -------------------------------------------------------------------- advice

@dataclass
class Candidate:
    """One placement a solver considered, and what it thought of it.

    ``ev`` is whatever the solver ranks by — points per hand, royalty,
    anything — and is only ever compared against other candidates from the
    same solver. ``detail`` is free-form: put foul rates, rollout counts, or
    nothing at all in it, and the GUI will show what it finds.
    """
    action: Action
    ev: float = 0.0
    detail: Dict[str, float] = field(default_factory=dict)

    #: Keys ``to_dict`` owns. A detail entry using one of these is kept but
    #: renamed, so a solver's diagnostics can never overwrite the placement
    #: the GUI and the automation layer read back out.
    RESERVED = ("placements", "discard", "ev")

    def to_dict(self) -> dict:
        out = self.action.to_texts()
        out["ev"] = round(self.ev, 3)
        for key, value in self.detail.items():
            out[f"detail_{key}" if key in self.RESERVED else key] = value
        return out


@dataclass
class Advice:
    """A solver's answer, best candidate first."""
    candidates: List[Candidate] = field(default_factory=list)
    solver: str = ""
    elapsed: float = 0.0
    note: str = ""

    @property
    def best(self) -> Optional[Candidate]:
        return self.candidates[0] if self.candidates else None

    @classmethod
    def of(cls, request: "SolveRequest", candidates: Sequence[Candidate],
           solver: str = "", elapsed: float = 0.0, note: str = "") -> "Advice":
        """Build an advice with the candidates sorted best-first."""
        ordered = sorted(candidates, key=lambda c: c.ev, reverse=True)
        return cls(candidates=list(ordered), solver=solver,
                   elapsed=elapsed, note=note)

    def to_dict(self) -> dict:
        return {
            "solver": self.solver,
            "elapsed": round(self.elapsed, 3),
            "note": self.note,
            "best": self.candidates[0].to_dict() if self.candidates else None,
            "candidates": [c.to_dict() for c in self.candidates],
        }


# ------------------------------------------------------------------ registry

SolverFn = Callable[[SolveRequest], Advice]

_REGISTRY: Dict[str, SolverFn] = {}


def register(name: str, fn: SolverFn, replace: bool = False) -> None:
    """Make a solver selectable by name.

    Registering an existing name is refused unless ``replace`` is set, so a
    stray import cannot silently take over the solver the user chose.
    """
    if not replace and name in _REGISTRY:
        raise ValueError(f"solver {name!r} is already registered")
    _REGISTRY[name] = fn


def get(name: str) -> SolverFn:
    if name not in _REGISTRY:
        raise KeyError(f"no solver named {name!r}; available: {available()}")
    return _REGISTRY[name]


def available() -> List[str]:
    return sorted(_REGISTRY)


def solve(request: SolveRequest, name: str) -> Advice:
    """Run a registered solver and time it.

    A solver that raises is reported as an empty advice carrying the error
    rather than propagating: a broken solver should cost the user their
    advice for one street, not their capture session.
    """
    started = time.perf_counter()
    try:
        fn = get(name)
        advice = fn(request)
        if advice is None:
            return Advice(solver=name, elapsed=time.perf_counter() - started,
                          note="solver returned nothing")
        if not isinstance(advice, Advice):
            return Advice(solver=name, elapsed=time.perf_counter() - started,
                          note=f"solver returned {type(advice).__name__}, expected Advice")
        if not advice.solver:
            advice.solver = name
        if not advice.elapsed:
            advice.elapsed = time.perf_counter() - started
        return advice
    except Exception as exc:                       # noqa: BLE001 - reported, not swallowed
        return Advice(solver=name, elapsed=time.perf_counter() - started,
                      note=f"{type(exc).__name__}: {exc}")


# ---------------------------------------------------------------- validation

@dataclass
class Validation:
    """What is wrong with a solver's answer, if anything.

    ``errors`` mean the action cannot be played at all — a card hero was
    never dealt, a row overfilled. Nothing should act on an action that has
    them. ``warnings`` mean it is playable but notable; a line that fouls is
    the usual one, and sometimes every line fouls and one still has to be
    played, so that must not block execution.
    """
    errors: List[str] = field(default_factory=list)
    warnings: List[str] = field(default_factory=list)

    @property
    def ok(self) -> bool:
        """True when the action is safe to play."""
        return not self.errors

    @property
    def problems(self) -> List[str]:
        """Everything worth telling the user, errors first."""
        return self.errors + self.warnings


def validate(request: SolveRequest, action: Action) -> Validation:
    """Check a solver's answer before anything acts on it.

    A solver that returns a card hero was never dealt, or overfills a row,
    would otherwise turn into a misclick on a real table.
    """
    result = Validation()

    unknown_rows = sorted({r for _, r in action.placements if r not in ROWS})
    if unknown_rows:
        # Everything below indexes by row, so there is nothing meaningful to
        # check once a row name is not one of the three.
        result.errors.append(f"places cards in unknown rows: {', '.join(unknown_rows)}")
        return result

    placed = [c for c, _ in action.placements]
    available_cards = list(request.dealt)

    for card in placed:
        if card in available_cards:
            available_cards.remove(card)
        else:
            result.errors.append(f"places {code_to_text(card)}, which was not dealt")

    if len(set(placed)) != len(placed):
        result.errors.append("places the same card twice")

    if action.discard is not None:
        if action.discard in available_cards:
            available_cards.remove(action.discard)
        elif action.discard in placed:
            result.errors.append(
                f"discards {code_to_text(action.discard)} and also places it")
        else:
            result.errors.append(
                f"discards {code_to_text(action.discard)}, which was not dealt")

    if request.in_fantasyland:
        needed = 13 - request.board.card_count()
        if len(placed) != needed:
            result.errors.append(
                f"fantasyland places {len(placed)} cards, expected {needed}")
    else:
        expected = 5 if len(request.dealt) == 5 else 2
        if len(placed) != expected:
            result.errors.append(f"places {len(placed)} cards, expected {expected}")
        # On a pineapple street exactly one card is mucked, and a solver that
        # does not name it leaves the caller guessing which card to drag.
        if expected == 2 and action.discard is None:
            result.errors.append("does not say which card to discard")

    for row in ROWS:
        placing = sum(1 for _, r in action.placements if r == row)
        if placing > request.board.free(row):
            result.errors.append(f"{row} row takes {placing} more than it has room for")

    if result.ok:
        final = action.apply(request.board)
        if final.is_complete() and final.is_foul():
            result.warnings.append("this line fouls")

    return result


def describe(advice: Advice) -> str:
    """One line summarising an advice, for the log."""
    if advice.note and not advice.candidates:
        return f"[{advice.solver}] no advice: {advice.note}"
    best = advice.best
    if best is None:
        return f"[{advice.solver}] no candidates"
    detail = " ".join(f"{k}={v}" for k, v in best.detail.items())
    return (f"[{advice.solver}] {best.action}  ev={best.ev:+.2f}"
            f"{'  ' + detail if detail else ''}  ({advice.elapsed*1000:.0f}ms)")


__all__ = [
    "STREET_INITIAL", "STREET_FANTASYLAND",
    "OpponentView", "SolveRequest", "Candidate", "Advice",
    "SolverFn", "register", "get", "available", "solve",
    "validate", "describe",
]
