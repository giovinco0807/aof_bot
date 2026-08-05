"""Replay recorded OFC hands through the solver, with no game running.

Two sources, both already written by the existing capture:

``packets.db``
    Every packet the hook ever saw, with its payload. Replaying these
    reproduces a hand street by street, so a solver can be asked the same
    question the live bot would have asked, in the same order.

``hands.db`` / ``ofc_hands``
    Finished hands only — final boards and results. Useful for checking how
    a solver's arrangement compares with what was actually played, not for
    street-by-street decisions.

There is also a synthetic mode that deals random hands, so a solver can be
exercised before a single hand has been recorded.

    python -m ofc.replay --synthetic 20 --solver baseline
    python -m ofc.replay --packets automation/data/packets.db --solver mine
    python -m ofc.replay --hands automation/data/hands.db --limit 50
"""

import argparse
import json
import random
import sqlite3
import sys
from pathlib import Path
from typing import Dict, Iterator, List, Optional, Tuple

REPO_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO_ROOT))

from ofc import evaluator as ev                            # noqa: E402
from ofc.board import Board                                # noqa: E402
from ofc.cards import FULL_DECK, code_to_text, codes_to_texts, text_to_code  # noqa: E402
from ofc.solver import (                                   # noqa: E402
    Advice, OpponentView, SolveRequest, available, describe, solve, validate,
)
from ofc.state import HANDLERS, Table, apply_packet        # noqa: E402

DEFAULT_PACKETS_DB = REPO_ROOT / "automation" / "data" / "packets.db"
DEFAULT_HANDS_DB = REPO_ROOT / "automation" / "data" / "hands.db"


# ------------------------------------------------------------------ packets

def iter_packets(db_path: Path, limit: int = 0) -> Iterator[Tuple[str, int, dict]]:
    """Yield ``(name, table_id, payload)`` for recorded OFC packets, in order."""
    conn = sqlite3.connect(f"file:{db_path}?mode=ro", uri=True, timeout=5)
    try:
        names = ",".join(f"'{n}'" for n in HANDLERS)
        sql = (f"SELECT packet_type, table_id, data FROM packets "
               f"WHERE packet_type IN ({names}) ORDER BY id")
        if limit:
            sql += f" LIMIT {int(limit)}"
        for name, table_id, blob in conn.execute(sql):
            try:
                payload = json.loads(blob) if blob else {}
            except (TypeError, ValueError):
                continue
            if not isinstance(payload, dict) or not payload:
                # Early captures logged the packet name with an empty body;
                # those carry no state and are skipped rather than replayed
                # as if the table had gone blank.
                continue
            yield name, int(table_id or 0), payload
    finally:
        conn.close()


def replay_packets(db_path: Path, hero_uid: int, solver: str,
                   limit: int = 0, budget: float = 4.0,
                   verbose: bool = True) -> List[Tuple[SolveRequest, Advice]]:
    """Feed recorded packets through the state machine, solving hero's spots."""
    tables: Dict[int, Table] = {}
    seen: Dict[int, tuple] = {}
    out: List[Tuple[SolveRequest, Advice]] = []

    for name, table_id, payload in iter_packets(db_path, limit=limit):
        table = tables.get(table_id)
        if table is None:
            table = tables[table_id] = Table(table_id=table_id, hero_uid=hero_uid)
        apply_packet(table, name, payload)

        if not table.hero_has_decision():
            continue
        request = table.build_request(time_budget=budget)
        if request is None:
            continue
        fingerprint = (table.game_id, request.street, tuple(request.dealt),
                       request.board.card_count())
        if seen.get(table_id) == fingerprint:
            continue
        seen[table_id] = fingerprint

        advice = solve(request, solver)
        out.append((request, advice))
        if verbose:
            _report(request, advice)
    return out


# -------------------------------------------------------------------- hands

def iter_finished_hands(db_path: Path, limit: int = 0) -> Iterator[dict]:
    """Yield recorded finished OFC hands from ``ofc_hands``."""
    conn = sqlite3.connect(f"file:{db_path}?mode=ro", uri=True, timeout=5)
    try:
        sql = ("SELECT timestamp, table_id, game_id, dealer_seat, player_data "
               "FROM ofc_hands ORDER BY id DESC")
        if limit:
            sql += f" LIMIT {int(limit)}"
        for timestamp, table_id, game_id, dealer_seat, blob in conn.execute(sql):
            try:
                players = json.loads(blob)
            except (TypeError, ValueError):
                continue
            yield {"timestamp": timestamp, "table_id": table_id, "game_id": game_id,
                   "dealer_seat": dealer_seat, "players": players}
    finally:
        conn.close()


def audit_finished_hands(db_path: Path, limit: int = 0) -> dict:
    """Summarise recorded hands: fouls, royalties, Fantasyland rates.

    A sanity check on the capture as much as on anybody's play — if the rows
    do not add up to thirteen cards, the packet reader has drifted from the
    wire format.
    """
    hands = fouls = malformed = 0
    royalty_total = 0
    fantasy = 0
    by_player: Dict[str, dict] = {}

    for record in iter_finished_hands(db_path, limit=limit):
        hands += 1
        for player in record["players"]:
            rows = (player.get("head") or [], player.get("middle") or [],
                    player.get("tail") or [])
            if sum(len(r) for r in rows) != 13:
                malformed += 1
                continue
            try:
                top, middle, bottom = (list(map(text_to_code, r)) for r in rows)
            except ValueError:
                malformed += 1
                continue

            name = player.get("name") or str(player.get("uid", "?"))
            stats = by_player.setdefault(name, {"hands": 0, "fouls": 0, "royalty": 0,
                                                "fantasy": 0, "profit": 0})
            stats["hands"] += 1
            stats["profit"] += player.get("profit", 0)

            if ev.is_foul(top, middle, bottom):
                fouls += 1
                stats["fouls"] += 1
                continue
            royalty = ev.total_royalty(top, middle, bottom)
            royalty_total += royalty
            stats["royalty"] += royalty
            if ev.fantasyland_entry(top):
                fantasy += 1
                stats["fantasy"] += 1

    return {"hands": hands, "fouls": fouls, "malformed_rows": malformed,
            "royalty_total": royalty_total, "fantasyland": fantasy,
            "by_player": by_player}


# ---------------------------------------------------------------- synthetic

def synthetic_spots(count: int, seed: int = 0,
                    opponents: int = 1) -> Iterator[SolveRequest]:
    """Deal random but legal mid-hand spots.

    Each one is a real position: hero holds a legal partial board, the
    opponents hold theirs, and the cards on the table are consistent with a
    single deck. Enough to exercise a solver end to end before any hand has
    been recorded.
    """
    rng = random.Random(seed)
    for index in range(count):
        deck = FULL_DECK[:]
        rng.shuffle(deck)
        cursor = 0

        # Hero is somewhere between the opening deal and the last street.
        placed = rng.choice([0, 5, 7, 9, 11])
        hero = Board()
        if placed:
            rows = ["bottom"] * 5 + ["middle"] * 5 + ["top"] * 3
            rng.shuffle(rows)
            for row in rows[:placed]:
                hero.place(row, deck[cursor])
                cursor += 1

        dealt = 5 if placed == 0 else 3
        hand = deck[cursor:cursor + dealt]
        cursor += dealt

        villains = []
        for seat in range(opponents):
            board = Board()
            count_placed = min(placed, 13)
            rows = ["bottom"] * 5 + ["middle"] * 5 + ["top"] * 3
            rng.shuffle(rows)
            for row in rows[:count_placed]:
                board.place(row, deck[cursor])
                cursor += 1
            villains.append(OpponentView(seat_id=seat + 1, name=f"villain{seat + 1}",
                                         board=board))

        yield SolveRequest(board=hero, dealt=hand,
                           street=0 if placed == 0 else (placed - 5) // 2 + 1,
                           opponents=villains, seed=index)


# ----------------------------------------------------------------- reporting

def _report(request: SolveRequest, advice: Advice) -> None:
    texts = request.texts()
    print(f"\nstreet {texts['street']}  board {request.board}  "
          f"dealt {' '.join(texts['dealt'])}  deck {texts['deck_size']}")
    print(f"  {describe(advice)}")
    if advice.best is not None:
        for problem in validate(request, advice.best.action).problems:
            print(f"  ! {problem}")


def summarise(results: List[Tuple[SolveRequest, Advice]]) -> dict:
    """Aggregate a replay: how often the solver answered, and how fast."""
    answered = [a for _, a in results if a.best is not None]
    invalid = 0
    fouling = 0
    for request, advice in results:
        if advice.best is None:
            continue
        check = validate(request, advice.best.action)
        if not check.ok:
            invalid += 1
        elif check.warnings:
            fouling += 1
    times = [a.elapsed for a in answered] or [0.0]
    return {
        "spots": len(results),
        "answered": len(answered),
        "invalid": invalid,
        "fouling": fouling,
        "avg_ms": round(1000 * sum(times) / len(times), 1),
        "max_ms": round(1000 * max(times), 1),
    }


# ----------------------------------------------------------------------- cli

def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__,
                                     formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--solver", default="baseline",
                        help=f"solver to run (available: {', '.join(available())})")
    parser.add_argument("--hero-uid", type=int, default=0,
                        help="your PPPoker UID — required for packet replay")
    parser.add_argument("--packets", type=Path, nargs="?", const=DEFAULT_PACKETS_DB,
                        help="replay recorded packets street by street")
    parser.add_argument("--hands", type=Path, nargs="?", const=DEFAULT_HANDS_DB,
                        help="audit recorded finished hands")
    parser.add_argument("--synthetic", type=int, metavar="N",
                        help="deal N random spots instead of reading a database")
    parser.add_argument("--opponents", type=int, default=1)
    parser.add_argument("--limit", type=int, default=0)
    parser.add_argument("--budget", type=float, default=4.0)
    parser.add_argument("--seed", type=int, default=0)
    parser.add_argument("--quiet", action="store_true")
    args = parser.parse_args()

    if args.solver not in available():
        parser.error(f"unknown solver {args.solver!r}; available: {', '.join(available())}")

    if args.synthetic:
        results = []
        for request in synthetic_spots(args.synthetic, seed=args.seed,
                                       opponents=args.opponents):
            advice = solve(request, args.solver)
            results.append((request, advice))
            if not args.quiet:
                _report(request, advice)
        print("\n" + json.dumps(summarise(results), indent=2))
        return

    if args.hands:
        if not args.hands.is_file():
            parser.error(f"no hand database at {args.hands}")
        print(json.dumps(audit_finished_hands(args.hands, limit=args.limit), indent=2))
        return

    if args.packets:
        if not args.packets.is_file():
            parser.error(f"no packet database at {args.packets}")
        if not args.hero_uid:
            parser.error("--hero-uid is required: replay cannot tell which seat is yours")
        results = replay_packets(args.packets, args.hero_uid, args.solver,
                                 limit=args.limit, budget=args.budget,
                                 verbose=not args.quiet)
        print("\n" + json.dumps(summarise(results), indent=2))
        return

    parser.error("pick one of --synthetic, --packets or --hands")


if __name__ == "__main__":
    main()
