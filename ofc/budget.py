"""How long the solver may think, street by street.

One number for the whole hand is the wrong shape for OFC. The opening deal
has 232 legal placements and decides the shape of everything that follows;
the streets after it have at most 27 and are increasingly forced. A
Fantasyland hand places thirteen cards at once out of fourteen to seventeen,
which is a far bigger search than any of them. Spending the same three
seconds on all of those wastes time in the places it cannot help and starves
the places it can.

So the budget is per street, with a Fantasyland entry of its own, and it
persists between sessions.

There is a second, harder limit. The table has its own clock — PPPoker sends
``actionLeftTime`` alongside the deal — and no amount of thinking is worth
anything after it runs out, because the client will place the cards for you.
:meth:`TimeBudget.for_street` takes that into account when the table has told
us what it is.
"""

import json
from dataclasses import dataclass, field
from pathlib import Path
from typing import Dict, Optional

BUDGET_PATH = Path(__file__).resolve().parent / "data" / "budget.json"

#: The streets a hand goes through. Street 0 deals five cards; 1 through 4
#: deal three and place two, finishing the board at thirteen.
STREETS = (0, 1, 2, 3, 4)

#: Seconds held back from the table's own clock, to leave room for the
#: placement itself and for the packet round trip.
DEFAULT_RESERVE = 2.0

#: ``actionLeftTime`` outside this range is not believed. The field's unit is
#: not documented anywhere and has not been confirmed against a live table,
#: so a value that is not a plausible number of seconds is ignored rather
#: than guessed at — capping a budget to a misread 0 would mean never
#: thinking at all.
PLAUSIBLE_ACTION_SECONDS = (1.0, 600.0)


def _defaults() -> Dict[int, float]:
    # The opening street is worth the most thought: it has by far the widest
    # choice and every later street is played into the shape it leaves.
    return {0: 6.0, 1: 3.0, 2: 3.0, 3: 2.5, 4: 2.0}


@dataclass
class TimeBudget:
    """Seconds of thinking allowed, per street."""

    per_street: Dict[int, float] = field(default_factory=_defaults)
    fantasyland: float = 15.0
    #: Used for a street not named in ``per_street``.
    default: float = 3.0
    #: Held back from the table's clock when it is known.
    reserve: float = DEFAULT_RESERVE
    #: Whether to shorten the budget to fit the table's own timer.
    respect_table_clock: bool = True

    # ------------------------------------------------------------- lookup
    def for_street(self, street: int = 0, in_fantasyland: bool = False,
                   action_left: Optional[float] = None) -> float:
        """The budget for one decision.

        ``action_left`` is the table's remaining time for this turn, when it
        has been reported. A budget longer than that is pointless: the client
        places the cards itself when the clock runs out, and it would place
        them without waiting for an answer that arrived late.
        """
        if in_fantasyland:
            allowed = self.fantasyland
        else:
            allowed = self.per_street.get(street, self.default)

        if self.respect_table_clock and action_left is not None:
            low, high = PLAUSIBLE_ACTION_SECONDS
            if low <= action_left <= high:
                allowed = min(allowed, max(0.2, action_left - self.reserve))

        return max(0.1, allowed)

    def set_street(self, street: int, seconds: float) -> None:
        self.per_street[int(street)] = max(0.1, float(seconds))

    # -------------------------------------------------------- persistence
    def to_dict(self) -> dict:
        return {
            "per_street": {str(k): v for k, v in sorted(self.per_street.items())},
            "fantasyland": self.fantasyland,
            "default": self.default,
            "reserve": self.reserve,
            "respect_table_clock": self.respect_table_clock,
        }

    @classmethod
    def from_dict(cls, data: dict) -> "TimeBudget":
        raw = (data or {}).get("per_street") or {}
        per_street = {}
        for key, value in raw.items():
            try:
                per_street[int(key)] = max(0.1, float(value))
            except (TypeError, ValueError):
                continue
        return cls(
            per_street=per_street or _defaults(),
            fantasyland=float((data or {}).get("fantasyland", 15.0)),
            default=float((data or {}).get("default", 3.0)),
            reserve=float((data or {}).get("reserve", DEFAULT_RESERVE)),
            respect_table_clock=bool((data or {}).get("respect_table_clock", True)),
        )

    def save(self, path: Path = BUDGET_PATH) -> None:
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(self.to_dict(), indent=2), encoding="utf-8")

    @classmethod
    def load(cls, path: Path = BUDGET_PATH) -> "TimeBudget":
        """Read the saved budget, falling back to the defaults on anything odd."""
        try:
            return cls.from_dict(json.loads(path.read_text(encoding="utf-8")))
        except (OSError, ValueError, TypeError):
            return cls()

    @classmethod
    def uniform(cls, seconds: float) -> "TimeBudget":
        """One value for every street — the old single-number behaviour."""
        return cls(per_street={s: seconds for s in STREETS},
                   fantasyland=seconds, default=seconds)

    def describe(self) -> str:
        parts = [f"open {self.per_street.get(0, self.default):g}s"]
        parts += [f"s{s} {self.per_street.get(s, self.default):g}s" for s in STREETS[1:]]
        parts.append(f"FL {self.fantasyland:g}s")
        if self.respect_table_clock:
            parts.append("capped by the table clock")
        return ", ".join(parts)


__all__ = ["TimeBudget", "STREETS", "BUDGET_PATH", "DEFAULT_RESERVE",
           "PLAUSIBLE_ACTION_SECONDS"]
