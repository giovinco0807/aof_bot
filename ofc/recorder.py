"""Keeping a record of what was played, and what should have been.

Recording happens whatever the table looks like. The strong solver only
plays heads-up, but a three-handed hand is still a hand worth having: it is
the same game, the mistakes are the same mistakes, and a table that cannot
be solved today is exactly the one worth having data from. So every decision
hero faces is written down — the position, whatever the solver managed to
say about it (including *why* it declined), and what hero actually did.

Two tables, because they answer different questions.

``hands``
    One row per finished hand: the final boards, who fouled, royalties, and
    hero's score. This is the "how am I doing" table.

``decisions``
    One row per spot hero faced: the board, the deal, the dead cards, the
    ranked candidates, and — filled in once hero acts — the placement they
    actually made and how far down the ranking it was. This is the "what am
    I getting wrong" table, and it is the one that needs the solver's full
    candidate list rather than only its pick.

Writes go through a background thread. The packet handler runs on Frida's
callback thread and a SQLite write there would stall capture.
"""

import json
import queue
import sqlite3
import threading
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path
from typing import Dict, List, Optional

from . import evaluator as ev
from .cards import code_to_text, text_to_code

DB_PATH = Path(__file__).resolve().parent / "data" / "ofc.db"

SCHEMA = """
CREATE TABLE IF NOT EXISTS hands (
    id            INTEGER PRIMARY KEY AUTOINCREMENT,
    recorded      TEXT NOT NULL,
    table_id      INTEGER,
    game_id       TEXT,
    seats         INTEGER,        -- players contesting the hand
    hero_seat     INTEGER,
    hero_fouled   INTEGER,
    hero_royalty  INTEGER,
    hero_fl_entry INTEGER,        -- cards won for fantasyland, 0 if none
    hero_profit   INTEGER,
    players       TEXT NOT NULL   -- json: every seat's final rows and result
);
CREATE INDEX IF NOT EXISTS idx_hands_recorded ON hands(recorded);
CREATE INDEX IF NOT EXISTS idx_hands_game ON hands(game_id);

CREATE TABLE IF NOT EXISTS decisions (
    id            INTEGER PRIMARY KEY AUTOINCREMENT,
    recorded      TEXT NOT NULL,
    table_id      INTEGER,
    game_id       TEXT,
    street        INTEGER,
    seats         INTEGER,        -- players contesting, so 3-handed spots are findable
    hero_seat     INTEGER,
    hero_first    INTEGER,
    board         TEXT,           -- json {top, middle, bottom}
    dealt         TEXT,           -- json list
    discards      TEXT,           -- json list
    opponents     TEXT,           -- json list of {seat_id, board}
    deck_size     INTEGER,
    solver        TEXT,
    engine        TEXT,           -- which build of it: the exact weights loaded
    note          TEXT,           -- why the solver declined, when it did
    elapsed       REAL,
    candidates    TEXT,           -- json: the ranked list, best first
    advised       TEXT,           -- json: the top candidate
    played        TEXT,           -- json: what hero actually did
    played_rank   INTEGER,        -- where that sat in the ranking, 1 = best
    ev_loss       REAL            -- best ev minus the ev of what was played
);
CREATE INDEX IF NOT EXISTS idx_decisions_recorded ON decisions(recorded);
CREATE INDEX IF NOT EXISTS idx_decisions_seats ON decisions(seats);
CREATE INDEX IF NOT EXISTS idx_decisions_loss ON decisions(ev_loss);
CREATE INDEX IF NOT EXISTS idx_decisions_engine ON decisions(engine);
"""

#: Columns added after the first release. ``CREATE TABLE IF NOT EXISTS`` does
#: nothing to a table that already exists, so a database made by an earlier
#: version keeps its old shape and every insert naming a new column fails.
#: Each entry is applied only if the column is genuinely absent; the records
#: already in the file are left exactly as they are.
ADDED_COLUMNS = (
    ("decisions", "engine", "TEXT"),
)


def _migrate(connection) -> None:
    """Bring an older database up to the current shape, additively.

    Runs before :data:`SCHEMA`, not after: the schema script indexes columns
    it declares, and indexing a column an older file has not got yet fails
    the whole script. A brand-new database has no tables here, so every entry
    is skipped and the schema creates it complete.
    """
    for table, column, kind in ADDED_COLUMNS:
        rows = connection.execute(f"PRAGMA table_info({table})").fetchall()
        if not rows:
            continue                      # the schema above just created it
        if any(row[1] == column for row in rows):
            continue
        connection.execute(f"ALTER TABLE {table} ADD COLUMN {column} {kind}")


def _now() -> str:
    return datetime.now().isoformat(timespec="seconds")


def _dump(value) -> str:
    return json.dumps(value, ensure_ascii=False)


@dataclass
class _Pending:
    """A decision written down, waiting to learn what hero actually played."""
    row_id: int
    dealt: List[int]
    board_before: Dict[str, List[str]]
    candidates: List[dict] = field(default_factory=list)


class Recorder:
    """Writes hands and decisions to SQLite, off the capture thread."""

    def __init__(self, db_path: Path = DB_PATH, verbose: bool = True):
        self.db_path = Path(db_path)
        self.verbose = verbose
        self.hands = 0
        self.decisions = 0
        self.errors = 0

        self._work: "queue.Queue" = queue.Queue()
        self._worker: Optional[threading.Thread] = None
        self._running = False
        #: Both maps belong to the writer thread and are only ever touched
        #: there, which is why neither needs a lock.
        #: table id -> the decision waiting for hero's placement.
        self._pending: Dict[int, _Pending] = {}
        #: table id -> a placement that arrived before its decision row.
        self._early: Dict[int, Dict[str, List[str]]] = {}

    # ------------------------------------------------------------ lifecycle
    def start(self) -> None:
        if self._running:
            return
        self.db_path.parent.mkdir(parents=True, exist_ok=True)
        self._running = True
        self._worker = threading.Thread(target=self._run, name="ofc-recorder",
                                        daemon=True)
        self._worker.start()

    def stop(self, timeout: float = 5.0) -> None:
        if not self._running:
            return
        self._running = False
        self._work.put(None)
        if self._worker:
            self._worker.join(timeout=timeout)
            self._worker = None

    def _run(self) -> None:
        connection = sqlite3.connect(str(self.db_path))
        try:
            connection.execute("PRAGMA journal_mode=WAL")
            # Migrate first: SCHEMA indexes the columns it declares, and an
            # index on a column an older database has not got yet fails the
            # whole script. On a database that does not exist there is nothing
            # to migrate, and SCHEMA below creates it complete.
            _migrate(connection)
            connection.executescript(SCHEMA)
            connection.commit()
            while True:
                item = self._work.get()
                if item is None:
                    break
                try:
                    item(connection)
                    connection.commit()
                except Exception as exc:             # noqa: BLE001
                    self.errors += 1
                    if self.verbose:
                        print(f"  [OFC rec] {type(exc).__name__}: {exc}")
        finally:
            try:
                connection.close()
            except Exception:                        # noqa: BLE001
                pass

    # -------------------------------------------------------------- writing
    def record_decision(self, request, advice, snapshot: Optional[dict] = None) -> None:
        """Write down a spot hero faced, solved or not.

        Called for every decision, including ones the solver declined — a
        three-handed hand the engine will not touch is still worth the row,
        and the note says why there is no advice in it.
        """
        if not self._running:
            return

        texts = request.texts()
        candidates = [c.to_dict() for c in (advice.candidates if advice else [])]
        seats = len(request.opponents) + 1
        hero_first = all(o.board.card_count() <= request.board.card_count()
                         for o in request.opponents)

        row = (
            _now(), request.table_id,
            (snapshot or {}).get("game_id", ""),
            request.street, seats, request.hero_seat, int(hero_first),
            _dump(texts["board"]), _dump(texts["dealt"]), _dump(texts["discards"]),
            _dump([{"seat_id": o["seat_id"], "board": o["board"]}
                   for o in texts["opponents"]]),
            texts["deck_size"],
            (advice.solver if advice else ""),
            (getattr(advice, "engine", "") if advice else ""),
            (advice.note if advice else ""),
            round(advice.elapsed, 3) if advice else 0.0,
            _dump(candidates),
            _dump(candidates[0]) if candidates else None,
        )

        pending = _Pending(row_id=0, dealt=list(request.dealt),
                           board_before=texts["board"], candidates=candidates)
        table_id = request.table_id

        def write(connection):
            cursor = connection.execute(
                "INSERT INTO decisions (recorded, table_id, game_id, street, seats,"
                " hero_seat, hero_first, board, dealt, discards, opponents,"
                " deck_size, solver, engine, note, elapsed, candidates, advised)"
                " VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)", row)
            pending.row_id = cursor.lastrowid
            self._pending[table_id] = pending
            self.decisions += 1
            # Hero can place before the solver has finished thinking, in which
            # case the placement arrived before this row existed. It was kept
            # rather than dropped, so apply it now.
            early = self._early.pop(table_id, None)
            if early is not None:
                self._apply_played(connection, table_id, early)

        self._work.put(write)

    def record_played(self, table_id: int, board_after: Dict[str, List[str]]) -> None:
        """Fill in what hero actually placed, and how it ranked.

        The wire never states the action, only the resulting board, so the
        placement is the difference between the board before and after — and
        the discard is whatever was dealt and did not land.
        """
        if not self._running:
            return

        def write(connection):
            if table_id in self._pending:
                self._apply_played(connection, table_id, board_after)
            else:
                # A fast hand, or a slow solver: hero acted before the
                # decision row was written. Hold it for that insert.
                self._early[table_id] = board_after

        self._work.put(write)

    def _apply_played(self, connection, table_id: int,
                      board_after: Dict[str, List[str]]) -> None:
        """Grade a placement against the advice. Writer thread only."""
        pending = self._pending.pop(table_id, None)
        if pending is None or not pending.row_id:
            return

        before = {row: list(pending.board_before.get(row, []))
                  for row in ("top", "middle", "bottom")}
        placements = []
        for row in ("top", "middle", "bottom"):
            after = list(board_after.get(row, []))
            for card in after[len(before[row]):]:
                placements.append({"card": card, "row": row})

        placed = {p["card"] for p in placements}
        discards = [code_to_text(c) for c in pending.dealt
                    if code_to_text(c) not in placed]
        played = {"placements": placements,
                  "discard": discards[0] if discards else None,
                  "discards": discards}

        rank, loss = self._grade(played, pending.candidates)
        connection.execute(
            "UPDATE decisions SET played = ?, played_rank = ?, ev_loss = ?"
            " WHERE id = ?",
            (_dump(played), rank, loss, pending.row_id))

    @staticmethod
    def _grade(played: dict, candidates: List[dict]):
        """Where hero's placement sat in the ranking, and what it cost."""
        if not candidates:
            return None, None

        def identity(entry):
            return (tuple(sorted((p["card"], p["row"])
                                 for p in entry.get("placements", ()))),
                    tuple(sorted(entry.get("discards") or
                                 ([entry["discard"]] if entry.get("discard") else []))))

        target = identity(played)
        best = candidates[0].get("ev", 0.0)
        for index, candidate in enumerate(candidates, start=1):
            if identity(candidate) == target:
                return index, round(best - candidate.get("ev", 0.0), 4)
        # Hero played something the solver did not rank. That is information
        # too — it means the candidate list was filtered, or the read of the
        # board drifted — so it is recorded as unranked rather than dropped.
        return None, None

    def record_hand(self, snapshot: dict) -> None:
        """Write down a finished hand, however many were in it."""
        if not self._running:
            return

        players = []
        hero = None
        for player in snapshot.get("players", []):
            board = player.get("board") or {}
            rows = [board.get("top", []), board.get("middle", []),
                    board.get("bottom", [])]
            entry = {
                "seat_id": player.get("seat_id"),
                "uid": player.get("uid"),
                "name": player.get("name"),
                "board": board,
                "in_hand": player.get("in_hand", True),
                "in_fantasyland": player.get("in_fantasyland", False),
            }
            if sum(len(r) for r in rows) == 13:
                try:
                    top, middle, bottom = ([text_to_code(c) for c in r] for r in rows)
                except ValueError:
                    top = middle = bottom = None
                if top is not None:
                    entry["fouled"] = ev.is_foul(top, middle, bottom)
                    entry["royalty"] = ev.total_royalty(top, middle, bottom)
                    entry["fl_entry"] = (0 if entry["fouled"]
                                         else ev.fantasyland_entry(top))
            players.append(entry)
            if player.get("is_hero"):
                hero = entry

        if not players:
            return

        contesting = sum(1 for p in players if p.get("in_hand", True))
        row = (
            _now(), snapshot.get("table_id"), snapshot.get("game_id", ""),
            contesting, snapshot.get("hero_seat", -1),
            int(bool(hero.get("fouled"))) if hero else None,
            hero.get("royalty") if hero else None,
            hero.get("fl_entry") if hero else None,
            None,                          # profit: the wire's scores, when added
            _dump(players),
        )

        def write(connection):
            connection.execute(
                "INSERT INTO hands (recorded, table_id, game_id, seats, hero_seat,"
                " hero_fouled, hero_royalty, hero_fl_entry, hero_profit, players)"
                " VALUES (?,?,?,?,?,?,?,?,?,?)", row)
            self.hands += 1

        def forget(_connection):
            table_id = snapshot.get("table_id", 0)
            self._pending.pop(table_id, None)
            self._early.pop(table_id, None)

        self._work.put(write)
        self._work.put(forget)


# ------------------------------------------------------------------ reading

def summarise(db_path: Path = DB_PATH, limit: int = 0) -> dict:
    """What the record says so far — overall and split by table size."""
    path = Path(db_path)
    if not path.is_file():
        return {"error": f"no record at {path}"}

    connection = sqlite3.connect(f"file:{path}?mode=ro", uri=True, timeout=5)
    try:
        out = {"database": str(path)}
        hands = connection.execute(
            "SELECT COUNT(*), SUM(hero_fouled), AVG(hero_royalty),"
            " SUM(CASE WHEN hero_fl_entry > 0 THEN 1 ELSE 0 END) FROM hands"
        ).fetchone()
        out["hands"] = {
            "recorded": hands[0] or 0,
            "hero_fouled": hands[1] or 0,
            "hero_royalty_mean": round(hands[2], 2) if hands[2] is not None else None,
            "hero_fantasyland": hands[3] or 0,
        }

        out["by_table_size"] = {
            str(seats): {"decisions": count, "graded": graded or 0,
                         "mean_ev_loss": round(loss, 4) if loss is not None else None}
            for seats, count, graded, loss in connection.execute(
                "SELECT seats, COUNT(*), COUNT(ev_loss), AVG(ev_loss)"
                " FROM decisions GROUP BY seats ORDER BY seats")
        }

        out["by_street"] = {
            str(street): {"decisions": count,
                          "mean_ev_loss": round(loss, 4) if loss is not None else None}
            for street, count, loss in connection.execute(
                "SELECT street, COUNT(*), AVG(ev_loss) FROM decisions"
                " GROUP BY street ORDER BY street")
        }

        out["unsolved"] = [
            {"note": note, "count": count}
            for note, count in connection.execute(
                "SELECT note, COUNT(*) FROM decisions"
                " WHERE candidates = '[]' AND note != ''"
                " GROUP BY note ORDER BY COUNT(*) DESC LIMIT 10")
        ]

        sql = ("SELECT recorded, street, seats, played_rank, ev_loss, board, dealt,"
               " advised, played FROM decisions WHERE ev_loss > 0"
               " ORDER BY ev_loss DESC")
        if limit:
            sql += f" LIMIT {int(limit)}"
        out["worst_decisions"] = [
            {"recorded": r[0], "street": r[1], "seats": r[2], "rank": r[3],
             "ev_loss": round(r[4], 3), "board": json.loads(r[5]),
             "dealt": json.loads(r[6]),
             "advised": json.loads(r[7]) if r[7] else None,
             "played": json.loads(r[8]) if r[8] else None}
            for r in connection.execute(sql).fetchall()[:limit or 10]
        ]
        return out
    finally:
        connection.close()


def mistakes(db_path: Path = DB_PATH, limit: int = 20, seats: Optional[int] = None,
             street: Optional[int] = None) -> List[dict]:
    """The decisions that cost the most, worst first.

    Only decisions the solver ranked can be graded, so a three-handed spot
    shows up in the record but not here until a solver that plays it exists.
    """
    path = Path(db_path)
    if not path.is_file():
        return []

    where = ["ev_loss IS NOT NULL", "ev_loss > 0"]
    params: List = []
    if seats is not None:
        where.append("seats = ?")
        params.append(seats)
    if street is not None:
        where.append("street = ?")
        params.append(street)

    connection = sqlite3.connect(f"file:{path}?mode=ro", uri=True, timeout=5)
    try:
        rows = connection.execute(
            "SELECT recorded, street, seats, played_rank, ev_loss, board, dealt,"
            " opponents, advised, played FROM decisions"
            f" WHERE {' AND '.join(where)} ORDER BY ev_loss DESC LIMIT {int(limit)}",
            params).fetchall()
    finally:
        connection.close()

    return [{
        "recorded": r[0], "street": r[1], "seats": r[2], "rank": r[3],
        "ev_loss": round(r[4], 3),
        "board": json.loads(r[5]), "dealt": json.loads(r[6]),
        "opponents": json.loads(r[7]) if r[7] else [],
        "advised": json.loads(r[8]) if r[8] else None,
        "played": json.loads(r[9]) if r[9] else None,
    } for r in rows]


def _moves(entry: Optional[dict]) -> str:
    if not entry:
        return "-"
    out = ", ".join(f"{p['card']}->{p['row']}" for p in entry.get("placements", ()))
    discard = entry.get("discard")
    return f"{out}  discard {discard}" if discard else out


def main() -> None:
    import argparse

    parser = argparse.ArgumentParser(
        description="What the OFC bot has recorded so far.")
    parser.add_argument("--db", type=Path, default=DB_PATH)
    parser.add_argument("--summary", action="store_true",
                        help="counts and mean EV loss, split by table size and street")
    parser.add_argument("--mistakes", type=int, nargs="?", const=20, metavar="N",
                        help="the N costliest decisions, worst first")
    parser.add_argument("--seats", type=int, help="only this many players in the hand")
    parser.add_argument("--street", type=int)
    args = parser.parse_args()

    if args.mistakes:
        found = mistakes(args.db, limit=args.mistakes, seats=args.seats,
                         street=args.street)
        if not found:
            print("nothing graded yet — a decision is only gradable once a solver "
                  "ranked it and hero then played")
            return
        for entry in found:
            print(f"\n{entry['recorded']}  street {entry['street']}  "
                  f"{entry['seats']} players  rank {entry['rank']}  "
                  f"cost {entry['ev_loss']}")
            board = entry["board"]
            print(f"  board  T[{' '.join(board.get('top', [])) or '-'}] "
                  f"M[{' '.join(board.get('middle', [])) or '-'}] "
                  f"B[{' '.join(board.get('bottom', [])) or '-'}]")
            print(f"  dealt  {' '.join(entry['dealt'])}")
            print(f"  played {_moves(entry['played'])}")
            print(f"  best   {_moves(entry['advised'])}")
        return

    print(json.dumps(summarise(args.db, limit=5), indent=2, ensure_ascii=False))


if __name__ == "__main__":
    main()


__all__ = ["Recorder", "summarise", "mistakes", "DB_PATH", "SCHEMA"]
