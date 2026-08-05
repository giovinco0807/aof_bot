"""OFC Pineapple bot for PPPoker.

Card acquisition reuses the Frida packet hook this repository already has —
the ``Pine*`` packets carry hero's deal, every player's placed rows and the
showdown, so nothing is read off the screen and nothing is guessed. What this
package adds is the memory of those packets, a place to plug a solver in, and
a display for what the solver says.

The pieces, in the order data flows through them::

    cards      card encodings, and the only place that converts between them
    evaluator  hand strength, royalties, fouling, Fantasyland rules
    board      the three rows
    actions    every legal way to play a street
    state      table memory, rebuilt from the packet stream
    solver     the contract a placement solver implements, and its registry
    advisor    packets in, advice out
    gui        the board and the recommendation on screen

``solver`` is the seam: everything above it is bookkeeping that will not
change, everything below it is strategy. See ``ofc/solvers/baseline.py`` for
a minimal implementation of the contract.
"""

__version__ = "0.1.0"

from . import cards, evaluator                      # noqa: F401
from .actions import Action, actions_for            # noqa: F401
from .board import BOTTOM, Board, MIDDLE, ROWS, TOP  # noqa: F401
from .solver import (                               # noqa: F401
    Advice, Candidate, OpponentView, SolveRequest,
    available, describe, register, solve, validate, Validation,
)
from .state import Table, Tables, apply_packet      # noqa: F401

from . import solvers                               # noqa: F401,E402  (registers built-ins)

__all__ = [
    "__version__",
    "cards", "evaluator",
    "Action", "actions_for",
    "Board", "TOP", "MIDDLE", "BOTTOM", "ROWS",
    "SolveRequest", "OpponentView", "Candidate", "Advice",
    "register", "available", "solve", "validate", "Validation", "describe",
    "Table", "Tables", "apply_packet",
]
