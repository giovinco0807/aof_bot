"""Putting the cards where the solver says — the automation half.

This is phase two, it is off unless asked for, and it has not yet driven a
real table. Read the limitations at the bottom before arming it.

Two things make OFC harder to actuate than the all-in bot this repository
already has:

* the hook cannot submit a placement. ``pppoker_hook.js`` notes that sending
  packets crashes the client, so the AoF bot clicks its Fold and All-In
  buttons on screen and this has to do the same; and
* a placement is a drag, not a click. A card moves from the hand strip to
  one of thirteen slots, and ``PcController`` — which this builds on rather
  than modifies — only knows how to tap.

The layout is measured once and stored **relative to the client window**, so
moving the window does not silently send every drag to the wrong place. At
drag time the window is found again and the coordinates are translated back;
if it cannot be found, or has been resized since calibration, the placement
is refused rather than aimed at where the window used to be.

    python -m ofc.placer --calibrate     measure the slots
    python -m ofc.placer --show          print the stored layout
    python -m ofc.placer --dry-run       plan a placement without clicking

To rehearse against real hands without touching the mouse::

    python -m ofc.main --hero-uid <UID> --solver m3 --dry-place
"""

import json
import math
import random
import sys
import threading
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Callable, Dict, List, Optional, Tuple

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from ofc.board import BOTTOM, CAPACITY, MIDDLE, ROWS, TOP     # noqa: E402
from ofc.cards import code_to_text                            # noqa: E402
from ofc.solver import Advice                                 # noqa: E402

LAYOUT_PATH = Path(__file__).resolve().parent / "data" / "layout.json"

#: Cards the hand strip shows. The opening street deals five and every street
#: after it deals three, and the two are not drawn in the same places — a
#: three-card strip is usually centred where a five-card one was spread. They
#: are therefore measured separately.
HAND_SIZES = (5, 3)

#: How far the window may differ from its calibrated size before the stored
#: coordinates stop meaning anything.
SIZE_TOLERANCE = 2

#: How far the cursor may drift from where we put it before we conclude
#: somebody else is holding the mouse.
HIJACK_TOLERANCE = 40


@dataclass
class Layout:
    """Where everything sits, relative to the client window.

    Coordinates are window-relative on purpose. Absolute screen positions
    stop being true the moment the window moves, and nothing about a moved
    window is visible to a drag that has already been aimed.
    """
    #: Window size when this was measured. Position is not stored — it is
    #: looked up fresh — but the size is, because a resized client re-lays
    #: everything out and the stored points no longer describe it.
    window_size: Tuple[int, int] = (0, 0)
    rows: Dict[str, List[Tuple[int, int]]] = field(default_factory=dict)
    #: Deal size -> the strip positions for that many cards.
    hands: Dict[int, List[Tuple[int, int]]] = field(default_factory=dict)
    confirm: Optional[Tuple[int, int]] = None

    # -------------------------------------------------------------- storage
    def to_dict(self) -> dict:
        return {
            "window_size": list(self.window_size),
            "rows": {r: [list(p) for p in pts] for r, pts in self.rows.items()},
            "hands": {str(n): [list(p) for p in pts] for n, pts in sorted(self.hands.items())},
            "confirm": list(self.confirm) if self.confirm else None,
        }

    @classmethod
    def from_dict(cls, data: dict) -> "Layout":
        data = data or {}
        hands = {}
        for size, points in (data.get("hands") or {}).items():
            try:
                hands[int(size)] = [tuple(p) for p in points]
            except (TypeError, ValueError):
                continue
        return cls(
            window_size=tuple(data.get("window_size") or (0, 0)),
            rows={r: [tuple(p) for p in pts]
                  for r, pts in (data.get("rows") or {}).items()},
            hands=hands,
            confirm=tuple(data["confirm"]) if data.get("confirm") else None,
        )

    def save(self, path: Path = LAYOUT_PATH) -> None:
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(self.to_dict(), indent=2), encoding="utf-8")

    @classmethod
    def load(cls, path: Path = LAYOUT_PATH) -> "Layout":
        try:
            return cls.from_dict(json.loads(path.read_text(encoding="utf-8")))
        except (OSError, ValueError, TypeError):
            return cls()

    # ------------------------------------------------------------ inspection
    def missing(self) -> List[str]:
        """Slots that have not been measured."""
        gaps = []
        for row in ROWS:
            points = self.rows.get(row) or []
            if len(points) != CAPACITY[row]:
                gaps.append(f"{row} row has {len(points)} of {CAPACITY[row]} slots")
        for size in HAND_SIZES:
            points = self.hands.get(size) or []
            if len(points) != size:
                gaps.append(f"the {size}-card hand strip has {len(points)} "
                            f"of {size} positions")
        if self.window_size == (0, 0):
            gaps.append("the client window size was not recorded — recalibrate, "
                        "because the stored points cannot be checked against it")
        return gaps

    def strip(self, deal_size: int) -> List[Tuple[int, int]]:
        return list(self.hands.get(deal_size) or [])


# ------------------------------------------------------------------- window

def client_rect(title: str = "PPPoker") -> Optional[Tuple[int, int, int, int]]:
    """The client window as ``(x, y, w, h)``, or None when it is not up."""
    try:
        import pygetwindow as gw                     # noqa: PLC0415
    except ImportError:
        return None
    try:
        windows = [w for w in gw.getWindowsWithTitle(title)
                   if w.width > 300 and w.height > 300 and w.left > -10000]
    except Exception:                                # noqa: BLE001
        return None
    if not windows:
        return None
    window = windows[0]
    return (window.left, window.top, window.width, window.height)


class Placer:
    """Executes a solver's placement on screen.

    Every guard here exists because the alternative is a drag landing
    somewhere real: on another window, on the wrong slot, or after the user
    has already asked the bot to stop.
    """

    def __init__(self, layout: Optional[Layout] = None, verbose: bool = True,
                 dry_run: bool = False, window_title: str = "PPPoker",
                 should_continue: Optional[Callable[[], bool]] = None):
        self.layout = layout if layout is not None else Layout.load()
        self.verbose = verbose
        self.enabled = False                 # must be set on purpose
        #: Plan and print the drags without performing any.
        self.dry_run = dry_run
        self.window_title = window_title
        #: Asked before every drag. Returning False aborts the rest of the
        #: placement — that is how a stop reaches a sequence already running.
        self.should_continue = should_continue
        self.aborted = False
        self._controller = None
        self._lock = threading.Lock()

    # ------------------------------------------------------------ readiness
    def readiness(self) -> List[str]:
        """Everything standing between this and a safe placement.

        A dry run only needs the layout — it never touches the mouse — so
        the input-layer checks are skipped for it, and a calibration can be
        reviewed from a machine that will never play with it.
        """
        problems = list(self.layout.missing())
        if self.dry_run:
            return problems
        return problems + self.input_problems() + self.window_problems()

    def input_problems(self) -> List[str]:
        """Why the mouse could not be driven, if it could not."""
        problems: List[str] = []
        # Both the drag and PcController.tap are SetCursorPos + mouse_event,
        # which is win32 only. Neither goes through pyautogui, so pyautogui
        # is not required for either.
        if sys.platform != "win32":
            problems.append(f"dragging needs Windows; this is {sys.platform}")
            return problems
        try:
            import ctypes                            # noqa: PLC0415
            ctypes.windll.user32
        except Exception as exc:                     # noqa: BLE001
            problems.append(f"no user32 to drag with: {type(exc).__name__}: {exc}")
        return problems

    def window_problems(self) -> List[str]:
        """Why the stored coordinates cannot be trusted right now."""
        rect = client_rect(self.window_title)
        if rect is None:
            return [f"the {self.window_title} window was not found, so the stored "
                    "coordinates cannot be anchored to anything"]
        _, _, width, height = rect
        want_w, want_h = self.layout.window_size
        if want_w and (abs(width - want_w) > SIZE_TOLERANCE
                       or abs(height - want_h) > SIZE_TOLERANCE):
            return [f"the window is {width}x{height} but the layout was measured "
                    f"at {want_w}x{want_h}; the client re-lays out on resize, so "
                    "recalibrate at this size"]
        return []

    def _pc(self):
        if self._controller is None:
            sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "automation"))
            from pc_input import PcController          # noqa: PLC0415

            controller = PcController()
            controller.check_connection()
            controller.enabled = True                 # gating happens on this class
            self._controller = controller
        return self._controller

    # ---------------------------------------------------------------- input
    def _cursor(self) -> Tuple[int, int]:
        import ctypes                                 # noqa: PLC0415

        class Point(ctypes.Structure):
            _fields_ = [("x", ctypes.c_long), ("y", ctypes.c_long)]

        point = Point()
        ctypes.windll.user32.GetCursorPos(ctypes.byref(point))
        return int(point.x), int(point.y)

    def _focus(self) -> bool:
        """Bring the client to the front, so a drag lands on it and not on
        whatever the user has on top."""
        try:
            import ctypes                             # noqa: PLC0415
            hwnd = self._pc()._find_hwnd()
            if not hwnd:
                return False
            ctypes.windll.user32.SetForegroundWindow(hwnd)
            time.sleep(0.12)
            return True
        except Exception:                             # noqa: BLE001
            return False

    def drag(self, start: Tuple[int, int], end: Tuple[int, int],
             steps: int = 24) -> bool:
        """Press at ``start``, move to ``end`` along a curve, release.

        The path is eased and slightly bowed rather than linear, because a
        cursor that teleports in a straight line at constant speed does not
        look like a hand moving a card.

        Returns False when the drag was abandoned. The button is released in
        a ``finally`` whatever happens: leaving it down would hand the user a
        desktop that drags everything it touches.
        """
        if not self.enabled or self.dry_run:
            if self.verbose:
                print(f"  [OFC place] (not enabled) {start} -> {end}")
            return False

        import ctypes                                 # noqa: PLC0415

        user32 = ctypes.windll.user32
        x0, y0 = start
        x1, y1 = end

        dx, dy = x1 - x0, y1 - y0
        distance = math.hypot(dx, dy) or 1.0
        bow = random.uniform(-0.10, 0.10) * distance
        nx, ny = -dy / distance, dx / distance

        pressed = False
        try:
            user32.SetCursorPos(int(x0), int(y0))
            time.sleep(0.04 + random.random() * 0.05)
            user32.mouse_event(0x0002, 0, 0, 0, 0)          # left down
            pressed = True
            time.sleep(0.05 + random.random() * 0.05)

            for step in range(1, steps + 1):
                t = step / steps
                eased = t * t * (3 - 2 * t)                 # smoothstep
                arc = math.sin(math.pi * t) * bow
                x = int(x0 + dx * eased + nx * arc)
                y = int(y0 + dy * eased + ny * arc)

                # Somebody else moving the mouse is a stop signal, and the
                # only one that actually works: the advertised pyautogui
                # corner failsafe never applied here, because none of this
                # goes through pyautogui.
                where = self._cursor()
                if step > 1 and math.hypot(where[0] - last[0],
                                           where[1] - last[1]) > HIJACK_TOLERANCE:
                    self.aborted = True
                    if self.verbose:
                        print("  [OFC place] the mouse moved under us — stopping")
                    return False

                user32.SetCursorPos(x, y)
                last = (x, y)
                time.sleep(random.uniform(0.006, 0.018))

            time.sleep(0.04 + random.random() * 0.06)
            return True
        finally:
            if pressed:
                user32.mouse_event(0x0004, 0, 0, 0, 0)      # left up
                time.sleep(0.08 + random.random() * 0.10)

    def _jitter(self, point: Tuple[int, int], sigma: float = 3.0) -> Tuple[int, int]:
        return (int(point[0] + random.gauss(0, sigma)),
                int(point[1] + random.gauss(0, sigma)))

    # ------------------------------------------------------------- planning
    def plan(self, advice: Advice, hand_order: List[int],
             board=None) -> List[dict]:
        """Turn an advice into a list of drags, without performing any.

        ``hand_order`` is the left-to-right order the dealt cards sit in on
        screen, and is required rather than guessed. Deriving it from the
        solver's output would order the cards by where they are going rather
        than by where they are, and every drag would pick up the wrong card.

        ``board`` is hero's rows before this street, so slots are counted
        from the first free one; rows fill left to right, so without it every
        drag after the opening street aims at an occupied slot.

        Drags come back in descending strip order. If the client closes the
        gap when a card leaves the strip, taking the rightmost first keeps
        every remaining position where the plan says it is; if it does not
        compact, the order makes no difference. Within a row the order does
        not matter either — OFC scores a row as a set.
        """
        if advice.best is None:
            return []

        action = advice.best.action
        order = list(hand_order)
        for card in action.mucked:
            if card not in order:
                order.append(card)

        strip = self.layout.strip(len(order))
        filled = {row: len(board.row(row)) if board is not None else 0
                  for row in ROWS}

        moves: List[dict] = []
        for card, row in action.placements:
            points = self.layout.rows.get(row) or []
            index = filled.get(row, 0)
            filled[row] = index + 1

            source_index = order.index(card) if card in order else None
            source = (strip[source_index]
                      if source_index is not None and source_index < len(strip)
                      else None)
            moves.append({
                "card": code_to_text(card), "row": row, "slot": index,
                "from": source, "to": points[index] if index < len(points) else None,
                "strip_index": source_index,
                "unknown_source": source_index is None,
            })

        moves.sort(key=lambda m: (m["strip_index"] is None, -(m["strip_index"] or 0)))
        return moves

    # ------------------------------------------------------------ execution
    def execute(self, advice: Advice, request,
                hand_order: Optional[List[int]] = None) -> bool:
        """Perform the placement. Returns False if nothing was placed.

        ``request`` is the position the advice answers, and it is required:
        the drags need the cards in the order they were dealt and the board
        they go onto, neither of which an :class:`Advice` carries.

        Refuses rather than improvises, and stops rather than continuing
        into a sequence that has started going wrong.
        """
        self.aborted = False

        if not self.enabled:
            self._refuse("not enabled")
            return False
        if request is None:
            self._refuse("no request — the drags would not know which card is "
                         "where on the strip")
            return False
        if request.in_fantasyland:
            self._refuse("fantasyland places thirteen cards from a strip this "
                         "has never been calibrated for")
            return False

        problems = self.readiness()
        if problems:
            for problem in problems:
                self._refuse(problem)
            return False

        order = list(hand_order) if hand_order is not None else list(request.dealt)
        if not self.layout.strip(len(order)):
            self._refuse(f"no calibrated strip for a {len(order)}-card deal")
            return False

        moves = self.plan(advice, order, request.board)
        if not moves:
            return False

        for move in moves:
            if move["unknown_source"]:
                self._refuse(f"{move['card']} is not in the hand order given; "
                             "nothing placed")
                return False
            if move["from"] is None or move["to"] is None:
                self._refuse(f"no coordinates for {move['card']} -> "
                             f"{move['row']}[{move['slot']}]; nothing placed")
                return False

        if self.dry_run:
            print("  [OFC place] dry run — nothing will be clicked:")
            for move in moves:
                print(f"    {move['card']} -> {move['row']}[{move['slot']}]  "
                      f"{move['from']} -> {move['to']}")
            return False

        # One placement at a time. The AoF bot drives the same mouse from its
        # own controller, and a tap arriving mid-drag would drop the card.
        with self._lock:
            rect = client_rect(self.window_title)
            if rect is None:
                self._refuse("the client window disappeared before the first drag")
                return False
            origin = (rect[0], rect[1])

            if not self._focus():
                self._refuse("could not bring the client to the front; a drag "
                             "would have landed on whatever is on top")
                return False

            for index, move in enumerate(moves, start=1):
                if self.should_continue is not None and not self.should_continue():
                    self.aborted = True
                    self._refuse(f"stopped after {index - 1} of {len(moves)} drags")
                    return False
                if self.verbose:
                    print(f"  [OFC place] {move['card']} -> "
                          f"{move['row']}[{move['slot']}]")
                start = self._jitter(_offset(move["from"], origin))
                end = self._jitter(_offset(move["to"], origin))
                if not self.drag(start, end):
                    self._refuse(f"drag {index} of {len(moves)} did not complete; "
                                 "the board is part-placed — finish it by hand")
                    return False

            if self.layout.confirm:
                time.sleep(0.2 + random.random() * 0.3)
                # PcController.tap adds its own jitter, so this passes the
                # point through unjittered rather than compounding the two.
                self._pc().tap(*_offset(self.layout.confirm, origin), jitter=True)
        return True

    def _refuse(self, reason: str) -> None:
        if self.verbose:
            print(f"  [OFC place] refused: {reason}")


def _offset(point: Tuple[int, int], origin: Tuple[int, int]) -> Tuple[int, int]:
    """Window-relative point -> absolute screen point."""
    return (point[0] + origin[0], point[1] + origin[1])


# ---------------------------------------------------------------- calibration

def calibrate(window_title: str = "PPPoker") -> None:
    """Measure the slots by pointing at them.

    Hover the mouse over each position in turn and press Enter. Nothing is
    clicked, so this is safe at a live table, though it is easier at an empty
    one where every slot is visible.

    Positions are stored relative to the client window, and the window's size
    is stored with them, so a moved window still works and a resized one is
    detected rather than silently mis-aimed.
    """
    try:
        import ctypes
    except ImportError:
        print("calibration needs Windows")
        return

    rect = client_rect(window_title)
    if rect is None:
        print(f"the {window_title} window was not found — start the client first")
        return
    origin = (rect[0], rect[1])
    print(f"client window: {rect[2]}x{rect[3]} at {origin}")
    print("Hover over each position and press Enter. 'q' stops.\n")

    class Point(ctypes.Structure):
        _fields_ = [("x", ctypes.c_long), ("y", ctypes.c_long)]

    def cursor() -> Tuple[int, int]:
        point = Point()
        ctypes.windll.user32.GetCursorPos(ctypes.byref(point))
        return int(point.x) - origin[0], int(point.y) - origin[1]

    layout = Layout.load()
    layout.window_size = (rect[2], rect[3])

    def ask(label: str) -> Optional[Tuple[int, int]]:
        answer = input(f"  {label}: ").strip().lower()
        if answer == "q":
            raise KeyboardInterrupt
        point = cursor()
        print(f"    -> {point} (relative to the window)")
        return point

    try:
        for row in ROWS:
            # Every slot is asked for, in order, with no skipping: a gap
            # would silently shift every later slot down one and the drags
            # would be off by a card for the rest of the row.
            points = []
            for slot in range(CAPACITY[row]):
                points.append(ask(f"{row} slot {slot + 1}/{CAPACITY[row]}"))
            layout.rows[row] = points

        for size in HAND_SIZES:
            print(f"\n  -- the {size}-card hand strip. Deal yourself a "
                  f"{'opening' if size == 5 else 'pineapple'} street, or use a "
                  "screenshot of one --")
            points = []
            for slot in range(size):
                points.append(ask(f"{size}-card strip, card {slot + 1}/{size} "
                                  "(leftmost first)"))
            layout.hands[size] = points

        answer = input("\n  confirm button — hover and press Enter, or 's' if "
                       "there is none: ").strip().lower()
        if answer != "s":
            layout.confirm = cursor()
            print(f"    -> {layout.confirm}")
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
        print("layout complete — rehearse it with:")
        print("  python -m ofc.main --hero-uid <UID> --solver m3 --dry-place")


def main() -> None:
    import argparse

    parser = argparse.ArgumentParser(description=__doc__,
                                     formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--calibrate", action="store_true")
    parser.add_argument("--show", action="store_true")
    parser.add_argument("--dry-run", action="store_true",
                        help="plan a placement from a sample advice, click nothing")
    parser.add_argument("--window", default="PPPoker")
    args = parser.parse_args()

    if args.calibrate:
        calibrate(args.window)
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
    from ofc.cards import texts_to_codes
    from ofc.solver import OpponentView, SolveRequest, solve

    dealt = texts_to_codes(["As", "Ad", "Kh", "7c", "2d"])
    request = SolveRequest(board=Board(), dealt=dealt, street=0,
                           opponents=[OpponentView(seat_id=1, board=Board())])
    advice = solve(request, "baseline")
    placer = Placer(layout, dry_run=True)
    placer.enabled = True

    print("\nplanned moves for a sample opening deal "
          f"({' '.join(t for t in request.texts()['dealt'])}):")
    moves = placer.plan(advice, list(request.dealt), request.board)
    if not moves:
        print("  the solver returned nothing to place")
        return
    for move in moves:
        print(f"  {move['card']} -> {move['row']}[{move['slot']}]  "
              f"{move['from']} -> {move['to']}   (window-relative)")
    print("\nDrags are listed rightmost-strip-card first, so a strip that "
          "closes up as cards leave it stays correct.")


if __name__ == "__main__":
    main()
