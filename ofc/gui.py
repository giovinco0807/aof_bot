"""The OFC board and the solver's recommendation, on screen.

A separate window from the AoF GUIs on purpose: OFC has its own state, its own
solver and its own display, and bolting it onto a screen built around two hole
cards would help nobody.

It runs in three modes, all against the same solver:

  live     attach to PPPoker and follow the packet stream
  replay   step through hands already recorded in the hand database
  manual   type a board and a deal in, and see what comes back

Manual mode is the one to build a solver against. It needs no game running,
answers in the time the solver takes, and shows the ranked candidates rather
than only the pick — so a change in the solver is visible immediately.
"""

import queue
import sys
import threading
import tkinter as tk
from pathlib import Path
from tkinter import ttk
from typing import List, Optional

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from ofc import solver as solver_api                      # noqa: E402
from ofc.advisor import Advisor                            # noqa: E402
from ofc.board import BOTTOM, Board, MIDDLE, ROWS, TOP     # noqa: E402
from ofc.cards import code_to_text, is_red, pretty, text_to_code  # noqa: E402
from ofc.solver import OpponentView, SolveRequest, describe, solve, validate  # noqa: E402
from ofc.state import Table                                # noqa: E402

CARD_W, CARD_H = 44, 62
CARD_GAP = 6
ROW_GAP = 14

BG = "#0b3d24"
SLOT = "#0d4a2c"
SLOT_EDGE = "#1c6b42"
GHOST = "#f5c542"
CARD_BG = "#fdfdfa"
CARD_EDGE = "#9aa39c"
RED = "#c62828"
BLACK = "#1a1a1a"

ROW_LABEL = {TOP: "TOP  (3)", MIDDLE: "MID  (5)", BOTTOM: "BOT  (5)"}


class BoardCanvas(tk.Canvas):
    """Draws a board, plus the placement the solver wants to make.

    Cards already on the board are drawn solid. Cards the solver proposes are
    drawn in the slot they would go to, outlined, so the recommendation reads
    as a move rather than as a list to decode.
    """

    #: Left margin the row labels occupy, and the gutter the hand-name
    #: annotations are written into on the right.
    LABEL_W = 78
    NOTE_W = 118

    def __init__(self, master, title: str = "", **kw):
        width = self.LABEL_W + 5 * (CARD_W + CARD_GAP) + self.NOTE_W
        height = 26 + 3 * (CARD_H + ROW_GAP)
        super().__init__(master, width=width, height=height, bg=BG,
                         highlightthickness=0, **kw)
        self.title = title

    def render(self, board: Board, proposal: Optional[dict] = None,
               labels: Optional[dict] = None) -> None:
        """``proposal`` maps a row name to the cards being added to it."""
        self.delete("all")
        proposal = proposal or {}
        labels = labels or {}

        if self.title:
            self.create_text(8, 10, text=self.title, anchor="w",
                             fill="#cfe8d8", font=("Segoe UI", 9, "bold"))

        y = 24
        for row in ROWS:
            placed = board.row(row)
            incoming = proposal.get(row, [])
            capacity = 3 if row == TOP else 5

            self.create_text(8, y + CARD_H // 2, text=ROW_LABEL[row], anchor="w",
                             fill="#8fbfa4", font=("Consolas", 8))

            x = self.LABEL_W
            for slot in range(capacity):
                if slot < len(placed):
                    self._card(x, y, code_to_text(placed[slot]), ghost=False)
                elif slot - len(placed) < len(incoming):
                    self._card(x, y, code_to_text(incoming[slot - len(placed)]), ghost=True)
                else:
                    self.create_rectangle(x, y, x + CARD_W, y + CARD_H,
                                          fill=SLOT, outline=SLOT_EDGE, dash=(3, 3))
                x += CARD_W + CARD_GAP

            note = labels.get(row, "")
            if note:
                self.create_text(x + 8, y + CARD_H // 2, text=note, anchor="w",
                                 fill="#cfe8d8", font=("Consolas", 8))
            y += CARD_H + ROW_GAP

    def _card(self, x: int, y: int, text: str, ghost: bool) -> None:
        if ghost:
            self.create_rectangle(x, y, x + CARD_W, y + CARD_H,
                                  fill=SLOT, outline=GHOST, width=2)
            colour = GHOST
        else:
            self.create_rectangle(x, y, x + CARD_W, y + CARD_H,
                                  fill=CARD_BG, outline=CARD_EDGE)
            colour = RED if is_red(text) else BLACK
        self.create_text(x + CARD_W // 2, y + CARD_H // 2, text=pretty(text),
                         fill=colour, font=("Segoe UI", 14, "bold"))


class OfcGui:
    def __init__(self):
        self.root = tk.Tk()
        self.root.title("OFC Bot — board & recommended placement")
        self.root.geometry("1180x760")

        self.events: "queue.Queue" = queue.Queue()
        self.advisor: Optional[Advisor] = None
        self.capture = None
        self.capture_thread: Optional[threading.Thread] = None

        self.var_hero_uid = tk.StringVar(value="0")
        self.var_process = tk.StringVar(value="PPPoker.exe")
        self.var_solver = tk.StringVar(value=(solver_api.available() or ["baseline"])[0])
        self.var_budget = tk.DoubleVar(value=4.0)
        self.var_status = tk.StringVar(value="idle")
        self.var_deck = tk.StringVar(value="")

        self.var_top = tk.StringVar()
        self.var_mid = tk.StringVar()
        self.var_bot = tk.StringVar()
        self.var_dealt = tk.StringVar(value="As Ad Kh 7c 2d")
        self.var_opp = tk.StringVar()

        self._build()
        self.root.after(100, self._drain)

    # ------------------------------------------------------------------ ui
    def _build(self) -> None:
        bar = ttk.LabelFrame(self.root, text="Session")
        bar.pack(fill="x", padx=8, pady=6)

        line = ttk.Frame(bar)
        line.pack(fill="x", pady=4)
        ttk.Label(line, text="Process:").pack(side="left")
        ttk.Entry(line, textvariable=self.var_process, width=15).pack(side="left", padx=4)
        ttk.Label(line, text="Hero UID:").pack(side="left", padx=(12, 0))
        ttk.Entry(line, textvariable=self.var_hero_uid, width=12).pack(side="left", padx=4)
        ttk.Label(line, text="Solver:").pack(side="left", padx=(12, 0))
        self.cmb_solver = ttk.Combobox(line, textvariable=self.var_solver, width=16,
                                       values=solver_api.available(), state="readonly")
        self.cmb_solver.pack(side="left", padx=4)
        ttk.Button(line, text="Reload", command=self._reload_solvers).pack(side="left")
        ttk.Label(line, text="Budget (s):").pack(side="left", padx=(12, 0))
        ttk.Spinbox(line, textvariable=self.var_budget, from_=0.1, to=30.0,
                    increment=0.5, width=6).pack(side="left", padx=4)

        self.btn_start = ttk.Button(line, text="ATTACH", command=self._start)
        self.btn_start.pack(side="left", padx=(16, 4))
        self.btn_stop = ttk.Button(line, text="STOP", command=self._stop, state="disabled")
        self.btn_stop.pack(side="left")
        ttk.Label(line, textvariable=self.var_status,
                  foreground="#0a6").pack(side="left", padx=12)

        body = ttk.Frame(self.root)
        body.pack(fill="both", expand=True, padx=8)

        left = ttk.Frame(body)
        left.pack(side="left", fill="y")

        hero_box = ttk.LabelFrame(left, text="Hero board — outlined cards are the recommendation")
        hero_box.pack(fill="x", pady=4)
        self.hero_canvas = BoardCanvas(hero_box)
        self.hero_canvas.pack(padx=6, pady=6)

        self.opp_box = ttk.LabelFrame(left, text="Opponents (their cards are dead cards)")
        self.opp_box.pack(fill="both", expand=True, pady=4)
        self.opp_canvases: List[BoardCanvas] = []

        right = ttk.Frame(body)
        right.pack(side="left", fill="both", expand=True, padx=(10, 0))

        manual = ttk.LabelFrame(right, text="Manual spot — type cards as 'As Kd 7c'")
        manual.pack(fill="x")
        for label, var in (("Top", self.var_top), ("Middle", self.var_mid),
                           ("Bottom", self.var_bot), ("Opponent", self.var_opp),
                           ("Dealt", self.var_dealt)):
            row = ttk.Frame(manual)
            row.pack(fill="x", pady=2)
            ttk.Label(row, text=f"{label}:", width=10).pack(side="left")
            ttk.Entry(row, textvariable=var).pack(side="left", fill="x", expand=True, padx=4)
        actions = ttk.Frame(manual)
        actions.pack(fill="x", pady=4)
        ttk.Button(actions, text="SOLVE", command=self._solve_manual).pack(side="left", padx=4)
        ttk.Button(actions, text="Clear", command=self._clear_manual).pack(side="left")
        ttk.Label(actions, textvariable=self.var_deck,
                  foreground="#555").pack(side="left", padx=12)

        cand = ttk.LabelFrame(right, text="Candidates, best first")
        cand.pack(fill="both", expand=True, pady=6)
        self.tree = ttk.Treeview(cand, columns=("ev", "placement", "discard", "detail"),
                                 show="headings", height=9)
        for col, head, width in (("ev", "EV", 70), ("placement", "Placement", 260),
                                 ("discard", "Discard", 70), ("detail", "Detail", 220)):
            self.tree.heading(col, text=head)
            self.tree.column(col, width=width, anchor="w")
        self.tree.pack(fill="both", expand=True, padx=4, pady=4)
        self.tree.bind("<<TreeviewSelect>>", self._preview_selected)

        log_box = ttk.LabelFrame(right, text="Log")
        log_box.pack(fill="both", expand=True)
        self.log = tk.Text(log_box, height=8, bg="#101410", fg="#cfe8d8",
                           font=("Consolas", 9), wrap="word")
        self.log.pack(fill="both", expand=True, padx=4, pady=4)

        self._last_request: Optional[SolveRequest] = None
        self._last_advice = None
        self._render_manual()

    # -------------------------------------------------------------- helpers
    def _say(self, text: str) -> None:
        self.log.insert("end", text.rstrip() + "\n")
        self.log.see("end")

    def _reload_solvers(self) -> None:
        names = solver_api.available()
        self.cmb_solver["values"] = names
        if self.var_solver.get() not in names and names:
            self.var_solver.set(names[0])
        self._say(f"solvers: {', '.join(names) or 'none'}")

    @staticmethod
    def _parse(text: str) -> List[int]:
        out = []
        for token in (text or "").replace(",", " ").split():
            out.append(text_to_code(token))
        return out

    def _clear_manual(self) -> None:
        for var in (self.var_top, self.var_mid, self.var_bot, self.var_opp, self.var_dealt):
            var.set("")
        self.tree.delete(*self.tree.get_children())
        self._render_manual()

    def _render_manual(self, proposal: Optional[dict] = None) -> None:
        try:
            board = Board(self._parse(self.var_top.get()),
                          self._parse(self.var_mid.get()),
                          self._parse(self.var_bot.get()))
        except ValueError:
            return
        from ofc import evaluator as ev
        labels = {}
        for row in ROWS:
            cards = board.row(row)
            if len(cards) in (3, 5):
                labels[row] = ev.hand_name(cards)
        self.hero_canvas.render(board, proposal, labels)

    # --------------------------------------------------------------- solve
    def _solve_manual(self) -> None:
        try:
            board = Board(self._parse(self.var_top.get()),
                          self._parse(self.var_mid.get()),
                          self._parse(self.var_bot.get()))
            dealt = self._parse(self.var_dealt.get())
            opponent = Board(bottom=[])
            opponent_cards = self._parse(self.var_opp.get())
        except ValueError as exc:
            self._say(f"bad card: {exc}")
            return

        if len(dealt) not in (3, 5) and len(dealt) < 13:
            self._say(f"deal 5 cards (opening), 3 (street), or 13+ (fantasyland); got {len(dealt)}")
            return

        opponents = []
        if opponent_cards:
            # Opponent cards are entered as one flat list; they only matter as
            # dead cards and as something to be scored against, so they are
            # laid out bottom-first.
            opponent.bottom = opponent_cards[:5]
            opponent.middle = opponent_cards[5:10]
            opponent.top = opponent_cards[10:13]
            opponents = [OpponentView(seat_id=1, name="villain", board=opponent)]

        request = SolveRequest(
            board=board, dealt=dealt, opponents=opponents,
            in_fantasyland=len(dealt) >= 13,
            time_budget=float(self.var_budget.get()),
        )
        self._run_solver(request)

    def _run_solver(self, request: SolveRequest) -> None:
        name = self.var_solver.get()
        self.var_status.set(f"solving with {name}…")
        self.root.update_idletasks()

        advice = solve(request, name)
        self.var_status.set("idle")
        self.var_deck.set(f"deck {len(request.deck)} · dead {len(request.dead_cards)}")
        self._show(request, advice)

    def _show(self, request: SolveRequest, advice) -> None:
        self._last_request, self._last_advice = request, advice
        self.tree.delete(*self.tree.get_children())

        if advice.note:
            self._say(f"note: {advice.note}")
        if not advice.candidates:
            self._say(describe(advice))
            return

        for index, candidate in enumerate(advice.candidates):
            texts = candidate.action.to_texts()
            placement = ", ".join(f"{p['card']}→{p['row']}" for p in texts["placements"])
            detail = " ".join(f"{k}={v}" for k, v in candidate.detail.items())
            self.tree.insert("", "end", iid=str(index),
                             values=(f"{candidate.ev:+.2f}", placement,
                                     texts["discard"] or "-", detail))
        self.tree.selection_set("0")
        self._preview(0)

        self._say(describe(advice))
        for problem in validate(request, advice.best.action).problems:
            self._say(f"  ! {problem}")

    def _preview_selected(self, _event=None) -> None:
        selection = self.tree.selection()
        if selection:
            self._preview(int(selection[0]))

    def _preview(self, index: int) -> None:
        if not self._last_advice or index >= len(self._last_advice.candidates):
            return
        request = self._last_request
        candidate = self._last_advice.candidates[index]

        proposal = {TOP: [], MIDDLE: [], BOTTOM: []}
        for card, row in candidate.action.placements:
            proposal[row].append(card)

        if request.in_fantasyland:
            # A Fantasyland answer is the whole board, not an addition to it.
            board = Board(proposal[TOP], proposal[MIDDLE], proposal[BOTTOM])
            proposal = {}
        else:
            board = request.board

        from ofc import evaluator as ev
        final = candidate.action.apply(request.board) if not request.in_fantasyland else board
        labels = {}
        for row in ROWS:
            cards = final.row(row)
            if len(cards) in (3, 5):
                labels[row] = ev.hand_name(cards)
        if final.is_complete():
            royalties = final.royalties()
            labels[BOTTOM] = f"{labels.get(BOTTOM, '')}  royalty {royalties['total']}" + (
                "  FOUL" if royalties["foul"] else "")

        self.hero_canvas.render(board, proposal, labels)
        self._render_opponents(request.opponents)

    def _render_opponents(self, opponents) -> None:
        for canvas in self.opp_canvases:
            canvas.destroy()
        self.opp_canvases = []
        for opponent in opponents:
            canvas = BoardCanvas(self.opp_box,
                                 title=f"seat {opponent.seat_id} {opponent.name}".strip())
            canvas.pack(padx=6, pady=4)
            canvas.render(opponent.board)
            self.opp_canvases.append(canvas)

    # ---------------------------------------------------------------- live
    def _start(self) -> None:
        try:
            hero_uid = int(self.var_hero_uid.get() or 0)
        except ValueError:
            self._say("hero UID must be a number")
            return
        if not hero_uid:
            self._say("set your hero UID first — without it the bot cannot tell which seat is yours")
            return

        self.advisor = Advisor(hero_uid=hero_uid, solver=self.var_solver.get(),
                               event_queue=self.events,
                               time_budget=float(self.var_budget.get()))
        self.advisor.start()

        self.btn_start.config(state="disabled")
        self.btn_stop.config(state="normal")
        self.var_status.set("attaching…")
        self.capture_thread = threading.Thread(target=self._run_capture, daemon=True)
        self.capture_thread.start()

    def _run_capture(self) -> None:
        try:
            from ofc.capture import OfcCapture
            self.capture = OfcCapture(process_name=self.var_process.get(),
                                      advisor=self.advisor)
            self.root.after(0, lambda: self.var_status.set("attached"))
            self.capture.run()
        except Exception as exc:                   # noqa: BLE001
            message = f"{type(exc).__name__}: {exc}"
            self.root.after(0, lambda: self._say(f"capture failed: {message}"))
            self.root.after(0, lambda: self.var_status.set("capture failed"))
        finally:
            self.root.after(0, self._stopped)

    def _stop(self) -> None:
        if self.capture is not None:
            try:
                self.capture.stop()
            except Exception:                      # noqa: BLE001
                pass
        if self.advisor is not None:
            self.advisor.stop()
        self._stopped()

    def _stopped(self) -> None:
        self.btn_start.config(state="normal")
        self.btn_stop.config(state="disabled")
        self.var_status.set("idle")

    # -------------------------------------------------------------- events
    def _drain(self) -> None:
        while True:
            try:
                event = self.events.get_nowait()
            except queue.Empty:
                break
            kind = event.get("type")
            if kind == "ofc_advice":
                self._on_live_advice(event)
            elif kind == "ofc_result":
                self._say(f"hand finished on table {event.get('table_id')}")
        self.root.after(100, self._drain)

    def _on_live_advice(self, event: dict) -> None:
        request = event.get("request", {})
        advice = event.get("advice", {})
        best = advice.get("best")
        self.var_deck.set(f"deck {request.get('deck_size', 0)}")

        board = request.get("board", {})
        self.var_top.set(" ".join(board.get("top", [])))
        self.var_mid.set(" ".join(board.get("middle", [])))
        self.var_bot.set(" ".join(board.get("bottom", [])))
        self.var_dealt.set(" ".join(request.get("dealt", [])))

        self.tree.delete(*self.tree.get_children())
        for index, candidate in enumerate(advice.get("candidates", [])):
            placement = ", ".join(f"{p['card']}→{p['row']}" for p in candidate["placements"])
            detail = " ".join(f"{k}={v}" for k, v in candidate.items()
                              if k not in ("placements", "discard", "ev"))
            self.tree.insert("", "end", iid=str(index),
                             values=(f"{candidate['ev']:+.2f}", placement,
                                     candidate.get("discard") or "-", detail))

        proposal = {TOP: [], MIDDLE: [], BOTTOM: []}
        if best:
            for entry in best["placements"]:
                proposal[entry["row"]].append(text_to_code(entry["card"]))
        self._render_manual(proposal)

        if best:
            moves = ", ".join("{}→{}".format(p["card"], p["row"])
                              for p in best["placements"])
            discard = best.get("discard")
            self._say(f"table {event.get('table_id')} street {request.get('street')}: "
                      f"{moves}"
                      f"{'  discard ' + discard if discard else ''}"
                      f"  ev={best['ev']:+.2f}")
        for problem in event.get("errors", []) + event.get("warnings", []):
            self._say(f"  ! {problem}")

    def run(self) -> None:
        self._say(f"solvers available: {', '.join(solver_api.available()) or 'none'}")
        self._say("manual mode works without the game running — type a spot and press SOLVE")
        self.root.mainloop()


def main() -> None:
    OfcGui().run()


if __name__ == "__main__":
    main()
