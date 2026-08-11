"""The pineapple project's m3 engine, wired into the solver contract.

This is the strong one. The `ofc_hu_m3_engine` library is a Rust cdylib built
from the sibling *regular OFC* project, and it carries a per-street, per-seat
set of learned models — the m7v5 pin package. Those models are the distilled
product of a deep teacher search, so playing from them needs no search at all:
one forward pass ranks the whole fan.

That distinction is the reason this is usable live. The trainer's published
20.5 s figure for the opening street is the *teacher*, which rolls out all 232
candidates to label them. Asking the models what they think of those same 232
candidates is a different request — measured here at 0.12 s on four cores.

    street        candidates    model_scores
    T0 first      232           0.12 s
    T1 first      27            0.05 s
    T2 first      27            0.01 s
    T3 first      21            0.01 s

What it needs, and will say plainly if it does not have:

* the built library — ``target/release/libofc_hu_m3_engine.so`` on Linux,
  ``ofc_hu_m3_engine.dll`` on Windows — and the fourteen ``.bin`` pins in
  ``rust/hu_m3_engine/tests/fixtures/``. Both live in the regular-OFC
  repository; point ``OFC_REGULAR_ROOT`` at its root, or write the path into
  ``ofc/data/m3engine.json``.
* a heads-up hand. The models are trained for two seats and the engine has no
  notion of a third.
* a position whose card counts match the street. The engine validates the
  geometry and refuses anything else, which is a feature — it is the same
  check that catches a missed packet.

Fantasyland is not handled here: that lives in a different crate with its own
weights, and is not reachable through this library.
"""

import json
import os
import sys
from pathlib import Path
from typing import Dict, List, Optional, Tuple

from ..actions import Action
from ..board import BOTTOM, Board, MIDDLE, ROWS, TOP
from ..cards import code_to_text, text_to_code
from ..solver import Advice, Candidate, SolveRequest, register

CONFIG_PATH = Path(__file__).resolve().parent.parent / "data" / "m3engine.json"

#: Card ordering the engine uses: suit-major over ``hdcs``, ranks ``2..A``
#: within each suit. A card's bit in an action key is ``suit * 13 + rank``.
_ENGINE_RANKS = "23456789TJQKA"
_ENGINE_SUITS = "hdcs"

#: Action-key token layout: four 52-bit masks, most significant hex digit first.
_KEY_PREFIX = "rak1"
_MASK_HEX_WIDTH = 13
_KEY_ROWS = (TOP, MIDDLE, BOTTOM)

#: Cards each side holds, and how many hero has thrown away, at each street.
#: The engine checks this and so do we, so a mismatch is reported as a note
#: rather than surfacing as an exception from inside the library.
_GEOMETRY = {0: (0, 5, 0), 1: (5, 3, 0), 2: (7, 3, 1), 3: (9, 3, 2), 4: (11, 3, 3)}


def _engine_index(text: str) -> int:
    return _ENGINE_SUITS.index(text[1]) * 13 + _ENGINE_RANKS.index(text[0])


def _decode_mask(mask_hex: str) -> List[int]:
    """One 13-hex-digit mask -> the cards it names, as our own codes."""
    value = int(mask_hex, 16)
    out = []
    for index in range(52):
        if value >> index & 1:
            suit, rank = divmod(index, 13)
            out.append(text_to_code(_ENGINE_RANKS[rank] + _ENGINE_SUITS[suit]))
    return out


def decode_action_key(token: str) -> Optional[Action]:
    """``rak1:<top>:<middle>:<bottom>:<discard>`` -> an :class:`Action`.

    Returns ``None`` for anything that is not a well-formed key, so a change
    in the engine's key format shows up as missing candidates rather than as
    a crash mid-hand.
    """
    parts = token.split(":")
    if len(parts) != 5 or parts[0] != _KEY_PREFIX:
        return None
    if any(len(p) != _MASK_HEX_WIDTH for p in parts[1:]):
        return None

    try:
        placements: List[Tuple[int, str]] = []
        for row, mask_hex in zip(_KEY_ROWS, parts[1:4]):
            for card in _decode_mask(mask_hex):
                placements.append((card, row))
        discards = tuple(_decode_mask(parts[4]))
    except (ValueError, KeyError):
        return None

    return Action(tuple(placements),
                  discard=discards[0] if discards else None,
                  discards=discards)


class M3Engine:
    """Holds the loaded library, so the weights are hashed once per session."""

    def __init__(self, root: Optional[Path] = None):
        self.root = Path(root) if root else self._find_root()
        self._engine_eval = None
        self._library = None
        self.error: Optional[str] = None

    # ------------------------------------------------------------ discovery
    @staticmethod
    def _find_root() -> Optional[Path]:
        """Where the regular-OFC project is checked out.

        Explicit configuration first, then the places it plausibly sits
        relative to this repository. Nothing is downloaded or guessed beyond
        those; an unfound root is reported, not worked around.
        """
        env = os.environ.get("OFC_REGULAR_ROOT")
        if env:
            return Path(env)
        if CONFIG_PATH.is_file():
            try:
                configured = json.loads(CONFIG_PATH.read_text(encoding="utf-8")).get("root")
                if configured:
                    return Path(configured)
            except (OSError, ValueError):
                pass
        here = Path(__file__).resolve().parents[2]
        for candidate in (here.parent / "regular-ofc-pineapple",
                          here.parent / "ofc-regular",
                          here.parent / "pineapple"):
            if (candidate / "rust" / "hu_m3_engine").is_dir():
                return candidate
        return None

    def _library_path(self) -> Path:
        release = self.root / "target" / "release"
        names = ("ofc_hu_m3_engine.dll", "libofc_hu_m3_engine.so",
                 "libofc_hu_m3_engine.dylib")
        for name in names:
            if (release / name).is_file():
                return release / name
        return release / names[0 if sys.platform == "win32" else 1]

    # --------------------------------------------------------------- loading
    def load(self):
        """Import the project and load the library. Returns the module, or None."""
        if self._engine_eval is not None or self.error is not None:
            return self._engine_eval

        if self.root is None:
            self.error = ("the regular-OFC project was not found — set "
                          "OFC_REGULAR_ROOT or write {\"root\": \"...\"} into "
                          f"{CONFIG_PATH}")
            return None
        if not (self.root / "rust" / "hu_m3_engine").is_dir():
            self.error = f"{self.root} does not look like the regular-OFC project"
            return None

        library = self._library_path()
        if not library.is_file():
            self.error = (f"engine library not built: {library} — run "
                          "`cargo build --release -p ofc_hu_m3_engine` in "
                          f"{self.root}")
            return None

        for path in (self.root / "src", self.root):
            if str(path) not in sys.path:
                sys.path.insert(0, str(path))

        try:
            import trainer.engine_eval as engine_eval        # noqa: PLC0415
        except Exception as exc:                             # noqa: BLE001
            self.error = f"could not import the project: {type(exc).__name__}: {exc}"
            return None

        engine_eval.LIBRARY_PATH = library
        try:
            self._library, _ = engine_eval._ensure_loaded()
        except Exception as exc:                             # noqa: BLE001
            self.error = f"engine would not load: {type(exc).__name__}: {exc}"
            return None

        self._engine_eval = engine_eval
        return engine_eval

    # ---------------------------------------------------------------- solving
    def rank(self, request: SolveRequest) -> Advice:
        engine_eval = self.load()
        if engine_eval is None:
            return Advice(solver="m3", note=self.error or "engine unavailable")

        refusal = self._refuse(request)
        if refusal:
            return Advice(solver="m3", note=refusal)

        opponent = request.opponents[0]
        hero_rows = request.board.to_texts()
        opponent_rows = opponent.board.to_texts()
        dealt = [code_to_text(c) for c in request.dealt]
        discards = [code_to_text(c) for c in request.discards]
        # Hero leads a street when the opponent has not yet answered it; once
        # they have, their board is two cards ahead of hero's.
        seat = "first" if opponent.board.card_count() <= request.board.card_count() else "second"

        from ofc_regular.hu_m3_rust import evaluate_request   # noqa: PLC0415

        try:
            observation = engine_eval._observation(
                hero_rows, opponent_rows, dealt, discards, request.street, seat)
            config = engine_eval._joint_config(observation, request.street, "fast")
            payload = {
                "schema": engine_eval.HU_M3_REQUEST_SCHEMA,
                "kind": "model_scores",
                "observation": observation.to_dict(),
                "observation_fingerprint": observation.fingerprint(),
                "config": engine_eval._joint_config_payload(config),
            }
            response = evaluate_request(payload, library=self._library)
        except Exception as exc:                             # noqa: BLE001
            # Ranking the whole fan needs a feature encoder that only accepts
            # boards with two, four, six or eight open slots, which rules out
            # the opening deal from the second seat. Asking for the move
            # instead still works there, and one answer beats none.
            move = self._decide(engine_eval, request, seat, hero_rows,
                                opponent_rows, dealt, discards)
            if move is not None:
                return move
            return Advice(solver="m3", note=f"{type(exc).__name__}: {exc}")

        rows = response.get("actions") or []
        if not rows:
            return Advice(solver="m3", note="the engine ranked nothing")

        # The engine names actions by key rather than by placement, so decode
        # each one and keep only what is legal on the board we asked about.
        legal = {self._identity(a) for a in request.legal_actions()}
        candidates: List[Candidate] = []
        undecodable = 0
        for row in rows:
            action = decode_action_key(row.get("action_key", ""))
            if action is None:
                undecodable += 1
                continue
            if legal and self._identity(action) not in legal:
                continue
            candidates.append(Candidate(
                action=action,
                ev=float(row.get("score", 0.0)),
                detail={"model_score": round(float(row.get("score", 0.0)), 3)},
            ))

        note = f"{seat} seat, {response.get('evaluator', 'model')}"
        if undecodable:
            note += f"; {undecodable} action keys could not be decoded"
        if not candidates:
            note += "; nothing the engine returned was legal here"
        return Advice.of(request, candidates, solver="m3", note=note)

    def _decide(self, engine_eval, request: SolveRequest, seat: str,
                hero_rows, opponent_rows, dealt, discards) -> Optional[Advice]:
        """The single move the models play, when the fan cannot be ranked.

        A different request from ranking, not a cheaper setting of it: it
        returns placements directly, so there is nothing to decode, and it
        answers at streets whose feature encoder refuses a full fan.
        """
        try:
            out = engine_eval.decide_with_engine(
                hero_board=hero_rows, opp_board=opponent_rows, dealt=dealt,
                dead=discards, turn=request.street, position=seat,
                precision="fast")
        except Exception:                                    # noqa: BLE001
            return None

        placements = out.get("action", {}).get("placements") or []
        if not placements:
            return None
        try:
            placed = tuple((text_to_code(card), row) for card, row in placements)
        except (ValueError, TypeError):
            return None

        chosen = {c for c, _ in placed}
        mucked = tuple(c for c in request.dealt if c not in chosen)
        action = Action(placed, discard=mucked[0] if mucked else None,
                        discards=mucked)
        return Advice.of(request, [Candidate(action=action, ev=0.0,
                                             detail={"ranked": 0.0})],
                         solver="m3",
                         note=(f"{seat} seat, {out.get('evaluator', 'decide')} — "
                               "the move only; this street's fan cannot be ranked"))

    @staticmethod
    def _identity(action: Action):
        """Order-independent identity, for matching engine actions to ours."""
        return (tuple(sorted((c, r) for c, r in action.placements)),
                tuple(sorted(action.mucked)))

    @staticmethod
    def _refuse(request: SolveRequest) -> Optional[str]:
        """Why this position cannot go to the engine, or None."""
        if request.in_fantasyland:
            return ("fantasyland is served by a different crate and is not "
                    "reachable through this engine")
        if len(request.opponents) != 1:
            return (f"the engine plays heads-up; this table has "
                    f"{len(request.opponents)} opponents")
        if request.street not in _GEOMETRY:
            return f"street {request.street} is outside T0-T4"

        placed, dealt, discarded = _GEOMETRY[request.street]
        if request.board.card_count() != placed:
            return (f"street {request.street} expects {placed} cards placed, "
                    f"hero has {request.board.card_count()}")
        if len(request.dealt) != dealt:
            return (f"street {request.street} deals {dealt} cards, "
                    f"got {len(request.dealt)}")
        if len(request.discards) != discarded:
            return (f"street {request.street} expects {discarded} hero discards, "
                    f"got {len(request.discards)} — the engine needs every one "
                    "to know which cards are gone")

        # Acting second means the opponent has already played this street, so
        # they lead by whatever that street places: five on the opening deal,
        # two on every street after it.
        lead = 5 if request.street == 0 else 2
        opponent = request.opponents[0].board.card_count()
        if opponent not in (placed, placed + lead):
            return (f"the opponent has {opponent} cards placed, which is neither "
                    f"level with hero ({placed}) nor one street ahead "
                    f"({placed + lead})")
        return None


_ENGINE = M3Engine()


def solve(request: SolveRequest) -> Advice:
    return _ENGINE.rank(request)


def status() -> str:
    """One line on whether the engine is usable, for the CLI and the GUI."""
    engine = _ENGINE.load()
    if engine is None:
        return f"m3: unavailable — {_ENGINE.error}"
    return f"m3: ready ({_ENGINE._library_path()})"


register("m3", solve)
