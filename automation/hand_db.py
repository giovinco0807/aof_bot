"""Hand history database for AoF bot (SQLite)."""

import sqlite3
import time
from pathlib import Path
from typing import Optional, List, Dict
from dataclasses import dataclass


DB_PATH = Path("data/hands.db")


@dataclass
class HandRecord:
    """A single completed AoF hand."""
    timestamp: str
    table_id: str           # Table identifier (from screen)
    num_players: int        # 2/3/4
    bb_size: float          # Big blind in chips (e.g. 20)
    dealer_seat: int        # 0-based seat index of dealer
    # Per-seat data: comma-separated, empty string for empty seats
    player_ids: str         # "pp123,pp456,pp789,pp012"
    stacks: str             # "100.0,200.0,150.0,180.0"
    actions: str            # "A,F,A,A" (A=allin, F=fold)
    cards: str              # "AhKd,,QsJc,TsTc" (empty = not shown)
    board: str              # "2s5h7s9h9c"
    winner_seat: int        # Seat of winner (-1 if unknown)
    pot_chips: float        # Total pot in chips
    rake_chips: float = 0.0 # Rake collected by the house
    id: Optional[int] = None


def init_db(db_path: Path = DB_PATH):
    """Create the database and tables if they don't exist."""
    db_path.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(str(db_path))
    conn.execute("""
        CREATE TABLE IF NOT EXISTS hands (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            timestamp TEXT NOT NULL,
            table_id TEXT,
            num_players INTEGER NOT NULL,
            bb_size REAL NOT NULL,
            dealer_seat INTEGER,
            player_ids TEXT NOT NULL,
            stacks TEXT,
            actions TEXT NOT NULL,
            cards TEXT,
            board TEXT,
            winner_seat INTEGER,
            pot_chips REAL,
            rake_chips REAL DEFAULT 0
        )
    """)
    conn.execute("""
        CREATE TABLE IF NOT EXISTS player_stats (
            player_id TEXT PRIMARY KEY,
            hands_seen INTEGER DEFAULT 0,
            hands_pushed INTEGER DEFAULT 0,
            last_seen TEXT
        )
    """)
    conn.execute("""
        CREATE INDEX IF NOT EXISTS idx_hands_timestamp ON hands(timestamp)
    """)
    conn.execute("""
        CREATE INDEX IF NOT EXISTS idx_player_stats_id ON player_stats(player_id)
    """)
    conn.commit()
    conn.close()


def save_hand(record: HandRecord, db_path: Path = DB_PATH) -> int:
    """Save a hand record and update player stats. Returns the hand ID."""
    conn = sqlite3.connect(str(db_path))
    cur = conn.execute("""
        INSERT INTO hands (timestamp, table_id, num_players, bb_size,
                           dealer_seat, player_ids, stacks, actions,
                           cards, board, winner_seat, pot_chips)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
    """, (
        record.timestamp, record.table_id, record.num_players,
        record.bb_size, record.dealer_seat, record.player_ids,
        record.stacks, record.actions, record.cards, record.board,
        record.winner_seat, record.pot_chips,
    ))
    hand_id = cur.lastrowid

    # Update player stats
    pids = record.player_ids.split(",")
    actions = record.actions.split(",")
    now = record.timestamp

    for pid, action in zip(pids, actions):
        pid = pid.strip()
        if not pid:
            continue
        pushed = 1 if action.strip().upper() == "A" else 0
        conn.execute("""
            INSERT INTO player_stats (player_id, hands_seen, hands_pushed, last_seen)
            VALUES (?, 1, ?, ?)
            ON CONFLICT(player_id) DO UPDATE SET
                hands_seen = hands_seen + 1,
                hands_pushed = hands_pushed + ?,
                last_seen = ?
        """, (pid, pushed, now, pushed, now))

    conn.commit()
    conn.close()
    return hand_id


def get_player_stats(player_id: str, db_path: Path = DB_PATH) -> Optional[Dict]:
    """Get aggregate stats for a player."""
    conn = sqlite3.connect(str(db_path))
    row = conn.execute(
        "SELECT hands_seen, hands_pushed, last_seen FROM player_stats WHERE player_id = ?",
        (player_id,)
    ).fetchone()
    conn.close()

    if row:
        seen, pushed, last = row
        return {
            "player_id": player_id,
            "hands_seen": seen,
            "hands_pushed": pushed,
            "push_rate": pushed / seen if seen > 0 else 0,
            "last_seen": last,
        }
    return None


def get_all_player_stats(db_path: Path = DB_PATH) -> List[Dict]:
    """Get stats for all known players."""
    conn = sqlite3.connect(str(db_path))
    rows = conn.execute(
        "SELECT player_id, hands_seen, hands_pushed, last_seen "
        "FROM player_stats ORDER BY hands_seen DESC"
    ).fetchall()
    conn.close()

    return [
        {
            "player_id": r[0],
            "hands_seen": r[1],
            "hands_pushed": r[2],
            "push_rate": r[2] / r[1] if r[1] > 0 else 0,
            "last_seen": r[3],
        }
        for r in rows
    ]


def get_player_hands(player_id: str, limit: int = 100,
                     db_path: Path = DB_PATH) -> List[HandRecord]:
    """Get recent hands involving a specific player."""
    conn = sqlite3.connect(str(db_path))
    rows = conn.execute(
        "SELECT * FROM hands WHERE player_ids LIKE ? ORDER BY timestamp DESC LIMIT ?",
        (f"%{player_id}%", limit)
    ).fetchall()
    conn.close()

    return [
        HandRecord(
            id=r[0], timestamp=r[1], table_id=r[2], num_players=r[3],
            bb_size=r[4], dealer_seat=r[5], player_ids=r[6], stacks=r[7],
            actions=r[8], cards=r[9], board=r[10], winner_seat=r[11],
            pot_chips=r[12],
        )
        for r in rows
    ]


def get_player_push_rate_by_position(player_id: str,
                                     db_path: Path = DB_PATH) -> Dict[str, Dict]:
    """Get push rate broken down by position for a player."""
    conn = sqlite3.connect(str(db_path))
    rows = conn.execute(
        "SELECT player_ids, actions, dealer_seat, num_players "
        "FROM hands WHERE player_ids LIKE ?",
        (f"%{player_id}%",)
    ).fetchall()
    conn.close()

    # Position labels for 2p/3p/4p
    pos_labels = {
        2: ["SB", "BB"],
        3: ["BTN", "SB", "BB"],
        4: ["UTG", "BTN", "SB", "BB"],
    }

    stats = {}  # pos_label -> {seen, pushed}

    for pids_str, actions_str, dealer_seat, np in rows:
        pids = [p.strip() for p in pids_str.split(",")]
        actions = [a.strip() for a in actions_str.split(",")]

        if player_id not in pids:
            continue

        seat = pids.index(player_id)
        labels = pos_labels.get(np, [])
        if not labels or dealer_seat < 0:
            continue

        # Calculate position relative to dealer
        pos_idx = (seat - dealer_seat) % np
        if pos_idx < len(labels):
            pos = labels[pos_idx]
        else:
            pos = f"Seat{pos_idx}"

        if pos not in stats:
            stats[pos] = {"seen": 0, "pushed": 0}

        stats[pos]["seen"] += 1
        if seat < len(actions) and actions[seat].upper() == "A":
            stats[pos]["pushed"] += 1

    for pos in stats:
        s = stats[pos]
        s["push_rate"] = s["pushed"] / s["seen"] if s["seen"] > 0 else 0

    return stats
