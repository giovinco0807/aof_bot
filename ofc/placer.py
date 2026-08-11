"""Putting the cards where the solver says — the automation half.

This is phase two, and it is off unless asked for. Two things make OFC harder
to actuate than the all-in bot this repository already has:

* the hook cannot submit a placement. ``pppoker_hook.js`` notes that sending
  packets crashes the client, so the AoF bot clicks its Fold and All-In
  buttons on screen and this has to do the same; and
* a placement is a drag, not a click. A card moves from the hand strip to one
  of thirteen slots, and ``PcController`` — which this builds on rather than
  modifies — only knows how to tap.

So the layout has to be measured once per window size, and stored. Nothing
here guesses coordinates: :meth:`Placer.readiness` refuses to run until every
slot has been calibrated, because a drag to a coordinate nobody checked lands
somewhere on a real table.

    python -m ofc.placer --calibrate     measure the slots
    python -m ofc.placer --show          print the stored layout
    python -m ofc.placer --dry-run       replay a placement without clicking
"""

import json
import math
import random
import sys
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Dict, List, Optional, Tuple

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from ofc.board import BOTTOM, CAPACITY, MIDDLE, ROWS, TOP    # noqa: E402
from ofc.cards import code_to_text                            # noqa: E402
from ofc.solver import Advice                                 # noqa: E402

LAYOUT_PATH = Path(__file__).resolve().parent / "data" / "layout.json"

#: How many card positions the hand strip has. The opening street shows five;
#: later streets show three.
HAND_SLOTS = 5


@dataclass
class Layout:
    """Screen coordinates for one window size.

    ``rows`` maps a row name to the centre of each slot in it. ``hand`` is
    where the dealt cards sit before being moved. ``confirm`` is the button
    that commits a street, if the client has one.
    """
    window: Tuple[int, int, int, int] = (0, 0, 0, 0)     # x, y, w, h when measured
    rows: Dict[str, List[Tuple[int, int]]] = field(default_factory=dict)
    hand: List[Tuple[int, int]] = field(default_factory=list)
    confirm: Optional[Tuple[int, int]] = None
    discard: Optional[Tuple[int, int]] = None

    def to_dict(self) -> dict:
        return {
            "window": list(self.window),
            "rows": {r: [list(p) for p in pts] for r, pts in self.rows.items()},
            "hand": [list(p) for p in self.hand],
            "confirm": list(self.confirm) if self.confirm else None,
            "discard": list(self.discard) if self.discard else None,
        }

    @classmethod
    def from_dict(cls, data: dict) -> "Layout":
        return cls(
            window=tuple(data.get("window") or (0, 0, 0, 0)),
            rows={r: [tuple(p) for p in pts]
                  for r, pts in (data.get("rows") or {}).items()},
            hand=[tuple(p) for p in (data.get("hand") or [])],
            confirm=tuple(data["confirm"]) if data.get("confirm") else None,
            discard=tuple(data["discard"]) if data.get("discard") else None,
        )

    def save(self, path: Path = LAYOUT_PATH) -> None:
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(self.to_dict(), indent=2), encoding="utf-8")

    @classmethod
    def load(cls, path: Path = LAYOUT_PATH) -> "Layout":
        if not path.is_file():
            return cls()
        return cls.from_dict(json.loads(path.read_text(encoding="utf-8")))

    def missing(self) -> List[str]:
        """Slots that have not been measured.

        The hand strip needs all five positions, not three: the opening
        street places five cards at once, and a layout that only covers a
        pineapple street would fail on the very first decision of a hand.
        """
        gaps = []
        for row in ROWS:
            points = self.rows.get(row) or []
            if len(points) != CAPACITY[row]:
                gaps.append(f"{row} row has {len(points)} of {CAPACITY[row]} slots")
        if len(self.hand) < HAND_SLOTS:
            gaps.append(f"hand strip has {len(self.hand)} of {HAND_SLOTS} positions")
        return gaps


class Placer:
    """Executes a solver's placement on screen.

    Built on ``automation/pc_input.PcController`` so the window handling,
    foreground activation and human-like timing already tuned for the AoF bot
    are reused rather than reinvented; the drag this needs is added here
    instead of being pushed into that class, which the working bot depends on.
    """

    def __init__(self, layout: Optional[Layout] = None, verbose: bool = True,
                 dry_run: bool = False):
        self.layout = layout if layout is not None else Layout.load()
        self.verbose = verbose
        self.enabled = False                 # must be set on purpose
        #: Plan and print the drags without performing any. The way to check
        #: a calibration against live hands before letting it touch the mouse.
        self.dry_run = dry_run
        self._controller = None

    # ------------------------------------------------------------ readiness
    def readiness(self) -> List[str]:
        """Everything standing between this and a safe placement.

        A dry run only needs the layout — it never touches the mouse — so
        the input-layer checks are skipped for it. That way a calibration
        can be reviewed from anywhere, not only from the machine that will
        play with it.
        """
        problems = list(self.layout.missing())
        if self.dry_run:
            return problems
        return problems + self.input_problems()

    def input_problems(self) -> List[str]:
        """Why the mouse could not be driven, if it could not.

        Checks the input layer for real rather than only constructing it: a
        missing dependency that surfaces as a printed warning during setup
        would otherwise surface as a placement that silently does nothing.
        """
        problems: List[str] = []

        # The drag is SetCursorPos + mouse_event, which is win32 only.
        if sys.platform != "win32":
            problems.append(f"dragging needs Windows; this is {sys.platform}")
        else:
            try:
                import ctypes
                ctypes.windll.user32
            except Exception as exc:         # noqa: BLE001
                problems.append(f"no user32 to drag with: {type(exc).__name__}: {exc}")

        # Only the confirm tap goes through PcController, so pyautogui is
        # required exactly when a confirm button was calibrated.
        if self.layout.confirm:
            try:
                self._pc()
            except Exception as exc:         # noqa: BLE001
                problems.append(f"input layer unavailable: {type(exc).__name__}: {exc}")
            else:
                try:
                    import pyautogui             # noqa: F401,PLC0415
                except ImportError:
                    problems.append("a confirm button is calibrated but pyautogui is "
                                    "missing, so it could not be pressed — "
                                    "pip install pyautogui")
        return problems

    def _pc(self):
        if self._controller is None:
            sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "automation"))
            from pc_input import PcController      # noqa: PLC0415

            controller = PcController()
            controller.check_connection()
            controller.enabled = True             # gating happens on this class
            self._controller = controller
        return self._controller

    # ---------------------------------------------------------------- input
    def drag(self, start: Tuple[int, int], end: Tuple[int, int],
             steps: int = 24) -> None:
        """Press at ``start``, move to ``end`` along a curve, release.

        The path is eased and slightly bowed rather than linear, and the
        press and release are separated in time, because a cursor that
        teleports in a straight line at constant speed does not look like a
        hand moving a card.
        """
        if not self.enabled:
            if self.verbose:
                print(f"  [OFC place] (disabled) {start} -> {end}")
            return

        import ctypes

        user32 = ctypes.windll.user32
        x0, y0 = start
        x1, y1 = end

        # Bow the path perpendicular to the direction of travel.
        dx, dy = x1 - x0, y1 - y0
        distance = math.hypot(dx, dy) or 1.0
        bow = random.uniform(-0.10, 0.10) * distance
        nx, ny = -dy / distance, dx / distance

        user32.SetCursorPos(int(x0), int(y0))
        time.sleep(0.04 + random.random() * 0.05)
        user32.mouse_event(0x0002, 0, 0, 0, 0)          # left down
        time.sleep(0.05 + random.random() * 0.05)

        for step in range(1, steps + 1):
            t = step / steps
            eased = t * t * (3 - 2 * t)                 # smoothstep
            arc = math.sin(math.pi * t) * bow
            x = x0 + dx * eased + nx * arc
            y = y0 + dy * eased + ny * arc
            user32.SetCursorPos(int(x), int(y))
            time.sleep(random.uniform(0.006, 0.018))

        time.sleep(0.04 + random.random() * 0.06)
        user32.mouse_event(0x0004, 0, 0, 0, 0)          # left up
        time.sleep(0.08 + random.random() * 0.10)

    def _jitter(self, point: Tuple[int, int], sigma: float = 3.0) -> Tuple[int, int]:
        return (int(point[0] + random.gauss(0, sigma)),
                int(point[1] + random.gauss(0, sigma)))

    # ------------------------------------------------------------- placement
    def plan(self, advice: Advice, hand_order: List[int],
             board: Optional["object"] = None) -> List[dict]:
        """Turn an advice into a list of drags, without performing any.

        ``hand_order`` is the left-to-right order the dealt cards sit in on
        screen, and is required rather than guessed. Deriving it from the
        solver's output would order the cards by where they are going rather
        than by where they are, and every drag would pick up the wrong card —
        the failure looks like the bot playing a different hand entirely.
        Pass ``request.dealt``, which is packet order, or the order read off
        the strip if you have it.

        ``board`` is hero's rows before this street, so slots are counted
        from the first free one. Rows fill left to right, so without it every
        drag after the opening street aims at an occupied slot.
        """
        if advice.best is None:
            return []

        action = advice.best.action
        order = list(hand_order)
        for card in action.mucked:
            if card not in order:
                order.append(card)

        filled = {row: len(board.row(row)) if board is not None else 0
                  for row in ROWS}

        moves: List[dict] = []
        for card, row in action.placements:
            points = self.layout.rows.get(row) or []
            index = filled.get(row, 0)
            filled[row] = index + 1

            source_index = order.index(card) if card in order else None
            source = (self.layout.hand[source_index]
                      if source_index is not None and source_index < len(self.layout.hand)
                      else None)
            target = points[index] if index < len(points) else None
            moves.append({
                "card": code_to_text(card), "row": row, "slot": index,
                "from": source, "to": target,
                "unknown_source": source_index is None,
            })
        return moves

    def execute(self, advice: Advice, request,
                hand_order: Optional[List[int]] = None) -> bool:
        """Perform the placement. Returns False if nothing was placed.

        ``request`` is the position the advice answers, and it is required:
        the drags need the cards in the order they were dealt and the board
        they go onto, neither of which an :class:`Advice` carries. Pass
        ``hand_order`` to override the dealt order when the strip has been
        read off the screen.

        Refuses rather than improvises. An uncalibrated slot, a card whose
        position on the strip is unknown, or the safety still off each stop
        the placement before anything moves.
        """
        if not self.enabled:
            if self.verbose:
                print("  [OFC place] refused: not enabled")
            return False

        problems = self.readiness()
        if problems:
            if self.verbose:
                for problem in problems:
                    print(f"  [OFC place] refused: {problem}")
            return False

        if request is None:
            if self.verbose:
                print("  [OFC place] refused: no request — the drags would not "
                      "know which card is where on the strip")
            return False

        order = list(hand_order) if hand_order is not None else list(request.dealt)
        moves = self.plan(advice, order, request.board)
        if not moves:
            return False

        if self.dry_run:
            print("  [OFC place] dry run — nothing will be clicked:")
            for move in moves:
                print(f"    {move['card']} -> {move['row']}[{move['slot']}]  "
                      f"{move['from']} -> {move['to']}")
            return False

        for move in moves:
            if move["unknown_source"]:
                if self.verbose:
                    print(f"  [OFC place] refused: {move['card']} is not in the "
                          f"hand order given; nothing placed")
                return False
            if move["from"] is None or move["to"] is None:
                if self.verbose:
                    print(f"  [OFC place] refused: no coordinates for {move['card']} -> "
                          f"{move['row']}[{move['slot']}]; nothing placed")
                return False

        self._pc()
        for move in moves:
            if self.verbose:
                print(f"  [OFC place] {move['card']} -> {move['row']}[{move['slot']}]")
            self.drag(self._jitter(move["from"]), self._jitter(move["to"]))

        if self.layout.confirm:
            time.sleep(0.2 + random.random() * 0.3)
            self._pc().tap(*self._jitter(self.layout.confirm))
        return True


# ---------------------------------------------------------------- calibration

def calibrate() -> None:
    """Measure the slots by pointing at them.

    Hover the mouse over each position in turn and press Enter. Nothing is
    clicked, so this is safe to run at a live table, though it is easier at
    an empty one where every slot is visible.
    """
    try:
        import ctypes
    except ImportError:
        print("calibration needs Windows")
        return

    class Point(ctypes.Structure):
        _fields_ = [("x", ctypes.c_long), ("y", ctypes.c_long)]

    def cursor() -> Tuple[int, int]:
        point = Point()
        ctypes.windll.user32.GetCursorPos(ctypes.byref(point))
        return int(point.x), int(point.y)

    layout = Layout.load()
    print("Hover over each position and press Enter. Type s to skip, q to stop.\n")

    def ask(label: str) -> Optional[Tuple[int, int]]:
        answer = input(f"  {label}: ").strip().lower()
        if answer == "q":
            raise KeyboardInterrupt
        if answer == "s":
            return None
        point = cursor()
        print(f"    -> {point}")
        return point

    try:
        for row in ROWS:
            points = []
            for slot in range(CAPACITY[row]):
                point = ask(f"{row} slot {slot + 1}/{CAPACITY[row]}")
                if point:
                    points.append(point)
            if points:
                layout.rows[row] = points

        hand = []
        for slot in range(HAND_SLOTS):
            point = ask(f"hand card {slot + 1}/{HAND_SLOTS} (leftmost first, s to skip)")
            if point:
                hand.append(point)
        if hand:
            layout.hand = hand

        point = ask("confirm button (s if there is none)")
        if point:
            layout.confirm = point
    except KeyboardInterrupt:
        print("\nstopped early — saving what was measured")

    layout.save()
    print(f"\nsaved to {LAYOUT_PATH}")
    gaps = layout.missing()
    if gaps:
        print("still missing:")
        for gap in gaps:
            print(f"  {gap}")
    else:
        print("layout complete")


def main() -> None:
    import argparse

    parser = argparse.ArgumentParser(description=__doc__,
                                     formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--calibrate", action="store_true")
    parser.add_argument("--show", action="store_true")
    parser.add_argument("--dry-run", action="store_true",
                        help="plan a placement from a sample advice, click nothing")
    args = parser.parse_args()

    if args.calibrate:
        calibrate()
        return

    layout = Layout.load()
    if args.show or not args.dry_run:
        print(json.dumps(layout.to_dict(), indent=2))
        gaps = layout.missing()
        print("\nmissing:" if gaps else "\nlayout complete")
        for gap in gaps:
            print(f"  {gap}")
        if not args.dry_run:
            return

    from ofc.board import Board
    from ofc.solver import Candidate, SolveRequest, solve
    from ofc.cards import texts_to_codes

    request = SolveRequest(board=Board(), dealt=texts_to_codes(["As", "Ad", "Kh", "7c", "2d"]))
    advice = solve(request, "baseline")
    placer = Placer(layout)
    print("\nplanned moves:")
    for move in placer.plan(advice):
        print(f"  {move['card']} -> {move['row']}[{move['slot']}]  "
              f"{move['from']} -> {move['to']}")


if __name__ == "__main__":
    main()
