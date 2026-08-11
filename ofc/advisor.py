"""Packets in, advice out.

The advisor owns the table memory and decides when there is a decision worth
solving. It exists so that neither the packet hook nor the GUI has to know
anything about solvers, and so that solving never happens on the wrong thread.

That last point matters more than it looks. The Frida callback that delivers
packets runs on Frida's own thread; anything slow done there stalls capture
and loses packets. So :meth:`Advisor.feed` only ever updates state and hands
work off — the solve itself happens on a single worker thread, which also
means two tables cannot fight each other for the CPU.

Results come back as events on a queue, in the flat shape the existing GUIs
already poll for (``{"type": ..., ...}``):

    ofc_state    a table changed; carries the full snapshot
    ofc_advice   the solver answered; carries the request and the candidates
    ofc_result   a hand finished; carries the final boards
"""

import queue
import threading
import time
from typing import Callable, Optional

from .budget import TimeBudget
from .solver import Advice, Validation, describe, solve, validate
from .state import HANDLERS, Table, Tables, apply_packet

#: Packets that can change whether hero has a decision to make.
TRIGGERS = ("PineHandCardBRC", "PineActionBRC")


class Advisor:
    """Turns the OFC packet stream into advice events.

    ``event_queue`` receives the events; anything with ``put_nowait`` works,
    including the queue the existing GUIs already drain. ``on_advice`` is an
    optional callback for a caller that wants the advice directly — the
    automation layer uses it, and it runs on the worker thread.
    """

    def __init__(self,
                 hero_uid: int = 0,
                 solver: str = "baseline",
                 event_queue: Optional[queue.Queue] = None,
                 on_advice: Optional[Callable[[Advice], None]] = None,
                 time_budget=None,
                 verbose: bool = True):
        self.hero_uid = hero_uid
        self.solver = solver
        self.event_queue = event_queue
        self.on_advice = on_advice
        #: A :class:`~ofc.budget.TimeBudget`, or a plain number for one value
        #: across every street.
        if time_budget is None:
            time_budget = TimeBudget.load()
        elif isinstance(time_budget, (int, float)):
            time_budget = TimeBudget.uniform(float(time_budget))
        self.time_budget = time_budget
        self.verbose = verbose

        self.tables = Tables(hero_uid=hero_uid)
        self.decisions = 0
        self.errors = 0

        self._work: "queue.Queue" = queue.Queue()
        self._lock = threading.Lock()
        self._running = False
        self._worker: Optional[threading.Thread] = None
        #: Guards against re-solving the same spot when several packets
        #: describe one decision.
        self._solved: dict = {}

    # ------------------------------------------------------------ lifecycle
    def start(self) -> None:
        if self._running:
            return
        self._running = True
        self._worker = threading.Thread(target=self._run, name="ofc-advisor", daemon=True)
        self._worker.start()

    def stop(self, timeout: float = 5.0) -> None:
        """Stop accepting work and wait for the worker to finish.

        The wait matters: a solve already running may be about to call
        ``on_advice``, and in auto-place mode that is a drag on a live table.
        ``_running`` going false makes the worker drop any advice it is
        holding, so a solve that outlasts the timeout still cannot act.
        """
        if not self._running:
            return
        self._running = False
        self._work.put(None)
        if self._worker:
            self._worker.join(timeout=timeout)
            if self._worker.is_alive():
                print("  [OFC] advisor worker still finishing a solve; "
                      "its result will be discarded")
            self._worker = None
        # Drop anything queued so a later start() does not replay stale spots.
        while True:
            try:
                self._work.get_nowait()
            except queue.Empty:
                break
        self._solved.clear()

    # ---------------------------------------------------------------- input
    def feed(self, name: str, table_id: int, pkt: dict) -> bool:
        """Consume one packet. Returns False when it is not an OFC packet.

        Safe to call from the Frida thread: it updates state and queues work,
        and never solves inline.
        """
        if name not in HANDLERS:
            return False
        if not self._running:
            # Stopped: do not keep queueing work nobody will drain.
            return False

        with self._lock:
            table = self.tables.get(table_id)
            apply_packet(table, name, pkt)
            snapshot = table.snapshot()
            if name == "PineResultBRC":
                self._solved.pop(table_id, None)

        self._emit("ofc_state", snapshot)

        if name == "PineResultBRC":
            self._emit("ofc_result", snapshot)
            return True

        if name in TRIGGERS:
            self._maybe_solve(table_id)
        return True

    def _maybe_solve(self, table_id: int) -> None:
        with self._lock:
            table = self.tables.get(table_id)
            if not table.hero_has_decision():
                return
            request = table.build_request(time_budget=self.time_budget)
            if request is None:
                return
            # One solve per spot: the deal packet and every opponent's
            # placement can all describe the same decision. Keyed on the
            # position rather than on time, so an opponent acting does not
            # re-solve a spot hero has already been advised on.
            fingerprint = (table.game_id, request.street, tuple(sorted(request.dealt)),
                           request.board.card_count())
            if self._solved.get(table_id) == fingerprint:
                return
            self._solved[table_id] = fingerprint

        self._work.put((table_id, request))

    # --------------------------------------------------------------- worker
    def _run(self) -> None:
        while self._running:
            item = self._work.get()
            if item is None:
                break
            table_id, request = item
            try:
                self._solve(table_id, request)
            except Exception as exc:               # noqa: BLE001 - reported, keeps the worker alive
                self.errors += 1
                # Forget the spot so a later packet can retry it, rather than
                # leaving hero with no advice for the rest of the street.
                with self._lock:
                    self._solved.pop(table_id, None)
                if self.verbose:
                    print(f"  [OFC] advisor error: {type(exc).__name__}: {exc}")

    def _solve(self, table_id: int, request) -> None:
        started = time.perf_counter()
        advice = solve(request, self.solver)
        self.decisions += 1

        check = Validation()
        if advice.best is not None:
            check = validate(request, advice.best.action)

        if self.verbose:
            clock = ("" if request.action_left is None
                     else f", table clock {request.action_left:g}s")
            print(f"\n  [OFC] table {table_id} street {request.street} "
                  f"dealt {' '.join(request.texts()['dealt'])} "
                  f"(thinking up to {request.time_budget:.1f}s{clock})")
            print(f"  {describe(advice)}")
            # An overrun is worth saying out loud: past the table's clock the
            # client places the cards itself, so late advice is no advice.
            if advice.elapsed > request.time_budget * 1.25:
                print(f"  [OFC] the solver took {advice.elapsed:.1f}s against a "
                      f"{request.time_budget:.1f}s budget")
            for problem in check.problems:
                print(f"  [OFC] {problem}")

        payload = {
            "table_id": table_id,
            "request": request.texts(),
            "advice": advice.to_dict(),
            "errors": check.errors,
            "warnings": check.warnings,
            "elapsed": round(time.perf_counter() - started, 3),
        }
        self._emit("ofc_advice", payload)

        if advice.best is None:
            # Nothing to act on, and the spot may be worth another try when
            # the next packet arrives.
            with self._lock:
                self._solved.pop(table_id, None)
            return

        # A fouling line still gets played — sometimes every line fouls. An
        # action that failed validation outright never does. Neither does one
        # that finished after the advisor was stopped.
        if self.on_advice is not None and check.ok and self._running:
            try:
                self.on_advice(advice)
            except Exception as exc:               # noqa: BLE001
                if self.verbose:
                    print(f"  [OFC] advice callback failed: {type(exc).__name__}: {exc}")

    # --------------------------------------------------------------- output
    def _emit(self, event_type: str, data: dict) -> None:
        if self.event_queue is None:
            return
        try:
            self.event_queue.put_nowait({"type": event_type, **data})
        except Exception:                          # noqa: BLE001 - a full GUI queue is not fatal
            pass

    # ----------------------------------------------------------- inspection
    def solve_now(self, table_id: int) -> Optional[Advice]:
        """Solve a table's current spot on the calling thread.

        For the replay tool and for tests, where there is no reason to go
        through the worker and every reason to get the answer back.
        """
        with self._lock:
            request = self.tables.get(table_id).build_request(time_budget=self.time_budget)
        if request is None:
            return None
        return solve(request, self.solver)


__all__ = ["Advisor", "TRIGGERS"]
