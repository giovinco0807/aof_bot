"""PPPoker packet capture via Frida hook.

Attaches to the PPPoker process, injects pppoker_hook.js,
and logs all intercepted protobuf packets.

Features:
  - Real-time hand tracking and hand_db recording
  - GTO solver API integration for decision support
  - Per-table state management (supports multiple tables)
"""

import frida
import json
import sys
import time
import signal
import sqlite3
import subprocess
import requests
from pathlib import Path
from datetime import datetime
from collections import defaultdict
from dataclasses import dataclass, field
from typing import Optional, Dict, List

# Pre-computed list of absolute trash hands (0% all-in freq in all situations)
ALWAYS_FOLD_HANDS = {
    '32o', '42o', '43o', '52o', '53o', '54o', '62o', '63o', '64o', '65o', 
    '72o', '73o', '74o', '75o', '76o', '82o', '83o', '84o', '85o', '86o', '87o', 
    '92o', '93o', '94o', '95o', '96o', 'T2s', 'T2o', 'T3o', 'T4o', 'T5o', 'T6o', 
    'J2o', 'J3o', 'J4o', 'J5o', 'J6o', 'Q2o', 'Q3o'
}

# Add automation directory to path for adb_input import
sys.path.insert(0, str(Path(__file__).parent.parent / "automation"))

# ============ Constants ============

# ActionType enum from PPPoker protobuf
ACTION_FOLD = 1
ACTION_CHECK = 2
ACTION_CALL = 3
ACTION_RAISE = 4  # Also used for AllIn
ACTION_BET = 7
ACTION_SB = 8
ACTION_BB = 9
ACTION_ANTE = 10
ACTION_FAST_FOLD = 16

ACTION_NAMES = {
    0: "None", ACTION_FOLD: "Fold", ACTION_CHECK: "Check", ACTION_CALL: "Call",
    ACTION_RAISE: "Raise", ACTION_BET: "Bet", ACTION_SB: "SB", ACTION_BB: "BB",
    ACTION_ANTE: "Ante", ACTION_FAST_FOLD: "FastFold",
}

HAND_TYPES = {
    -1: "Fold", 0: "None", 1: "High Card", 2: "Pair", 3: "Two Pair",
    4: "Three Kind", 5: "Straight", 6: "Flush", 7: "Full House",
    8: "Four Kind", 9: "Straight Flush", 10: "Royal Flush",
}

ROOM_TYPES = {6: "AllinFold"}
GAME_MODES = {13: "AllinFold", 508: "AofNlh"}

# Stage enum
STAGE_NONE = 0
STAGE_PREFLOP = 1
STAGE_FLOP = 2
STAGE_TURN = 3
STAGE_RIVER = 4
STAGE_COMPLETE = 5
STAGE_NAMES = {0: "None", 1: "Preflop", 2: "Flop", 3: "Turn", 4: "River", 5: "Complete"}

# Solver API
API_URL = "http://localhost:8080"

# Paths
DB_PATH = Path(__file__).parent.parent / "automation" / "data" / "packets.db"
HANDS_DB_PATH = Path(__file__).parent.parent / "automation" / "data" / "hands.db"
SCRIPT_PATH = Path(__file__).parent / "pppoker_hook.js"


# ============ Card Decoding ============

def decode_card(raw: int) -> str:
    """Decode PPPoker card integer: card = (suit << 8) | rank."""
    if raw <= 0:
        return ""
    rank_val = raw & 0xFF
    suit_val = (raw >> 8) & 0xFF
    rank_map = {2:"2",3:"3",4:"4",5:"5",6:"6",7:"7",8:"8",9:"9",
                10:"T",11:"J",12:"Q",13:"K",14:"A"}
    suit_map = {1:"d", 2:"c", 3:"h", 4:"s"}
    r = rank_map.get(rank_val, "?")
    s = suit_map.get(suit_val, "?")
    return r + s


# ============ Packet DB ============

def init_packet_db(db_path: Path = DB_PATH):
    db_path.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(str(db_path))
    conn.execute("""
        CREATE TABLE IF NOT EXISTS packets (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            timestamp TEXT NOT NULL,
            packet_type TEXT NOT NULL,
            table_id INTEGER,
            data TEXT
        )
    """)
    conn.execute("CREATE INDEX IF NOT EXISTS idx_packets_type ON packets(packet_type)")
    conn.execute("CREATE INDEX IF NOT EXISTS idx_packets_time ON packets(timestamp)")
    conn.commit()
    conn.close()


# --- Background packet writer (non-blocking) ---
import queue as _queue
import threading as _threading

_packet_queue = _queue.Queue()
_packet_writer_started = False


def _packet_writer_loop(db_path: Path):
    """Background thread: drains the packet queue and writes to SQLite."""
    conn = sqlite3.connect(str(db_path))
    conn.execute("PRAGMA journal_mode=WAL")  # WAL mode for better concurrency
    conn.execute("PRAGMA synchronous=NORMAL")  # Faster writes, still crash-safe
    while True:
        batch = []
        # Block until at least one item is available
        try:
            item = _packet_queue.get(timeout=5.0)
            batch.append(item)
        except _queue.Empty:
            continue
        # Drain any additional items that are already queued
        while not _packet_queue.empty():
            try:
                batch.append(_packet_queue.get_nowait())
            except _queue.Empty:
                break
        # Batch insert
        conn.executemany(
            "INSERT INTO packets (timestamp, packet_type, table_id, data) VALUES (?, ?, ?, ?)",
            batch
        )
        conn.commit()


def _ensure_writer_started(db_path: Path):
    global _packet_writer_started
    if not _packet_writer_started:
        _packet_writer_started = True
        t = _threading.Thread(target=_packet_writer_loop, args=(db_path,), daemon=True)
        t.start()


def save_packet(packet_type: str, table_id: int, data: dict, db_path: Path = DB_PATH):
    """Non-blocking: enqueue packet for background writing."""
    _ensure_writer_started(db_path)
    _packet_queue.put_nowait((
        datetime.now().isoformat(),
        packet_type,
        table_id,
        json.dumps(data, ensure_ascii=False),
    ))


# ============ Hand DB ============

def init_hands_db(db_path: Path = HANDS_DB_PATH):
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
        CREATE TABLE IF NOT EXISTS hand_players (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            hand_id INTEGER NOT NULL,
            player_id TEXT NOT NULL,
            seat_id INTEGER,
            position TEXT,
            prior_actions TEXT,
            action TEXT,
            cards TEXT,
            stack_bb REAL DEFAULT 0,
            profit_chips REAL DEFAULT 0,
            profit_bb REAL DEFAULT 0,
            FOREIGN KEY (hand_id) REFERENCES hands(id)
        )
    """)
    conn.execute("""
        CREATE TABLE IF NOT EXISTS player_stats (
            player_id TEXT PRIMARY KEY,
            hands_seen INTEGER DEFAULT 0,
            hands_pushed INTEGER DEFAULT 0,
            total_profit_chips REAL DEFAULT 0,
            total_profit_bb REAL DEFAULT 0,
            showdown_count INTEGER DEFAULT 0,
            showdown_hands TEXT DEFAULT '[]',
            last_seen TEXT
        )
    """)
    conn.execute("CREATE INDEX IF NOT EXISTS idx_hands_timestamp ON hands(timestamp)")
    conn.execute("CREATE INDEX IF NOT EXISTS idx_player_stats_id ON player_stats(player_id)")
    conn.execute("CREATE INDEX IF NOT EXISTS idx_hand_players_hand ON hand_players(hand_id)")
    conn.execute("CREATE INDEX IF NOT EXISTS idx_hand_players_player ON hand_players(player_id)")

    # Migrate old player_stats schema if needed
    try:
        conn.execute("SELECT total_profit_chips FROM player_stats LIMIT 1")
    except sqlite3.OperationalError:
        conn.execute("ALTER TABLE player_stats ADD COLUMN total_profit_chips REAL DEFAULT 0")
        conn.execute("ALTER TABLE player_stats ADD COLUMN total_profit_bb REAL DEFAULT 0")
        conn.execute("ALTER TABLE player_stats ADD COLUMN showdown_count INTEGER DEFAULT 0")
        conn.execute("ALTER TABLE player_stats ADD COLUMN showdown_hands TEXT DEFAULT '[]'")

    # Migrate old hand_players schema to include new situation context
    try:
        conn.execute("SELECT prior_actions FROM hand_players LIMIT 1")
    except sqlite3.OperationalError:
        conn.execute("ALTER TABLE hand_players ADD COLUMN prior_actions TEXT DEFAULT ''")
        conn.execute("ALTER TABLE hand_players ADD COLUMN stack_bb REAL DEFAULT 0")

    # OFC (Pineapple) hands table
    conn.execute("""
        CREATE TABLE IF NOT EXISTS ofc_hands (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            timestamp TEXT NOT NULL,
            table_id TEXT,
            game_id TEXT,
            num_players INTEGER NOT NULL,
            dealer_seat INTEGER,
            player_data TEXT NOT NULL,
            stakes REAL DEFAULT 0
        )
    """)
    conn.execute("CREATE INDEX IF NOT EXISTS idx_ofc_hands_time ON ofc_hands(timestamp)")

    # Clean up invalid player IDs (seat0, seat1, etc.)
    conn.execute("DELETE FROM player_stats WHERE player_id LIKE 'seat%'")
    conn.execute("DELETE FROM hand_players WHERE player_id LIKE 'seat%'")

    conn.commit()
    conn.close()


def save_hand_record(record: dict, db_path: Path = HANDS_DB_PATH):
    """Save a completed hand to the hand history database."""
    conn = sqlite3.connect(str(db_path))
    cur = conn.execute("""
        INSERT INTO hands (timestamp, table_id, num_players, bb_size,
                           dealer_seat, player_ids, stacks, actions,
                           cards, board, winner_seat, pot_chips, rake_chips)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
    """, (
        record["timestamp"], record["table_id"], record["num_players"],
        record["bb_size"], record["dealer_seat"], record["player_ids"],
        record["stacks"], record["actions"], record["cards"],
        record["board"], record["winner_seat"], record["pot_chips"],
        record.get("rake_chips", 0.0),
    ))
    hand_id = cur.lastrowid

    pids = record["player_ids"].split(",")
    actions = record["actions"].split(",")
    cards_list = record["cards"].split(",") if record["cards"] else []
    profits = record.get("profits", {})  # seat_id(str) -> chips
    positions = record.get("positions", [])
    prior_actions_list = record.get("prior_actions", [])
    stacks_list = record["stacks"].split(",") if record["stacks"] else []
    names_list = record.get("names", [])
    bb_size = record["bb_size"]
    now = record["timestamp"]

    for i, (pid, action) in enumerate(zip(pids, actions)):
        pid = pid.strip()
        if not pid:
            continue
        action = action.strip().upper()
        pushed = 1 if action == "A" else 0
        card = cards_list[i].strip() if i < len(cards_list) else ""
        seat_id = int(record.get("seat_ids", [i])[i]) if "seat_ids" in record else i
        position = positions[i] if i < len(positions) else ""
        prior_actions = prior_actions_list[i] if i < len(prior_actions_list) else ""
        stack_chips = float(stacks_list[i]) if i < len(stacks_list) and stacks_list[i] else 0.0
        stack_bb = round(stack_chips / bb_size, 2) if bb_size > 0 else 0.0
        player_name = names_list[i] if i < len(names_list) else ""

        profit_chips = profits.get(str(seat_id), 0)
        profit_bb = profit_chips / bb_size if bb_size > 0 else 0

        # Insert per-player hand record
        conn.execute("""
            INSERT INTO hand_players (hand_id, player_id, seat_id, position, prior_actions,
                                      action, cards, stack_bb, profit_chips, profit_bb)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """, (hand_id, pid, seat_id, position, prior_actions, action, card,
              stack_bb, profit_chips, round(profit_bb, 2)))

        # Update player stats
        showdown_card = card if card else ""
        conn.execute("""
            INSERT INTO player_stats (player_id, player_name, hands_seen, hands_pushed,
                                      total_profit_chips, total_profit_bb,
                                      showdown_count, showdown_hands, last_seen)
            VALUES (?, ?, 1, ?, ?, ?, ?, ?, ?)
            ON CONFLICT(player_id) DO UPDATE SET
                player_name = COALESCE(NULLIF(?, ''), player_stats.player_name),
                hands_seen = hands_seen + 1,
                hands_pushed = hands_pushed + ?,
                total_profit_chips = total_profit_chips + ?,
                total_profit_bb = total_profit_bb + ?,
                showdown_count = showdown_count + ?,
                last_seen = ?
        """, (pid, player_name, pushed, profit_chips, round(profit_bb, 2),
              1 if showdown_card else 0,
              json.dumps([showdown_card]) if showdown_card else "[]",
              now,
              player_name, pushed, profit_chips, round(profit_bb, 2),
              1 if showdown_card else 0, now))

        # Append showdown hand to JSON array
        if showdown_card:
            try:
                row = conn.execute(
                    "SELECT showdown_hands FROM player_stats WHERE player_id = ?",
                    (pid,)
                ).fetchone()
                if row:
                    hands = json.loads(row[0]) if row[0] else []
                    if showdown_card not in hands[-20:]:  # keep last 20 unique
                        hands.append(showdown_card)
                    conn.execute(
                        "UPDATE player_stats SET showdown_hands = ? WHERE player_id = ?",
                        (json.dumps(hands), pid)
                    )
            except Exception:
                pass

    conn.commit()
    conn.close()
    return hand_id


# ============ Per-Table Hand State ============

@dataclass
class SeatInfo:
    seat_id: int = -1
    uid: int = 0
    name: str = ""
    chips: int = 0
    action: str = ""   # "A" (allin), "F" (fold), "" (not acted)
    cards: str = ""    # e.g. "AhKd"


@dataclass
class HandState:
    """Tracks the state of a single hand in progress."""
    table_id: int = 0
    num_seats: int = 4
    bb_size: int = 2000   # in raw chips (e.g. 2000 = 1BB if blind=2000)
    blind: int = 2000
    dealer_idx: int = -1
    seats: Dict[int, SeatInfo] = field(default_factory=dict)
    board: List[str] = field(default_factory=list)
    stage: int = 0
    pot: int = 0
    winner_seat: int = -1
    winner_uid: int = 0
    hand_complete: bool = False
    profits: Dict[int, int] = field(default_factory=dict)  # seat_id -> profit (chips)
    rake_chips: int = 0 # the rake taken by the house
    hero_cards: str = ""   # Hero's hole cards
    hero_seat: int = -1
    is_aof: bool = False
    game_mode: int = 0
    room_type: int = 0
    fee_point: int = 0    # rake in basis points (200 = 2%)
    cap: int = 0
    stack_bb: float = 8.0  # initial stack in BB (from table config)
    room_id: int = 0
    action_order: List[int] = field(default_factory=list)  # seat_ids in action order (first=UTG, last=BB)
    sb_seat: int = -1  # seat_id of Small Blind
    bb_seat: int = -1  # seat_id of Big Blind
    hero_acted: bool = False  # Track if hero has already acted this hand
    last_auto_play_time: float = 0.0 # Time when bot last attempted an action
    pre_folded: bool = False  # Track if we already clicked pre-action fold reservation
    pre_allined: bool = False  # Track if we already clicked pre-action all-in reservation
    cards_received_time: float = 0.0  # Time when hero cards were received

    def get_position_map(self) -> Dict[int, str]:
        """Return dict of {seat_id: position_name} based on SB and BB seats.
        AoF action order: 4P: CO→BTN→SB→BB, 3P: BTN→SB→BB, 2P: SB→BB
        """
        np = len(self.seats)
        pos_map = {}
        if self.sb_seat >= 0: pos_map[self.sb_seat] = "SB"
        if self.bb_seat >= 0: pos_map[self.bb_seat] = "BB"

        all_sids = sorted(self.seats.keys())
        remaining = [s for s in all_sids if s not in pos_map]

        if np == 3 and len(remaining) == 1:
            pos_map[remaining[0]] = "BTN"
        elif np == 4 and len(remaining) == 2:
            # BTN is immediately before SB in clockwise seat order
            if self.sb_seat in all_sids:
                sb_idx = all_sids.index(self.sb_seat)
                for s in remaining:
                    s_idx = all_sids.index(s)
                    if (sb_idx - s_idx) % len(all_sids) == 1:
                        pos_map[s] = "BTN"
                    else:
                        pos_map[s] = "CO"
        return pos_map

    def get_hero_position_and_prior(self) -> tuple[str, str]:
        """Return (hero_pos, prior_actions_string) for GTO lookup."""
        pos_map = self.get_position_map()
        hero_pos = pos_map.get(self.hero_seat, "")
        np = len(self.seats)
        POS_NAMES = {2: ["SB", "BB"], 3: ["BTN", "SB", "BB"], 4: ["CO", "BTN", "SB", "BB"]}
        names = POS_NAMES.get(np, [])
        prior = ""
        if hero_pos and names:
            for p in names:
                if p == hero_pos:
                    break
                for sid, seat in self.seats.items():
                    if pos_map.get(sid) == p:
                        prior += "A" if seat.action == "A" else ("F" if seat.action == "F" else "")
                        break
        return hero_pos, prior


@dataclass
class OFCPlayerState:
    """Tracks a single OFC player's state within a hand."""
    uid: int = 0
    seat_id: int = -1
    name: str = ""
    chips: int = 0
    head: List[str] = field(default_factory=list)
    middle: List[str] = field(default_factory=list)
    tail: List[str] = field(default_factory=list)
    fantasy: int = 0
    profit: int = 0


@dataclass
class OFCHandState:
    """Tracks the state of a single OFC (Pineapple) hand."""
    table_id: int = 0
    game_id: str = ""
    dealer_seat: int = -1
    players: Dict[int, OFCPlayerState] = field(default_factory=dict)  # seat_id -> state
    hand_complete: bool = False
    stakes: float = 0  # point value


# ============ Main Capture Class ============

class PacketCapture:
    def __init__(self, process_name="PPPoker.exe", verbose=True, enable_solver=True,
                 hero_uid: int = 0, auto_play: bool = False,
                 max_hands: int = 0, stop_time: str = "",
                 leave_if_disadvantaged: bool = False,
                 gui_queue=None, enable_exploit: bool = True):
        self.process_name = process_name
        self.verbose = verbose
        self.enable_solver = enable_solver
        try:
            self.hero_uid = int(hero_uid) if hero_uid else 0
        except ValueError:
            self.hero_uid = 0
        self.auto_play = auto_play
        self.session = None
        self.script = None
        self.running = False

        sys.path.insert(0, str(Path(__file__).parent.parent / "automation"))
        if enable_exploit:
            try:
                from exploit_manager import ExploitManager
                self.exploit_manager = ExploitManager()
                print("  [Init] Exploit Manager enabled")
            except Exception as e:
                print(f"  [Init] Failed to load ExploitManager: {e}")
                self.exploit_manager = None
        else:
            print("  [Init] Exploit Manager disabled by GUI config")
            self.exploit_manager = None

        # Auto-exit conditions
        self.max_hands = max_hands          # 0 = unlimited
        self.stop_time = stop_time          # "" = no time limit, "HH:MM" format
        self.leave_if_disadvantaged = leave_if_disadvantaged
        self.hero_hands_played = 0          # hands played by hero at current table
        self.has_left = False               # prevent double-leave
        self.captcha_active = False          # CAPTCHA freeze flag
        # Auto-play controller (PC-native first, ADB fallback)
        self.adb = None
        if auto_play:
            # Try PC-native controller first (pyautogui)
            try:
                from pc_input import PcController
                self.adb = PcController()
                self.adb.enabled = True
                if self.adb.check_connection():
                    print("[PC] PPPoker window found (auto-play ON via pyautogui)")
                else:
                    raise RuntimeError("PC controller init failed")
            except Exception as e:
                print(f"[PC] PC controller unavailable ({e}), trying ADB fallback...")
                self.adb = None
                try:
                    from adb_input import AdbController
                    self.adb = AdbController()
                    self.adb.enabled = True
                    if not self.adb.check_connection():
                        print("WARNING: No ADB device connected. Auto-play disabled.")
                        self.adb = None
                        self.auto_play = False
                    else:
                        print(f"[ADB] Connected to {self.adb.device_serial} (auto-play ON)")
                except Exception:
                    print("WARNING: Neither PC nor ADB controller available. Auto-play disabled.")
                    self.adb = None
                    self.auto_play = False

        # Per-table hand state
        self.tables: Dict[int, HandState] = {}
        self.hand_count = 0
        self.hands_saved = 0
        self.session_profit = 0
        # Persistent seat->UID mapping per table (survives hand resets)
        self.seat_uid_map: Dict[int, Dict[int, int]] = {}  # table_id -> {seat_id -> uid}
        self.seat_name_map: Dict[int, Dict[int, str]] = {} # table_id -> {seat_id -> name}
        # Track rooms we've already entered (avoid duplicate enter requests)
        self.entered_rooms: set = set()
        # OFC hand state per table
        self.ofc_tables: Dict[int, OFCHandState] = {}
        self.ofc_hands_saved = 0
        self.gui_queue = gui_queue  # Queue for sending structured data to GUI

        # Cloud database (Supabase) for multi-PC hand sharing
        self.cloud_db = None
        try:
            from cloud_db import CloudDB
            self.cloud_db = CloudDB()
        except Exception as e:
            print(f"  [Cloud] Cloud DB not available: {e}")

    def _get_table(self, table_id: int) -> HandState:
        if table_id not in self.tables:
            self.tables[table_id] = HandState(table_id=table_id)
        return self.tables[table_id]

    def _emit_gui(self, event_type: str, data: dict):
        """Send structured event to GUI queue (if connected)."""
        if self.gui_queue:
            try:
                self.gui_queue.put_nowait({"type": event_type, **data})
            except Exception:
                pass

    def _emit_gui_hand_update(self, hs: HandState):
        """Emit current hand state to GUI for display."""
        if not self.gui_queue:
            return
        uid_map = self.seat_uid_map.get(hs.table_id, {})
        name_map = self.seat_name_map.get(hs.table_id, {})
        # Only show seats participating in the current hand
        all_sids = sorted(hs.seats.keys())
        seats_data = []
        for sid in all_sids:
            seat = hs.seats.get(sid)
            uid = (seat.uid if seat else 0) or uid_map.get(sid, 0)
            name = (seat.name if seat else "") or name_map.get(sid, "")
            action = seat.action if seat else ""
            seats_data.append({
                "seat_id": sid,
                "uid": str(uid) if uid else "",
                "name": name,
                "action": action,
                "is_hero": sid == hs.hero_seat,
                "in_hand": sid in hs.seats,
            })

        # Centralized position map
        pos_map = hs.get_position_map()
        hero_pos, prior = hs.get_hero_position_and_prior()

        for s in seats_data:
            s["position"] = pos_map.get(s["seat_id"], "")

        np = len(hs.seats)
        self._emit_gui("hand_update", {
            "table_id": hs.table_id,
            "hand_num": self.hand_count,
            "num_players": np,
            "hero_cards": hs.hero_cards,
            "hero_seat": hs.hero_seat,
            "hero_position": hero_pos,
            "prior_actions": prior,
            "seats": seats_data,
            "is_aof": hs.is_aof,
        })

    def on_message(self, message, data):
        if message["type"] == "send":
            payload = message["payload"]
            msg_type = payload.get("type", "")

            if msg_type == "info":
                print(f"[INFO] {payload['message']}")
            elif msg_type == "error":
                print(f"[ERROR] {payload['message']}")
            elif msg_type == "packet_type":
                if self.verbose:
                    name = payload['name']
                    tid = payload['tableId']
                    if name not in ("HeartBeatRSP",):
                        print(f"  [{name}] table={tid}")
                        save_packet(name, tid, {})
                # Auto-play: RoundHintMultipleTableRSP signals hero's turn to act
                # Mark the table as needing auto-play (actual play happens after cards arrive)
                if self.auto_play and payload['name'] == 'RoundHintMultipleTableRSP':
                    tid = payload['tableId']
                    hs = self.tables.get(tid)
                    if hs:
                        hs.pending_auto_play = True
                        print(f"  [AutoPlay] Marked table {tid} for auto-play")
            elif msg_type == "packet":
                self._handle_packet(payload)
        elif message["type"] == "error":
            print(f"[FRIDA ERROR] {message.get('description', message)}")

    # ============ Packet Dispatch ============

    def _handle_packet(self, payload):
        name = payload["name"]
        table_id = payload["tableId"]
        pkt = payload.get("data", {})

        save_packet(name, table_id, pkt)

        handler = {
            "EnterRoomRSP": self._on_enter_room,
            "RoundStartBRC": self._on_round_start,
            "HandCardRSP": self._on_hand_card,
            "ActionBRC": self._on_action,
            "ActionNotifyBRC": self._on_action_notify,
            "ShowHandRSP": self._on_showhand,
            "WinnerRSP": self._on_winner,
            "RoundOverBRC": self._on_round_over,
            "SitDownBRC": self._on_sitdown,
            "StandUpBRC": self._on_standup,
            "OtherEnterRoomBRC": self._on_other_enter,
            "ClubRoomRSP": self._on_club_room,
            # OFC (Pineapple)
            "PineGameStartBRC": self._on_pine_game_start,
            "PineHandCardBRC": self._on_pine_hand_card,
            "PineActionBRC": self._on_pine_action,
            "PineResultBRC": self._on_pine_result,
            "PineSitDownBRC": self._on_pine_sitdown,
            "PineStandUpBRC": self._on_pine_standup,
            "PineRoomStatusBRC": self._on_pine_room_status,
            # CAPTCHA
            "ShowCaptchaRSP": self._on_show_captcha,
            "CaptchaRSP": self._on_captcha_result,
        }.get(name)

        if handler:
            try:
                handler(table_id, pkt)
            except Exception as e:
                import traceback
                print(f"[ERROR] Exception in handler '{name}': {e}")
                traceback.print_exc()

    # ============ Packet Handlers ============

    def _on_enter_room(self, table_id: int, pkt: dict):
        hs = self._get_table(table_id)
        room_id = pkt.get("roomId", 0)
        if room_id:
            self.entered_rooms.add(room_id)
            hs.room_id = room_id
        room_info = pkt.get("roomInfo", {})
        table_status = pkt.get("tableStatus", {})

        hs.room_type = pkt.get("roomType", 0)
        hs.game_mode = room_info.get("gameMode", 0)
        hs.blind = room_info.get("blind", 1000)
        hs.num_seats = room_info.get("seatNum", 4)
        hs.fee_point = room_info.get("feePoint", 0)
        hs.cap = room_info.get("cap", 0)
        hs.is_aof = hs.room_type == 6 or hs.game_mode in (13, 508) or hs.num_seats <= 4
        hs.hero_acted = False
        hs.last_auto_play_time = 0.0
        hs.pre_folded = False
        hs.hand_complete = False # Corrected typo from "Falsely specifies the small blind."
        
        # In PPPoker AoF, "blind" field usually specifies the small blind.
        hs.bb_size = hs.blind * 2 if hs.is_aof else hs.blind

        mode_str = GAME_MODES.get(hs.game_mode, str(hs.game_mode))
        rake_pct = hs.fee_point / 100
        print(f"\n{'='*60}")
        print(f"[EnterRoom] table={table_id} mode={mode_str} "
              f"blind={hs.blind} seats={hs.num_seats} rake={rake_pct:.0f}%")

        # Compute initial stack in BB from room config
        buy_in = room_info.get("buyIn", 0)
        if buy_in > 0 and hs.bb_size > 0:
            hs.stack_bb = buy_in / hs.bb_size
        elif hs.bb_size > 0:
            # Fallback: use max chips from seats
            max_chips = 0
            for seat_data in table_status.get("seats", []) if table_status else []:
                c = seat_data.get("desktopChips", 0) or seat_data.get("handChips", 0)
                if c > max_chips:
                    max_chips = c
            if max_chips > 0:
                hs.stack_bb = max_chips / hs.bb_size

        print(f"  Stack: {hs.stack_bb:.0f}BB")

        if table_status:
            hs.dealer_idx = table_status.get("dealerIdx", -1)
            if table_id not in self.seat_uid_map:
                self.seat_uid_map[table_id] = {}
            
            hs.seats.clear()
            for seat_data in table_status.get("seats", []):
                sid = seat_data.get("seatId", -1)
                player = seat_data.get("player", {})
                uid = player.get("uid", 0)
                name = player.get("name", "")
                chips = seat_data.get("handChips", 0)
                
                # Only register occupied seats
                if uid > 0:
                    hs.seats[sid] = SeatInfo(seat_id=sid, uid=uid, name=name, chips=chips)
                    self.seat_uid_map[table_id][sid] = uid
                    if name:
                        if table_id not in self.seat_name_map:
                            self.seat_name_map[table_id] = {}
                        self.seat_name_map[table_id][sid] = name
                    if uid == self.hero_uid:
                        hs.hero_seat = sid
                    print(f"  Seat {sid}: {name} (uid={uid}) chips={chips}")

    def _on_round_start(self, table_id: int, pkt: dict):
        hs = self._get_table(table_id)
        stage = pkt.get("stage", 0)
        board_raw = pkt.get("board", [])
        hand_card = pkt.get("handCard")

        if stage == STAGE_PREFLOP:
            # New hand - reset actions
            self.hand_count += 1
            for seat in hs.seats.values():
                seat.action = ""
                seat.cards = ""
            hs.board = []
            hs.winner_seat = -1
            hs.hand_complete = False
            hs.action_order = []  # Reset action order tracking
            hs.pot = 0
            hs.hero_cards = ""
            hs.sb_seat = -1
            hs.bb_seat = -1
            hs.hero_acted = False
            hs.last_auto_play_time = 0.0
            hs.pre_folded = False
            hs.pre_allined = False
            hs.cards_received_time = 0.0

            # Emit GUI event: new hand started (even in spectator mode)
            self._emit_gui_hand_update(hs)

        # Accumulate board cards
        for c in board_raw:
            card_str = decode_card(c)
            if card_str and card_str not in hs.board:
                hs.board.append(card_str)

        board_str = " ".join(hs.board) if hs.board else "-"
        print(f"\n{'='*60}")
        print(f"[Round] #{self.hand_count} {STAGE_NAMES.get(stage, stage)} | Board: {board_str}")

        # Hero's hole cards
        if hand_card:
            cards = []
            for k in ("card1", "card2"):
                c = hand_card.get(k, 0)
                if c > 0:
                    cards.append(decode_card(c))
            if cards:
                hs.hero_cards = "".join(cards)
                is_allin = hand_card.get("isAllin", False)
                print(f"  Hero cards: {' '.join(cards)} {'[AoF]' if is_allin else ''}")
                hs.cards_received_time = time.time()

                # -------------------------------------------------------
                # Trigger solver immediately on card receipt.
                # This handles the case where hero acts FIRST (UTG/BTN):
                # no ActionBRC comes before hero's turn, so we must fire
                # here rather than waiting for an opponent's ActionBRC.
                # -------------------------------------------------------
                if self.enable_solver and hs.is_aof:
                    self._execute_async(self._check_solver_advice, hs)

                # Emit GUI event: new hand with hero cards
                self._emit_gui_hand_update(hs)

                # Auto-play: now that hero cards are known, execute if pending
                if getattr(hs, 'pending_auto_play', False):
                    hs.pending_auto_play = False
                    self._auto_play_allin(table_id)

    def _on_hand_card(self, table_id: int, pkt: dict):
        """Handle HandCardRSP - hero's hole cards delivered separately from RoundStartBRC."""
        hs = self._get_table(table_id)
        cards = []
        for k in ("card1", "card2"):
            c = pkt.get(k, 0)
            if c > 0:
                cards.append(decode_card(c))
        if not cards:
            # Try nested handCard format
            hand_card = pkt.get("handCard", pkt)
            for k in ("card1", "card2"):
                c = hand_card.get(k, 0)
                if c > 0:
                    cards.append(decode_card(c))

        if cards:
            hs.hero_cards = "".join(cards)
            is_allin = pkt.get("isAllin", False)
            print(f"  Hero cards: {' '.join(cards)} {'[AoF]' if is_allin else ''}")

            if self.enable_solver and hs.is_aof:
                self._execute_async(self._check_solver_advice, hs)

            self._emit_gui_hand_update(hs)

            # Pre-action reservation for absolute trash hands
            if getattr(self, 'auto_play', False) and hs.is_aof:
                c1, c2 = hs.hero_cards[0:2], hs.hero_cards[2:4]
                try:
                    sys.path.insert(0, str(Path(__file__).parent.parent / "automation"))
                    from gto_lookup import cards_to_hand_name
                    hand_name = cards_to_hand_name(c1, c2)
                    if hand_name in ALWAYS_FOLD_HANDS:
                        print(f"\n  >>> PRE-ACTION FOLD: {hand_name} is absolute trash <<<")
                        try:
                            table_keys = list(self.tables.keys())
                            tbl_idx = table_keys.index(table_id)
                        except ValueError:
                            tbl_idx = 0
                            
                        if getattr(self, 'adb', None):
                            def do_pre_fold():
                                import time
                                time.sleep(0.5)  # Wait for UI to show the buttons
                                self.adb.tap_fold(delay=True, table_index=tbl_idx)
                                # We DO NOT set hs.pre_folded = True.
                                # If the click fails, _auto_play_allin will catch it when our turn comes.
                            self._execute_async(do_pre_fold)
                    else:
                        # Pre-allin for BB: if hand is 100% push for ALL possible priors
                        self._try_pre_allin(hs, table_id, hand_name)
                except Exception as e:
                    print(f"  [PreAction] Error: {e}")
        else:
            print(f"  [HandCardRSP] No cards in packet: {list(pkt.keys())}")

    def _on_action_notify(self, table_id: int, pkt: dict):
        """Handle ActionNotifyBRC - notifies hero it's their turn to act."""
        hs = self._get_table(table_id)
        seat_id = pkt.get("seatId", -1)
        timeout = pkt.get("timeout", 0)
        if seat_id == hs.hero_seat:
            print(f"  [ActionNotify] Hero's turn (seat {seat_id}, timeout={timeout}s)")
            self._auto_play_allin(table_id)

    def _on_action(self, table_id: int, pkt: dict):
        hs = self._get_table(table_id)
        seat_id = pkt.get("seatId", -1)
        action_type = pkt.get("actionType", 0)
        chips = pkt.get("chips", 0)
        remaining = pkt.get("handChips", 0)

        action_str = ACTION_NAMES.get(action_type, f"?{action_type}")

        # Track SB/BB seats from blind posts
        if action_type == ACTION_SB:
            hs.sb_seat = seat_id
        elif action_type == ACTION_BB:
            hs.bb_seat = seat_id

        # Track action order for position determination
        # Skip blind posts (SB/BB) - only real actions determine position
        if seat_id not in hs.action_order and action_type not in (ACTION_SB, ACTION_BB, ACTION_ANTE):
            hs.action_order.append(seat_id)

        # Update seat state
        if seat_id in hs.seats:
            seat = hs.seats[seat_id]
            if action_type == ACTION_FOLD or action_type == ACTION_FAST_FOLD:
                seat.action = "F"
            elif action_type == ACTION_RAISE or action_type == ACTION_CALL:
                seat.action = "A"  # In AoF, raise = allin
            # SB/BB are not player actions
            seat.chips = remaining
        else:
            # Seat not yet known - check persistent UID map
            uid_map = self.seat_uid_map.get(table_id, {})
            uid = uid_map.get(seat_id, 0)
            name_map = self.seat_name_map.get(table_id, {})
            name = name_map.get(seat_id, "")
            hs.seats[seat_id] = SeatInfo(seat_id=seat_id, uid=uid, name=name, chips=remaining)
            if action_type in (ACTION_FOLD, ACTION_FAST_FOLD):
                hs.seats[seat_id].action = "F"
            elif action_type in (ACTION_RAISE, ACTION_CALL):
                hs.seats[seat_id].action = "A"

        is_hero = seat_id == hs.hero_seat
        if is_hero:
            # Blind posts/antes don't count as the hero's actual decision turn
            if action_type not in (ACTION_SB, ACTION_BB, ACTION_ANTE):
                hs.hero_acted = True
            # Disarm auto-play if hero folded, but preserve cards for DB
            if action_type in (ACTION_FOLD, ACTION_FAST_FOLD):
                hs.hero_cards_for_db = hs.hero_cards  # Keep for hand history
                hs.hero_cards = ""
                hs.last_auto_play_time = 0.0

        marker = " <-- HERO" if is_hero else ""
        print(f"  Seat {seat_id}: {action_str} chips={chips} left={remaining}{marker}")

        # After an OPPONENT's action, re-check if it's now hero's turn.
        # Skip if this ActionBRC is hero's own action (already acted guard
        # inside _check_solver_advice handles it, but skip here for clarity).
        if self.enable_solver and hs.hero_cards and hs.is_aof and not is_hero:
            self._execute_async(self._check_solver_advice, hs)

        # Emit GUI event: action update
        self._emit_gui_hand_update(hs)

        # Reactive pre-allin: after opponent folds, check if BB can pre-allin
        if (not is_hero
                and action_type in (ACTION_FOLD, ACTION_FAST_FOLD)
                and hs.is_aof
                and hs.hero_cards
                and not hs.pre_folded
                and not hs.pre_allined
                and not hs.hero_acted
                and hs.hero_seat == hs.bb_seat
                and hs.bb_seat >= 0):
            self._try_reactive_pre_allin(hs, table_id)

    def _on_showhand(self, table_id: int, pkt: dict):
        hs = self._get_table(table_id)
        infos = pkt.get("info", [])

        print(f"\n{'='*60}")
        print(f"[Showdown] table={table_id}")

        all_cards = []  # Collect all players' cards with UID for verification
        for info in infos:
            seat_id = info.get("seatId", -1)
            cards = []
            for k in ("card1", "card2"):
                c = info.get(k, 0)
                if c > 0:
                    cards.append(decode_card(c))
            card_str = "".join(cards)
            if seat_id in hs.seats:
                hs.seats[seat_id].cards = card_str
            uid = hs.seats[seat_id].uid if seat_id in hs.seats else 0
            print(f"  Seat {seat_id} (uid={uid}): {' '.join(cards)}")
            if len(cards) == 2:
                all_cards.append({"uid": uid, "cards": cards})

        # Write ALL showdown cards for Android cross-verification
        if all_cards:
            self._execute_async(self._write_showdown_cards, all_cards, self.hand_count)

    def _on_winner(self, table_id: int, pkt: dict):
        hs = self._get_table(table_id)
        winners = pkt.get("winners", [])
        profits = pkt.get("profits", [])

        print(f"\n{'='*60}")
        print(f"[Winner] table={table_id}")

        total_pot = 0
        for w in winners:
            seat = w.get("seatId", -1)
            chips = w.get("chips", 0)
            hand_type = w.get("handType", 0)
            uid = w.get("uid", 0)
            total_pot += chips
            hs.winner_seat = seat
            hs.winner_uid = uid
            # Update seat UID from winner info (persistent mapping)
            if uid and seat >= 0:
                if table_id not in self.seat_uid_map:
                    self.seat_uid_map[table_id] = {}
                self.seat_uid_map[table_id][seat] = uid
                if seat in hs.seats:
                    hs.seats[seat].uid = uid
            print(f"  Winner: seat={seat} uid={uid} chips={chips} "
                  f"hand={HAND_TYPES.get(hand_type, hand_type)}")

        hs.profits = {}
        net_profit = 0
        for p in profits:
            seat = p.get("seatId", -1)
            chips = p.get("chips", 0)
            hs.profits[seat] = chips
            net_profit += chips
            sign = "+" if chips >= 0 else ""
            print(f"  Profit: seat={seat} {sign}{chips}")

            if seat == hs.hero_seat and hs.hero_seat >= 0:
                self.session_profit += chips
                prof_sign = "+" if self.session_profit >= 0 else ""
                print(f"  >>> HERO SESSION P/L: {prof_sign}{self.session_profit} <<<")

        hs.rake_chips = -net_profit
        if hs.rake_chips > 0:
            print(f"  Rake: {hs.rake_chips}")

        hs.pot = total_pot
        hs.hand_complete = True
        # Disarm auto-play completely at end of hand
        hs.last_auto_play_time = 0.0
        hs.hero_cards = ""

        # Track hero hands played
        if self.hero_uid and hs.hero_seat >= 0:
            self.hero_hands_played += 1

        # Save hand to database (non-blocking: offload to background thread)
        self._execute_async(self._save_hand, hs)

        # Check auto-exit conditions
        self._check_auto_exit(hs, profits)

    def _on_round_over(self, table_id: int, pkt: dict):
        hs = self._get_table(table_id)
        pool = pkt.get("pool", [])
        if pool:
            hs.pot = max(pool) if pool else 0

    def _on_sitdown(self, table_id: int, pkt: dict):
        hs = self._get_table(table_id)
        seat_id = pkt.get("seatId", -1)
        chips = pkt.get("chips", 0)
        player = pkt.get("player", {})
        uid = player.get("uid", 0)
        name = player.get("name", "")

        hs.seats[seat_id] = SeatInfo(seat_id=seat_id, uid=uid, name=name, chips=chips)
        if uid:
            if table_id not in self.seat_uid_map:
                self.seat_uid_map[table_id] = {}
            self.seat_uid_map[table_id][seat_id] = uid
        if name:
            if table_id not in self.seat_name_map:
                self.seat_name_map[table_id] = {}
            self.seat_name_map[table_id][seat_id] = name
        if uid == self.hero_uid:
            hs.hero_seat = seat_id
        print(f"  [SitDown] Seat {seat_id}: {name} (uid={uid}) chips={chips}")

    def _on_standup(self, table_id: int, pkt: dict):
        hs = self._get_table(table_id)
        seat_id = pkt.get("seatId", -1) if isinstance(pkt, dict) else -1
        if seat_id in hs.seats:
            name = hs.seats[seat_id].name
            del hs.seats[seat_id]
            print(f"  [StandUp] Seat {seat_id}: {name}")

        # Auto-leave if <=1 player remains
        if len(hs.seats) <= 1 and table_id in self.entered_rooms:
            print(f"  >>> Table {table_id}: {len(hs.seats)} player(s) - should leave <<<")
            self.entered_rooms.discard(table_id)

    def _on_other_enter(self, table_id: int, pkt: dict):
        user = pkt.get("user", {})
        uid = user.get("uid", 0)
        name = user.get("name", "")
        if uid:
            print(f"  [OtherEnter] {name} (uid={uid}) joined table {table_id}")

    def _on_club_room(self, table_id: int, pkt: dict):
        """Handle ClubRoomRSP - monitor room list and auto-enter when 2+ players."""
        rooms = pkt.get("rooms", [])
        club_id = pkt.get("clubId", 0)
        room_num = pkt.get("roomNum", 0)

        print(f"\n{'='*60}")
        print(f"[ClubRoom] club={club_id} rooms={room_num}")

        targets = []  # (room_id, name, display_index) for tables to auto-enter

        for idx, room in enumerate(rooms):
            rid = room.get("roomId", 0)
            name = room.get("roomName", "")
            blind = room.get("blind", 0)
            seats = room.get("seatNum", 0)
            players = room.get("players", 0)
            current = room.get("currentPlayerNum", 0)
            started = room.get("isStarted", False)
            rtype = room.get("roomType", 0)
            buyin = room.get("buyin", 0)
            owner = room.get("ownerName", "")

            status = "PLAYING" if started else "WAITING"
            print(f"  Room {rid}: {name} | blind={blind} seats={seats} "
                  f"players={current}/{seats} {status} type={rtype}")

            # Auto-enter targets: AOF (seats=4, blind=1000 for 10/20) or OFC/CT (type=9 or name contains CT)
            is_aof = (seats == 4 and blind == 1000)
            is_ofc = (rtype == 9 or "CT " in name.upper())
            if (is_aof or is_ofc) and current >= 2 and rid not in self.entered_rooms:
                game_type = "AoF" if is_aof else "OFC"
                targets.append((rid, name, idx, game_type))

        # Auto-enter disabled: only log detected tables without clicking
        for rid, rname, display_idx, gtype in targets:
            print(f"  >>> {gtype} TABLE FOUND: Room {rid} \"{rname}\" (row #{display_idx}) <<<")
            self.entered_rooms.add(rid)

        # Auto-leave: rooms with <=1 player that we previously entered
        # [DISABLED] CAUSES BUG DURING 2-TABLE STARTUP. 
        # Users manually wait for opponents at heads-up tables anyway, so forceful auto-leave is detrimental.
        # for idx, room in enumerate(rooms):
        #     rid = room.get("roomId", 0)
        #     current = room.get("currentPlayerNum", 0)
        #     if current <= 1 and rid in self.entered_rooms:
        #         name = room.get("roomName", "")
        #         print(f"  >>> Room {rid} \"{name}\" has {current} player(s) - auto-leaving DISABLED <<<")
        #         self.entered_rooms.discard(rid)
        #         # Clean up table state
        #         for tid in list(self.tables.keys()):
        #             if tid == rid:
        #                 del self.tables[tid]
        #         for tid in list(self.ofc_tables.keys()):
        #             if tid == rid:
        #                 del self.ofc_tables[tid]

    def _auto_click_leave(self):
        """Leave a table by clicking hamburger menu -> 'ゲームから退出'."""
        import ctypes
        import ctypes.wintypes
        import time as _time
        import cv2
        import numpy as np
        from PIL import ImageGrab

        user32 = ctypes.windll.user32

        hwnd = self._find_pppoker_hwnd()
        if not hwnd:
            print(f"  [AutoLeave] PPPoker window not found!")
            return

        user32.ShowWindow(hwnd, 9)
        user32.SetForegroundWindow(hwnd)
        _time.sleep(0.3)

        rect = ctypes.wintypes.RECT()
        user32.GetWindowRect(hwnd, ctypes.byref(rect))
        win_x, win_y = rect.left, rect.top
        win_w = rect.right - rect.left
        win_h = rect.bottom - rect.top

        # Load menu (hamburger) template
        data_dir = Path(__file__).parent.parent / "automation" / "data"
        menu_tpl = cv2.imread(str(data_dir / "menu_template.png"))
        leave_tpl = cv2.imread(str(data_dir / "leave_template.png"))
        if menu_tpl is None:
            print(f"  [AutoLeave] menu_template.png not found")
            return
        if leave_tpl is None:
            print(f"  [AutoLeave] leave_template.png not found")
            return

        # Screenshot
        ss = ImageGrab.grab(bbox=(win_x, win_y, win_x + win_w, win_y + win_h))
        screen = cv2.cvtColor(np.array(ss), cv2.COLOR_RGB2BGR)

        # Find all hamburger menu icons (one per open table)
        mh, mw = menu_tpl.shape[:2]
        res = cv2.matchTemplate(screen, menu_tpl, cv2.TM_CCOEFF_NORMED)
        locs = np.where(res >= 0.7)
        pts = list(zip(locs[1], locs[0]))
        pts.sort(key=lambda p: p[0])  # sort by x (left to right)
        # Deduplicate (within 50px x)
        menus = []
        for px, py in pts:
            if not menus or px - menus[-1][0] > 50:
                menus.append((px, py))

        if not menus:
            print(f"  [AutoLeave] No menu icons found on screen")
            return

        print(f"  [AutoLeave] Found {len(menus)} table menu(s)")

        # Click the LAST menu icon (rightmost table = most recently opened)
        # Could be improved to target specific table, but this works for now
        mx, my = menus[-1]
        click_x = win_x + int(mx) + mw // 2
        click_y = win_y + int(my) + mh // 2
        print(f"  [AutoLeave] Clicking menu at ({click_x},{click_y})")

        user32.SetCursorPos(int(click_x), int(click_y))
        _time.sleep(0.15)
        user32.mouse_event(0x0002, 0, 0, 0, 0)
        _time.sleep(0.05)
        user32.mouse_event(0x0004, 0, 0, 0, 0)
        _time.sleep(0.7)  # wait for menu to open

        # Re-screenshot and find 'ゲームから退出' button
        ss2 = ImageGrab.grab(bbox=(win_x, win_y, win_x + win_w, win_y + win_h))
        screen2 = cv2.cvtColor(np.array(ss2), cv2.COLOR_RGB2BGR)

        lh, lw = leave_tpl.shape[:2]
        res2 = cv2.matchTemplate(screen2, leave_tpl, cv2.TM_CCOEFF_NORMED)
        _, max_val, _, max_loc = cv2.minMaxLoc(res2)

        if max_val < 0.7:
            print(f"  [AutoLeave] 'ゲームから退出' not found (confidence={max_val:.2f})")
            # Close menu by clicking elsewhere
            user32.SetCursorPos(int(win_x + win_w // 2), int(win_y + win_h // 2))
            _time.sleep(0.1)
            user32.mouse_event(0x0002, 0, 0, 0, 0)
            _time.sleep(0.05)
            user32.mouse_event(0x0004, 0, 0, 0, 0)
            return

        lx, ly = max_loc
        click_x = win_x + int(lx) + lw // 2
        click_y = win_y + int(ly) + lh // 2
        print(f"  [AutoLeave] Clicking 'ゲームから退出' at ({click_x},{click_y})")

        user32.SetCursorPos(int(click_x), int(click_y))
        _time.sleep(0.15)
        user32.mouse_event(0x0002, 0, 0, 0, 0)
        _time.sleep(0.05)
        user32.mouse_event(0x0004, 0, 0, 0, 0)

        print(f"  [AutoLeave] Left table successfully")

    def _find_pppoker_hwnd(self):
        """Find PPPoker window handle by partial title match."""
        import ctypes
        import ctypes.wintypes
        user32 = ctypes.windll.user32
        user32.FindWindowW.restype = ctypes.wintypes.HWND

        hwnd = None
        EnumWindowsProc = ctypes.WINFUNCTYPE(ctypes.c_bool, ctypes.wintypes.HWND, ctypes.wintypes.LPARAM)

        def _enum_cb(h, _lp):
            nonlocal hwnd
            buf = ctypes.create_unicode_buffer(256)
            user32.GetWindowTextW(h, buf, 256)
            if "PPPoker" in buf.value and buf.value != "":
                hwnd = h
                return False
            return True

        user32.EnumWindows(EnumWindowsProc(_enum_cb), 0)
        return hwnd

    def _auto_click_enter(self, room_id: int, name: str, room_index: int):
        """Click the PPPoker AOF table entry using OpenCV template matching."""
        import ctypes
        import ctypes.wintypes
        import time as _time
        import cv2
        import numpy as np
        from PIL import ImageGrab

        user32 = ctypes.windll.user32

        hwnd = self._find_pppoker_hwnd()
        if not hwnd:
            print(f"  [AutoClick] PPPoker window not found!")
            return

        # Bring window to foreground
        user32.ShowWindow(hwnd, 9)  # SW_RESTORE
        user32.SetForegroundWindow(hwnd)
        _time.sleep(0.5)

        # Get window rect
        rect = ctypes.wintypes.RECT()
        user32.GetWindowRect(hwnd, ctypes.byref(rect))
        win_x, win_y = rect.left, rect.top
        win_w = rect.right - rect.left
        win_h = rect.bottom - rect.top
        print(f"  [AutoClick] PPPoker window: ({win_x},{win_y}) {win_w}x{win_h}")

        # Load Blinds template (matches all table rows universally)
        template_path = str(Path(__file__).parent.parent / "automation" / "data" / "blinds_template.png")
        template = cv2.imread(template_path)
        if template is None:
            print(f"  [AutoClick] Blinds template not found at {template_path}")
            return

        th, tw = template.shape[:2]

        # Club list panel is always on the left (~504px wide)
        panel_w = min(win_w, 504)

        def _screenshot_and_find_rows():
            """Take screenshot and find all table row positions."""
            ss = ImageGrab.grab(bbox=(win_x, win_y, win_x + panel_w, win_y + win_h))
            bgr = cv2.cvtColor(np.array(ss), cv2.COLOR_RGB2BGR)
            res = cv2.matchTemplate(bgr, template, cv2.TM_CCOEFF_NORMED)
            locs = np.where(res >= 0.7)
            pts = list(zip(locs[1], locs[0]))
            pts.sort(key=lambda p: p[1])
            rows = []
            for px, py in pts:
                if not rows or py - rows[-1][1] > 40:
                    rows.append((px, py))
            return rows

        def _drag_scroll(distance):
            """Drag to scroll the table list down by given pixels."""
            # Start drag from center of table list area
            drag_x = win_x + panel_w // 2
            drag_start_y = win_y + win_h * 3 // 4  # start from lower area
            drag_end_y = drag_start_y - distance      # drag upward = scroll down
            user32.SetCursorPos(int(drag_x), int(drag_start_y))
            _time.sleep(0.1)
            user32.mouse_event(0x0002, 0, 0, 0, 0)  # LEFTDOWN
            # Smooth drag in steps
            steps = 5
            for i in range(1, steps + 1):
                y = int(drag_start_y + (drag_end_y - drag_start_y) * i / steps)
                user32.SetCursorPos(int(drag_x), y)
                _time.sleep(0.03)
            _time.sleep(0.05)
            user32.mouse_event(0x0004, 0, 0, 0, 0)  # LEFTUP
            _time.sleep(0.4)

        # First attempt: find rows on current screen
        rows = _screenshot_and_find_rows()
        print(f"  [AutoClick] Found {len(rows)} table row(s) on screen")

        if room_index < len(rows):
            # Target row is visible
            match_x, match_y = rows[room_index]
        else:
            # Target row is below visible area - need to scroll down
            visible_count = len(rows)
            if visible_count == 0:
                print(f"  [AutoClick] No table rows found on screen")
                return

            # Estimate row height from visible rows
            if len(rows) >= 2:
                row_height = (rows[-1][1] - rows[0][1]) / (len(rows) - 1)
            else:
                row_height = 80  # fallback estimate

            # Scroll down enough to reveal the target row
            rows_to_scroll = room_index - visible_count + 2  # +2 for margin
            scroll_px = int(rows_to_scroll * row_height)
            print(f"  [AutoClick] Scrolling down {scroll_px}px to reach row #{room_index}")
            _drag_scroll(scroll_px)

            # Re-scan after scroll
            rows = _screenshot_and_find_rows()
            print(f"  [AutoClick] After scroll: {len(rows)} row(s) visible")

            if not rows:
                print(f"  [AutoClick] No rows found after scroll")
                return

            # After scrolling, the target should be near the bottom of visible rows
            # The first visible row index shifted by rows_to_scroll
            adjusted_idx = room_index - (visible_count - 2 + rows_to_scroll - rows_to_scroll)
            # Simplified: just click the last visible row (most likely the target)
            # Better: the target is now at position = room_index - first_visible_after_scroll
            # Since we scrolled exactly enough, target should be near the end
            target_in_view = min(len(rows) - 1, room_index - (visible_count - 2))
            target_in_view = max(0, min(target_in_view, len(rows) - 1))
            match_x, match_y = rows[target_in_view]
            print(f"  [AutoClick] Using row {target_in_view} of {len(rows)} visible after scroll")

        click_x = win_x + panel_w // 2
        click_y = win_y + int(match_y) + th // 2

        print(f"  [AutoClick] Clicking row #{room_index} at screen ({click_x},{click_y})")

        # Move mouse and click
        user32.SetCursorPos(int(click_x), int(click_y))
        _time.sleep(0.15)
        user32.mouse_event(0x0002, 0, 0, 0, 0)  # MOUSEEVENTF_LEFTDOWN
        _time.sleep(0.05)
        user32.mouse_event(0x0004, 0, 0, 0, 0)  # MOUSEEVENTF_LEFTUP

        print(f"  [AutoClick] Clicked! Entering room {room_id} \"{name}\"")

    # ============ OFC (Pineapple) Packet Handlers ============

    def _get_ofc_table(self, table_id: int) -> OFCHandState:
        if table_id not in self.ofc_tables:
            self.ofc_tables[table_id] = OFCHandState(table_id=table_id)
        return self.ofc_tables[table_id]

    def _on_pine_game_start(self, table_id: int, pkt: dict):
        ofc = self._get_ofc_table(table_id)
        ofc.dealer_seat = pkt.get("dealerSeatId", -1)
        ofc.game_id = pkt.get("gameId", "")
        ofc.hand_complete = False
        # Reset player hands
        for p in ofc.players.values():
            p.head = []
            p.middle = []
            p.tail = []
            p.profit = 0

        start_info = pkt.get("startInfo", [])
        for si in start_info:
            sid = si.get("seatId", -1)
            chips = si.get("chips", 0)
            if sid in ofc.players:
                ofc.players[sid].chips = chips

        player_names = ", ".join(
            f"s{p.seat_id}:{p.name or p.uid}" for p in ofc.players.values()
        )
        print(f"\n{'='*60}")
        print(f"[OFC Start] table={table_id} game={ofc.game_id} "
              f"dealer=s{ofc.dealer_seat} | {player_names}")

    def _on_pine_hand_card(self, table_id: int, pkt: dict):
        ofc = self._get_ofc_table(table_id)
        hand_cards = pkt.get("handCards", [])
        action_seat = pkt.get("actionSeatId", -1)

        for hc in hand_cards:
            uid = hc.get("uid", 0)
            sid = hc.get("seatId", -1)
            cards_raw = hc.get("cards", [])
            rnd = hc.get("round", 0)
            fantasy = hc.get("fantasy", 0)

            cards = [decode_card(c) for c in cards_raw if c > 0]
            if sid in ofc.players:
                ofc.players[sid].fantasy = fantasy

            if cards:
                print(f"  [OFC Deal] Seat {sid}: {' '.join(cards)} "
                      f"round={rnd} {'FANTASY' if fantasy else ''}")

    def _on_pine_action(self, table_id: int, pkt: dict):
        ofc = self._get_ofc_table(table_id)
        uid = pkt.get("uid", 0)
        sid = pkt.get("seatId", -1)

        head = [decode_card(c) for c in pkt.get("headCard", []) if c > 0]
        mid = [decode_card(c) for c in pkt.get("middleCard", []) if c > 0]
        tail = [decode_card(c) for c in pkt.get("tailCard", []) if c > 0]

        if sid in ofc.players:
            if head:
                ofc.players[sid].head = head
            if mid:
                ofc.players[sid].middle = mid
            if tail:
                ofc.players[sid].tail = tail

        placed = []
        if head:
            placed.append(f"H:{' '.join(head)}")
        if mid:
            placed.append(f"M:{' '.join(mid)}")
        if tail:
            placed.append(f"T:{' '.join(tail)}")
        print(f"  [OFC Action] Seat {sid}: {' | '.join(placed)}")

    def _on_pine_result(self, table_id: int, pkt: dict):
        ofc = self._get_ofc_table(table_id)
        results = pkt.get("playerResults", [])

        print(f"\n{'='*60}")
        print(f"[OFC Result] table={table_id}")

        player_data = []
        for r in results:
            uid = r.get("uid", 0)
            sid = r.get("seatId", -1)
            name = r.get("name", "")
            chips = r.get("chips", 0)
            fantasy = r.get("fantasy", 0)
            scores = r.get("scores", [])

            # Extract card layout from result
            card_info = r.get("card", {})
            head = [decode_card(c) for c in card_info.get("headCard", []) if c > 0] if card_info else []
            mid = [decode_card(c) for c in card_info.get("middleCard", []) if c > 0] if card_info else []
            tail = [decode_card(c) for c in card_info.get("tailCard", []) if c > 0] if card_info else []
            bust = card_info.get("bust", False) if card_info else False

            # Total score from all matchups
            total_profit = 0
            for s in scores:
                total_profit += s.get("profit", 0)

            if sid in ofc.players:
                ofc.players[sid].head = head
                ofc.players[sid].middle = mid
                ofc.players[sid].tail = tail
                ofc.players[sid].profit = total_profit

            head_str = " ".join(head) if head else "-"
            mid_str = " ".join(mid) if mid else "-"
            tail_str = " ".join(tail) if tail else "-"
            bust_str = " BUST!" if bust else ""
            print(f"  Seat {sid} ({name}): H[{head_str}] M[{mid_str}] T[{tail_str}]"
                  f" profit={total_profit:+d}{bust_str}")

            player_data.append({
                "uid": uid, "seat_id": sid, "name": name,
                "head": head, "middle": mid, "tail": tail,
                "fantasy": fantasy, "profit": total_profit,
                "bust": bust, "scores": scores,
            })

        ofc.hand_complete = True

        # Save to DB
        self._execute_async(self._save_ofc_hand, ofc, player_data)

    def _on_pine_sitdown(self, table_id: int, pkt: dict):
        ofc = self._get_ofc_table(table_id)
        player = pkt.get("player", {})
        if not player:
            return
        uid = player.get("uid", 0)
        sid = player.get("seatId", -1)
        name = player.get("name", "")
        chips = player.get("chips", 0)
        ofc.players[sid] = OFCPlayerState(uid=uid, seat_id=sid, name=name, chips=chips)
        print(f"  [OFC SitDown] Seat {sid}: {name} (uid={uid}) chips={chips}")

    def _on_pine_standup(self, table_id: int, pkt: dict):
        ofc = self._get_ofc_table(table_id)
        sid = pkt.get("seatId", -1)
        if sid in ofc.players:
            name = ofc.players[sid].name
            del ofc.players[sid]
            print(f"  [OFC StandUp] Seat {sid}: {name}")

        # Auto-leave if <=1 player remains
        if len(ofc.players) <= 1 and table_id in self.entered_rooms:
            print(f"  >>> OFC table {table_id}: {len(ofc.players)} player(s) - auto-leaving <<<")
            self._leave_room(table_id)
            self.entered_rooms.discard(table_id)

    def _on_pine_room_status(self, table_id: int, pkt: dict):
        ofc = self._get_ofc_table(table_id)
        players = pkt.get("players", [])
        for p in players:
            uid = p.get("uid", 0)
            sid = p.get("seatId", -1)
            name = p.get("name", "")
            chips = p.get("chips", 0)
            ofc.players[sid] = OFCPlayerState(uid=uid, seat_id=sid, name=name, chips=chips)
        print(f"  [OFC RoomStatus] table={table_id} players={len(players)}")

    # ============ CAPTCHA Handlers ============

    def _on_show_captcha(self, table_id: int, pkt: dict):
        """Handle CAPTCHA display — auto-solve math, alert for slider."""
        operator = pkt.get("operator", -1)
        operand1 = pkt.get("operand1", 0)
        operand2 = pkt.get("operand2", 0)
        choices = pkt.get("choices", [])
        choices2 = pkt.get("choices2", [])
        timeout = pkt.get("timeout", 30)

        print(f"\n{'='*60}")
        print(f"  [CAPTCHA] ⚠️  CAPTCHA DETECTED on table {table_id}!")
        print(f"  [CAPTCHA] type={'MATH' if operator == 0 else 'SLIDER'} (operator={operator})")
        print(f"  [CAPTCHA] Packet: {json.dumps({k:v for k,v in pkt.items() if k != '_rawHex'}, ensure_ascii=False)}")
        print(f"{'='*60}\n")

        # Freeze auto-play while we solve
        self.captcha_active = True
        for tid, hs in self.tables.items():
            hs.pending_auto_play = False

        if operator == 0:
            # ===== MATH CAPTCHA: auto-solve =====
            answer = operand1 + operand2
            print(f"  [CAPTCHA] Math: {operand1} + {operand2} = {answer}")
            print(f"  [CAPTCHA] Choices: {choices} / {choices2}")
            print(f"  [CAPTCHA] Timeout: {timeout}s")

            import threading
            t = threading.Thread(
                target=self._solve_captcha_math,
                args=(table_id, answer, choices, choices2),
                daemon=True
            )
            t.start()
        else:
            # ===== SLIDER / OTHER CAPTCHA: alert only =====
            print(f"  [CAPTCHA] ⚠️  SLIDER CAPTCHA — manual intervention required!")
            print(f"  [CAPTCHA] Auto-play PAUSED. Solve manually, bot will resume on CaptchaRSP.")
            import threading
            threading.Thread(target=self._captcha_alert, daemon=True).start()

    def _solve_captcha_math(self, table_id: int, answer: int, choices: list, choices2: list):
        """Auto-solve a math CAPTCHA by clicking the correct answer button."""
        import time

        # Wait for the CAPTCHA UI to fully render
        time.sleep(1.5)

        # If we have answer choices from the packet, determine which button index to click
        all_choices = choices if choices else choices2
        button_idx = -1
        if all_choices and answer in all_choices:
            button_idx = all_choices.index(answer)
            print(f"  [CAPTCHA] Answer {answer} found at index {button_idx} in choices {all_choices}")

        if button_idx < 0:
            # No choices in packet — use OCR to find the button
            print(f"  [CAPTCHA] No choices in packet, trying OCR...")
            button_idx = self._ocr_find_captcha_answer(table_id, answer)

        if button_idx >= 0 and self.adb:
            # Click the correct button
            # CAPTCHA buttons are 3 buttons at the bottom of the popup
            # From the screenshot: roughly at equal spacing in the center of the window
            self._click_captcha_button(table_id, button_idx)
        else:
            # Fallback: alert the user
            print(f"  [CAPTCHA] ⚠️  Could not auto-solve! Manual intervention needed!")
            self._captcha_alert()

    def _ocr_find_captcha_answer(self, table_id: int, answer: int) -> int:
        """Use OCR to read the 3 CAPTCHA answer buttons and return the index of the correct one."""
        try:
            import pyautogui
            import re

            # Find the PPPoker window
            try:
                import pygetwindow as gw
            except ImportError:
                return -1

            wins = gw.getWindowsWithTitle("PPPoker")
            if not wins:
                return -1

            win = wins[0]
            # Take a screenshot of the window region
            # The CAPTCHA popup is in the center of the active table
            screenshot = pyautogui.screenshot(region=(win.left, win.top, win.width, win.height))

            # Try to use pytesseract for OCR
            try:
                import pytesseract
                # Focus on the bottom half where answers are
                h = screenshot.height
                w = screenshot.width
                # Crop to the middle area (where CAPTCHA popup usually appears)
                # The CAPTCHA appears on one of the table panes
                text = pytesseract.image_to_string(screenshot, config='--psm 6 digits')
                print(f"  [CAPTCHA OCR] Full text: {text.strip()}")

                # Find all numbers in the OCR text
                numbers = [int(n) for n in re.findall(r'\b\d+\b', text)]
                print(f"  [CAPTCHA OCR] Numbers found: {numbers}")

                # The answer should be among them
                if answer in numbers:
                    # Find which of the 3 answer buttons
                    # Use image_to_data for position info
                    data = pytesseract.image_to_data(screenshot, output_type=pytesseract.Output.DICT)
                    answer_str = str(answer)
                    button_positions = []
                    for i, txt in enumerate(data['text']):
                        if txt.strip() == answer_str:
                            x = data['left'][i] + data['width'][i] // 2
                            y = data['top'][i] + data['height'][i] // 2
                            button_positions.append((x, y))

                    if button_positions:
                        # Click at the found position
                        bx, by = button_positions[0]
                        abs_x = win.left + bx
                        abs_y = win.top + by
                        print(f"  [CAPTCHA OCR] Found '{answer}' at screen ({abs_x}, {abs_y})")
                        pyautogui.click(abs_x, abs_y)
                        print(f"  [CAPTCHA] ✅ Clicked answer {answer}!")
                        return 99  # Signal: already clicked directly
            except ImportError:
                print(f"  [CAPTCHA] pytesseract not installed, trying manual approach...")

            return -1
        except Exception as e:
            print(f"  [CAPTCHA OCR Error] {e}")
            return -1

    def _click_captcha_button(self, table_id: int, button_idx: int):
        """Click the CAPTCHA answer button at the given index (0=left, 1=center, 2=right)."""
        if button_idx == 99:
            return  # Already clicked via OCR

        try:
            import pyautogui

            try:
                import pygetwindow as gw
            except ImportError:
                self._captcha_alert()
                return

            wins = gw.getWindowsWithTitle("PPPoker")
            if not wins:
                self._captcha_alert()
                return

            win = wins[0]

            # CAPTCHA button positions (relative to window)
            # From the screenshot analysis: 3 buttons at the bottom of the popup
            # The popup is roughly centered in a table pane
            # Table panes are side by side, each ~340px wide
            # Button row: ~75% down from top of window
            # Button spacing: 3 buttons evenly across ~200px

            # Determine which table pane the CAPTCHA is on
            table_keys = list(self.tables.keys())
            try:
                tbl_idx = table_keys.index(table_id)
            except ValueError:
                tbl_idx = 0

            # Window is divided into panels (lobby + table1 + table2)
            # Each table pane is roughly 1/3 of the window
            pane_width = win.width // 3
            pane_x = pane_width * (tbl_idx + 1)  # Skip lobby pane
            pane_center_x = pane_x + pane_width // 2

            # Button Y is roughly 73% down the window height
            btn_y = int(win.height * 0.73)

            # 3 buttons spread ~130px apart, centered
            btn_spacing = 65
            btn_positions = [
                pane_center_x - btn_spacing,  # Left
                pane_center_x,                 # Center
                pane_center_x + btn_spacing,   # Right
            ]

            if 0 <= button_idx < 3:
                click_x = win.left + btn_positions[button_idx]
                click_y = win.top + btn_y
                print(f"  [CAPTCHA] Clicking button {button_idx} at ({click_x}, {click_y})")
                pyautogui.click(click_x, click_y)
                print(f"  [CAPTCHA] ✅ Clicked answer button {button_idx}!")
            else:
                self._captcha_alert()
        except Exception as e:
            print(f"  [CAPTCHA Click Error] {e}")
            self._captcha_alert()

    def _captcha_alert(self):
        """Play alert sound for manual CAPTCHA intervention."""
        try:
            import winsound
            import time
            for _ in range(5):
                winsound.Beep(1000, 300)
                time.sleep(0.2)
        except Exception:
            pass

    def _on_captcha_result(self, table_id: int, pkt: dict):
        """Handle CAPTCHA result — resume auto-play if solved."""
        code = pkt.get("code", -1)
        result = pkt.get("result", -1)
        print(f"\n{'='*60}")
        print(f"  [CAPTCHA] CaptchaRSP: code={code} result={result}")
        print(f"  [CAPTCHA] Auto-play RESUMED.")
        print(f"{'='*60}\n")

        # Resume auto-play
        self.captcha_active = False
    def _save_ofc_hand(self, ofc: OFCHandState, player_data: list):
        """Save a completed OFC hand to the database."""
        try:
            conn = sqlite3.connect(str(HANDS_DB_PATH))
            conn.execute("""
                INSERT INTO ofc_hands (timestamp, table_id, game_id,
                                       num_players, dealer_seat, player_data, stakes)
                VALUES (?, ?, ?, ?, ?, ?, ?)
            """, (
                datetime.now().isoformat(),
                str(ofc.table_id),
                ofc.game_id,
                len(player_data),
                ofc.dealer_seat,
                json.dumps(player_data, ensure_ascii=False),
                ofc.stakes,
            ))
            conn.commit()
            conn.close()
            self.ofc_hands_saved += 1
            print(f"  [DB] OFC hand #{self.ofc_hands_saved} saved (game={ofc.game_id})")
        except Exception as e:
            print(f"  [DB Error] OFC save: {e}")

    # ============ Hand Recording ============

    def _save_hand(self, hs: HandState):
        """Save a completed hand to the hand history database."""
        if not hs.hand_complete:
            return

        # Build sorted seat list
        seat_ids = sorted(hs.seats.keys())
        if not seat_ids:
            return

        player_ids = []
        stacks = []
        actions = []
        cards = []

        uid_map = self.seat_uid_map.get(hs.table_id, {})
        for sid in seat_ids:
            seat = hs.seats[sid]
            uid = seat.uid or uid_map.get(sid, 0)
            player_ids.append(str(uid) if uid else "")
            stacks.append(str(seat.chips))
            actions.append(seat.action if seat.action else "?")
            # Always record hero's known cards, even on fold
            hero_cards = hs.hero_cards or getattr(hs, 'hero_cards_for_db', '')
            if sid == hs.hero_seat and not seat.cards and hero_cards:
                cards.append(hero_cards)
            else:
                cards.append(seat.cards if seat.cards else "")

        board_str = "".join(hs.board)

        # Build profits map (seat_id as string -> chips)
        profits_map = {str(sid): hs.profits.get(sid, 0) for sid in seat_ids}

        # Determine BB seat from action_order (last to act = BB)
        # Determine BB seat from action_order (last to act = BB)
        POS_NAMES = {
            2: ["SB", "BB"],
            3: ["BTN", "SB", "BB"],
            4: ["CO", "BTN", "SB", "BB"],
        }
        positions = [""] * len(seat_ids)
        
        # If BB gets a walk (everyone folded), the BB doesn't act and isn't in action_order.
        # We can append the missing seat to the end to complete the sequence.
        act_order = list(hs.action_order)
        if len(act_order) == len(seat_ids) - 1:
            missing_seats = set(seat_ids) - set(act_order)
            if len(missing_seats) == 1:
                act_order.append(list(missing_seats)[0])

        if act_order and len(act_order) == len(seat_ids):
            bb_seat = act_order[-1]  # Last to act is always BB in AoF
            dealer_seat = bb_seat
            pos_names = POS_NAMES.get(len(seat_ids), [])
            if len(pos_names) == len(seat_ids):
                # Map act_order to position names, then to seat_ids order
                ao_to_pos = {sid: pos for sid, pos in zip(act_order, pos_names)}
                positions = [ao_to_pos.get(sid, "") for sid in seat_ids]
        else:
            dealer_seat = hs.dealer_idx  # Fallback to EnterRoom value

        # Build prior_actions for each seat based on action_order
        prior_actions_list = [""] * len(seat_ids)
        action_map = {sid: (hs.seats[sid].action or "F") for sid in seat_ids if sid in hs.seats}
        
        for i, sid in enumerate(seat_ids):
            p_acts = []
            if sid in hs.action_order:
                idx = hs.action_order.index(sid)
                for prev_sid in hs.action_order[:idx]:
                    p_acts.append(action_map.get(prev_sid, "F"))
            else:
                for prev_sid in hs.action_order:
                    p_acts.append(action_map.get(prev_sid, "F"))
            prior_actions_list[i] = "-".join(p_acts)

        record = {
            "timestamp": datetime.now().isoformat(),
            "table_id": str(hs.table_id),
            "num_players": len(seat_ids),
            "bb_size": float(hs.bb_size),
            "dealer_seat": dealer_seat,
            "player_ids": ",".join(player_ids),
            "stacks": ",".join(stacks),
            "actions": ",".join(actions),
            "cards": ",".join(cards),
            "board": board_str,
            "winner_seat": hs.winner_seat,
            "pot_chips": float(hs.pot),
            "rake_chips": float(hs.rake_chips),
            "profits": profits_map,
            "seat_ids": seat_ids,
            "positions": positions,
            "prior_actions": prior_actions_list,
            "names": [hs.seats[sid].name if sid in hs.seats else "" for sid in seat_ids],
        }

        try:
            hand_id = save_hand_record(record)
            self.hands_saved += 1
            print(f"  [DB] Hand #{self.hands_saved} saved (id={hand_id}) "
                  f"actions={','.join(actions)} board={board_str or 'none'}")
        except Exception as e:
            print(f"  [DB Error] {e}")

        # Also send to API server for Rust-side player_stats (Bayesian model)
        self._send_hand_to_api(player_ids, actions, cards, hs)

        # Cloud sync (Supabase)
        if self.cloud_db and self.cloud_db.enabled:
            try:
                self.cloud_db.save_hand(record)
            except Exception as e:
                print(f"  [Cloud] Error: {e}")

    # ============ API Integration ============

    def _send_hand_to_api(self, player_ids: list, actions: list, cards: list, hs: HandState):
        """Send completed hand to API server for Rust-side Bayesian model updates."""
        try:
            api_record = {
                "timestamp": datetime.now().isoformat(),
                "num_players": len(player_ids),
                "stack_bb": hs.stack_bb,
                "player_ids": player_ids,
                "actions": "".join(a if a in ("A", "F") else "F" for a in actions),
                "showdown_cards": cards,
                "pot_bb": round(hs.pot / hs.bb_size, 1) if hs.bb_size > 0 else 0.0,
            }
            resp = requests.post(f"{API_URL}/record_hand", json=api_record, timeout=2)
            if resp.status_code == 200:
                print(f"  [API] Hand sent to solver DB")
            else:
                print(f"  [API] Failed: {resp.status_code}")
        except requests.RequestException:
            pass  # API server not running

    # ============ Solver Integration ============

    def _check_solver_advice(self, hs: HandState):
        """If it's hero's turn, query the solver for exploitative advice."""
        if not hs.hero_cards or hs.hero_seat < 0:
            return

        hero_seat = hs.seats.get(hs.hero_seat)
        if not hero_seat or hero_seat.action:
            return  # Already acted

        # Determine action order for prior_actions.
        # Use action_order (populated by _on_action as ActionBRC arrives).
        # action_order contains seat_ids of players who have already acted.
        # Prior actions are the actions of seats in action_order before hero.
        seat_ids = sorted(hs.seats.keys())
        num_seats = len(seat_ids)
        uid_map = self.seat_uid_map.get(hs.table_id, {})

        # Collect opponent UIDs for exploit query (all non-hero seats)
        opponent_uids = []
        for sid in seat_ids:
            if sid == hs.hero_seat:
                continue
            s = hs.seats[sid]
            uid = s.uid or uid_map.get(sid, 0)
            if uid:
                opponent_uids.append(str(uid))

        # Use action_order for prior — seats that acted before hero
        if hs.action_order:
            action_order = list(hs.action_order)
            # Hero hasn't acted yet, so action_order has only prior seats
        else:
            # Fallback: old dealer_idx method
            dealer_idx = hs.dealer_idx
            if dealer_idx >= 0 and dealer_idx in seat_ids:
                dealer_pos = seat_ids.index(dealer_idx)
                action_order = (
                    seat_ids[dealer_pos + 1:] + seat_ids[:dealer_pos + 1]
                )
            else:
                action_order = seat_ids

        # Prior actions: collect actions from seats that act BEFORE hero
        prior = []
        for sid in action_order:
            if sid == hs.hero_seat:
                break  # stop collecting once we reach hero's position
            s = hs.seats[sid]
            if s.action == "A":
                prior.append("A")
            elif s.action == "F":
                prior.append("F")

        prior_str = "".join(prior)
        num_players = len(seat_ids)
        stack_bb = hero_seat.chips / hs.bb_size if hs.bb_size > 0 else 8.0
        hand = hs.hero_cards

        # --- LOCAL EXPLOIT OVERRIDE (Moved to _auto_play_allin) ---
        # The logic has been shifted strictly to the `_auto_play_allin` Native Engine
        # to correctly wait for UI animations to finish rendering the Call button.
        # ----------------------------------------------------------

        # Try exploit endpoint first (uses Bayesian opponent model)
        try:
            resp = requests.get(f"{API_URL}/exploit", params={
                "hand": hand,
                "num_players": num_players,
                "stack": round(stack_bb, 1),
                "prior_actions": prior_str,
                "player_ids": ",".join(opponent_uids),
            }, timeout=1)

            if resp.status_code == 200:
                data = resp.json()
                action = data.get("action", "Fold")
                gto = data.get("gto_allin", 0)
                exploit = data.get("exploit_allin", 0)
                blended = data.get("blended_allin", 0)
                confidence = data.get("confidence", 0)

                if confidence > 0.05:
                    print(f"\n  >>> EXPLOIT: {hand} | {num_players}p {stack_bb:.0f}BB | "
                          f"prior={prior_str or 'none'} | "
                          f"GTO={gto:.1%} Exploit={exploit:.1%} "
                          f"Blend={blended:.1%} conf={confidence:.0%} -> {action} <<<")
                else:
                    print(f"\n  >>> GTO: {hand} | {num_players}p {stack_bb:.0f}BB | "
                          f"prior={prior_str or 'none'} | "
                          f"push={gto:.1%} -> {action} (no opponent data) <<<")
                self._execute_action(action)
                return
        except requests.RequestException:
            pass

        # Fallback to GTO-only
        try:
            resp = requests.get(f"{API_URL}/gto", params={
                "hand": hand,
                "num_players": num_players,
                "stack": round(stack_bb, 1),
                "prior_actions": prior_str,
            }, timeout=1)

            if resp.status_code == 200:
                data = resp.json()
                action = data.get("action", "Fold")
                prob = data.get("allin_probability", 0)
                print(f"\n  >>> GTO: {hand} | {num_players}p {stack_bb:.0f}BB | "
                      f"prior={prior_str or 'none'} | "
                      f"push={prob:.1%} -> {action} <<<")
                self._execute_action(action)
        except requests.RequestException:
            pass  # Solver not running

    def _check_auto_exit(self, hs: HandState, profits: list):
        """Check if we should auto-leave the table."""
        if not self.auto_play or not self.adb or self.has_left:
            return

        reason = None

        # Condition 1: Max hands reached
        if self.max_hands > 0 and self.hero_hands_played >= self.max_hands:
            reason = f"max hands reached ({self.hero_hands_played}/{self.max_hands})"

        # Condition 2: Stop time reached
        if self.stop_time:
            now = datetime.now().strftime("%H:%M")
            if now >= self.stop_time:
                reason = f"stop time reached ({self.stop_time})"

        # Condition 3: Disadvantaged table (opponents too strong)
        if self.leave_if_disadvantaged and self.hero_hands_played >= 30:
            hero_profit = self._get_hero_session_profit(hs, profits)
            if hero_profit is not None and hero_profit < -20:
                # Losing more than 20BB in this session — check if opponents are tough
                reason = f"disadvantaged (session P/L: {hero_profit:.0f} chips)"

        if reason:
            print(f"\n  >>> AUTO-EXIT: {reason} <<<")
            self.has_left = True
            self.adb.tap_leave()

    def _get_hero_session_profit(self, hs: HandState, profits: list) -> Optional[float]:
        """Get hero's cumulative profit from the current session's profits list."""
        if hs.hero_seat < 0:
            return None
        for p in profits:
            if p.get("seatId") == hs.hero_seat:
                return p.get("chips", 0)
        return None

    def _execute_action(self, action: str):
        """Execute the solver's recommended action via ADB tap."""
        if not self.auto_play or not self.adb or self.has_left:
            return
        if action == "AllIn":
            self.adb.tap_allin()
        else:
            self.adb.tap_fold()

    def _execute_async(self, func, *args, **kwargs):
        """Execute a function in a background thread to prevent blocking packet capture."""
        import threading
        def worker():
            try:
                func(*args, **kwargs)
            except Exception as e:
                print(f"  [AsyncExec] Error: {e}")
        t = threading.Thread(target=worker, daemon=True)
        t.start()

    def _try_pre_allin(self, hs, table_id: int, hand_name: str):
        """Pre-allin for BB: if hero is BB and the hand is 100% push for ALL
        possible prior action scenarios where BB must act, click All-in immediately."""
        if not getattr(self, 'auto_play', False) or not getattr(self, 'adb', None):
            return
        if hs.bb_seat < 0 or hs.hero_seat != hs.bb_seat:
            return  # Only for BB

        np = len(hs.seats)
        if np < 2 or np > 4:
            return

        try:
            from gto_lookup import GtoLookup
            if not hasattr(self, '_gto_lookup') or self._gto_lookup is None:
                self._gto_lookup = GtoLookup()

            # Generate priors where BB actually needs to act
            # (at least one player pushed — if ALL fold, BB wins automatically)
            all_priors = self._generate_bb_priors(np)

            checked = 0
            for prior in all_priors:
                freq = self._gto_lookup.get_push_freq(hand_name, np, "BB", prior)
                if freq < 0:  # No chart for this prior (doesn't occur in practice)
                    continue
                if freq < 0.95:  # Not ~100% push for this prior
                    return
                checked += 1

            if checked == 0:
                return  # No valid priors found

            # All valid priors are ~100% push! Pre-allin!
            print(f"\n  >>> PRE-ACTION ALL-IN: {hand_name} is 100% push as BB ({checked} priors checked) <<<")
            # NOTE: pre_allined is NOT set because the actual click is disabled.
            # _auto_play_allin will handle the click when ActionNotifyBRC arrives.

            try:
                table_keys = list(self.tables.keys())
                tbl_idx = table_keys.index(table_id)
            except ValueError:
                tbl_idx = 0

            def do_pre_allin():
                # DISABLED: Tapping all-in coordinates during pre-action actually clicks "Check/Fold" in PPPoker AoF!
                # This causes premium hands to instantly fold.
                pass
                # import time
                # time.sleep(0.5)
                # self.adb.tap_allin(delay=True, table_index=tbl_idx)
            self._execute_async(do_pre_allin)

        except Exception as e:
            print(f"  [PreAllin] Error: {e}")

    def _generate_bb_priors(self, np: int) -> list:
        """Generate prior action strings where BB needs to act.
        Only includes scenarios where at least one player pushed (A),
        because if ALL fold, BB wins automatically and doesn't act."""
        from itertools import product
        num_before_bb = np - 1
        priors = []
        for combo in product("AF", repeat=num_before_bb):
            s = "".join(combo)
            if "A" in s:  # BB only acts if someone pushed
                priors.append(s)
        return priors

    def _try_reactive_pre_allin(self, hs, table_id: int):
        """Reactive pre-allin: after opponents fold, check if the remaining
        possible prior extensions all lead to 100% push for BB.

        Example: 4P, CO Fold, BTN Fold → prior so far = "FF"
        Remaining: only SB to act → check "FFA" (SB pushes)
        If 100% push → pre-allin!
        """
        if not getattr(self, 'auto_play', False) or not getattr(self, 'adb', None):
            return

        np = len(hs.seats)
        if np < 2 or np > 4:
            return

        try:
            from gto_lookup import GtoLookup, cards_to_hand_name
            if not hasattr(self, '_gto_lookup') or self._gto_lookup is None:
                self._gto_lookup = GtoLookup()

            c1, c2 = hs.hero_cards[0:2], hs.hero_cards[2:4]
            hand_name = cards_to_hand_name(c1, c2)

            # Build current prior string from actions seen so far
            # action_order contains seat_ids in order of action
            current_prior = ""
            for sid in hs.action_order:
                if sid == hs.hero_seat:
                    continue  # Skip hero
                seat = hs.seats.get(sid)
                if seat and seat.action in ("F", "A"):
                    current_prior += seat.action

            # How many players haven't acted yet (excluding BB)?
            players_before_bb = np - 1
            acted_count = len(current_prior)
            remaining = players_before_bb - acted_count

            if remaining < 0:
                return

            if remaining == 0:
                # All opponents have acted, just check the one final prior
                if "A" not in current_prior:
                    return  # All folded, BB wins automatically
                freq = self._gto_lookup.get_push_freq(hand_name, np, "BB", current_prior)
                if freq < 0:
                    return
                if freq >= 0.95:
                    print(f"\n  >>> REACTIVE PRE-ALLIN: {hand_name} BB, prior='{current_prior}', freq={freq:.0%} <<<")
                    # NOTE: pre_allined is NOT set because the actual click is disabled.
                    # _auto_play_allin will handle the click when ActionNotifyBRC arrives.
                    try:
                        tbl_idx = list(self.tables.keys()).index(table_id)
                    except ValueError:
                        tbl_idx = 0
                    def do_pre_allin():
                        # DISABLED: Clicks Check/Fold
                        pass
                        # import time
                        # time.sleep(0.3)
                        # self.adb.tap_allin(delay=True, table_index=tbl_idx)
                    self._execute_async(do_pre_allin)
                return

            # Generate all possible extensions for remaining players
            from itertools import product
            checked = 0
            for combo in product("AF", repeat=remaining):
                full_prior = current_prior + "".join(combo)
                if "A" not in full_prior:
                    continue  # All fold = BB wins, no action needed
                freq = self._gto_lookup.get_push_freq(hand_name, np, "BB", full_prior)
                if freq < 0:
                    continue  # No chart
                if freq < 0.95:
                    return  # Not 100% push for this extension
                checked += 1

            if checked == 0:
                return

            print(f"\n  >>> REACTIVE PRE-ALLIN: {hand_name} BB, prior='{current_prior}+?', "
                  f"all {checked} extensions are 100% push <<<")
            # NOTE: pre_allined is NOT set because the actual click is disabled.
            # _auto_play_allin will handle the click when ActionNotifyBRC arrives.

            try:
                tbl_idx = list(self.tables.keys()).index(table_id)
            except ValueError:
                tbl_idx = 0

            def do_pre_allin():
                # DISABLED: Clicks Check/Fold
                pass
                # import time
                # time.sleep(0.3)
                # self.adb.tap_allin(delay=True, table_index=tbl_idx)
            self._execute_async(do_pre_allin)

        except Exception as e:
            print(f"  [ReactivePreAllin] Error: {e}")

    def _auto_play_allin(self, table_id: int):
        """Auto-play based on GTO chart lookup. Falls back to ALL-IN if no chart."""
        if not getattr(self, 'auto_play', False) or not getattr(self, 'adb', None) or getattr(self, 'has_left', False):
            return
        if getattr(self, 'captcha_active', False):
            print(f"  [AutoPlay] BLOCKED — CAPTCHA active")
            return
        import time

        # Determine table index based on join order (insertion order)
        try:
            table_keys = list(self.tables.keys())
            tbl_idx = table_keys.index(table_id)
        except ValueError:
            tbl_idx = 0

        hs = self.tables.get(table_id)
        if not hs:
            return

        if getattr(hs, 'hero_acted', False):
            # Prevent duplicate clicks if we already acted this hand (confirmed by server)
            return

        now = time.time()
        if now - getattr(hs, 'last_auto_play_time', 0.0) < 1.0:
            # Debounce: wait 1.0s for the server's ActionBRC before trying to click again
            return

        if getattr(hs, 'hand_complete', False):
            # Do not click anything if the hand is already over (prevents rabbit-hunt misclicks)
            return

        if getattr(hs, 'pre_folded', False) or getattr(hs, 'pre_allined', False):
            # We already clicked the pre-action reservation button for this hand
            return

        hs.last_auto_play_time = now

        if not hs.hero_cards or len(hs.hero_cards) != 4:
            print(f"\n  >>> AUTO FOLD (no cards info) <<<")
            def do_fold_no_cards():
                import time
                time.sleep(0.3)
                self.adb.tap_fold(delay=False, table_index=tbl_idx)
            self._execute_async(do_fold_no_cards)
            return

        # Convert hero cards to hand name
        c1 = hs.hero_cards[0:2]
        c2 = hs.hero_cards[2:4]
        try:
            sys.path.insert(0, str(Path(__file__).parent.parent / "automation"))
            from gto_lookup import GtoLookup, cards_to_hand_name

            if not hasattr(self, '_gto_lookup') or self._gto_lookup is None:
                self._gto_lookup = GtoLookup()

            hand_name = cards_to_hand_name(c1, c2)
            np = len(hs.seats)

            hero_pos, prior = hs.get_hero_position_and_prior()
            
            # Print debug info so user can see SB/BB mapping
            print(f"  [GTO] SB={hs.sb_seat} BB={hs.bb_seat} hero={hs.hero_seat} -> {hero_pos}")

            if hero_pos:
                freq = self._gto_lookup.get_push_freq(hand_name, np, hero_pos, prior)
                should_push = freq >= 0.5 if freq >= 0 else False
                action = "PUSH" if should_push else "FOLD"
                
                # --- APPLY EXPLOITS ---
                if getattr(self, "exploit_manager", None):
                    try:
                        opponent_uids = []
                        uid_map = self.seat_uid_map.get(table_id, {})
                        for sid, seat in hs.seats.items():
                            if sid != hs.hero_seat:
                                uid = seat.uid or uid_map.get(sid, 0)
                                if uid: opponent_uids.append(str(uid))
                                
                        for uid_str in opponent_uids:
                            decision = self.exploit_manager.get_adjusted_decision(uid_str, hand_name, np, prior)
                            if decision is not None:
                                should_push = decision
                                action = "PUSH" if should_push else "FOLD"
                                freq = 1.0 if should_push else 0.0
                                print(f"\n  >>> NODE-LOCK EXPLOIT OVERRIDE vs {uid_str}: {hand_name} | -> {action} <<<")
                                break
                    except Exception as e:
                        print(f"  [ExploitManager in AutoPlay] Error: {e}")
                # ------------------------
                
                if freq >= 0 or getattr(self, "exploit_manager", None) is not None:
                    print(f"\n  >>> AUTO GTO/EXPLOIT: {hand_name} | {np}P {hero_pos} prior='{prior}' | "
                          f"freq={freq*100:.0f}% -> {action} <<<")

                    # Emit to GUI
                    self._emit_gui("auto_play", {
                        "hand": hand_name, "position": hero_pos,
                        "freq": freq, "action": action,
                    })

                    print(f"  [AutoPlay] Queueing adb.tap_{'allin' if should_push else 'fold'}() on table {tbl_idx}")
                    def do_gto_action():
                        import time
                        # Wait minimum "think time" from when cards were received
                        min_think = self.adb.config.get("min_think_time", 1.0) if self.adb else 1.0
                        elapsed = time.time() - getattr(hs, 'cards_received_time', 0)
                        if elapsed < min_think:
                            time.sleep(min_think - elapsed)
                            
                        # Perform human reaction delay here, so we can check hero_acted AFTER
                        if getattr(self, 'adb', None) and hasattr(self.adb, '_human_delay'):
                            self.adb._human_delay()
                            
                        max_retries = 3
                        for attempt in range(max_retries):
                            if getattr(hs, 'hero_acted', False):
                                break
                            if getattr(hs, 'hand_complete', False):
                                break
                            if attempt > 0:
                                print(f"  [AutoPlay] Retry #{attempt} (no server confirmation yet)")
                                time.sleep(0.3)
                            if should_push:
                                self.adb.tap_allin(delay=False, table_index=tbl_idx)
                            else:
                                self.adb.tap_fold(delay=False, table_index=tbl_idx)
                            # Wait for server confirmation (1.5s)
                            for _ in range(15):
                                time.sleep(0.1)
                                if getattr(hs, 'hero_acted', False) or getattr(hs, 'hand_complete', False):
                                    break
                        if not getattr(hs, 'hero_acted', False) and not getattr(hs, 'hand_complete', False):
                            print(f"  [AutoPlay] WARNING: action not confirmed after {max_retries} attempts!")
                    self._execute_async(do_gto_action)
                    return

            # Fallback if position unknown
            print(f"\n  >>> AUTO FOLD (pos unknown: {hand_name}) <<<")
            def do_fold_unknown():
                import time
                time.sleep(0.3)
                self.adb.tap_fold(delay=False, table_index=tbl_idx)
            self._execute_async(do_fold_unknown)

        except Exception as e:
            import traceback
            print(f"  [AutoPlay] Error during chart lookup: {e}")
            traceback.print_exc()
            def do_fold_error():
                import time
                time.sleep(1.0)
                self.adb.tap_fold(delay=False, table_index=tbl_idx)
                time.sleep(3.0)
            self._execute_async(do_fold_error)

    def _write_showdown_cards(self, all_cards: list, hand_num: int):
        """Write ALL showdown cards with UIDs to shared file for Android verification."""
        import json
        from datetime import datetime as dt
        shared_file = Path(__file__).parent.parent / "automation" / "data" / "pc_hero_cards.json"
        csv_file = Path(__file__).parent.parent / "automation" / "data" / "pc_cards_log.csv"

        ts = dt.now().strftime("%H:%M:%S")
        # all_cards is [{"uid": uid, "cards": ["7c","4h"]}, ...]
        data = {
            "hand": hand_num,
            "all_hands": all_cards,
            "time": ts,
        }

        shared_file.parent.mkdir(parents=True, exist_ok=True)
        with open(shared_file, "w") as f:
            json.dump(data, f)

        all_str = " | ".join([f"uid={h['uid']}:{' '.join(h['cards'])}" for h in all_cards])
        with open(csv_file, "a") as f:
            f.write(f"{ts},{hand_num},{all_str}\n")

        print(f"  [VERIFY] Showdown: {all_str} (hand #{hand_num})")

    def _retry_loop(self):
        """Background thread that retries auto-play if the server hasn't confirmed our action."""
        import time
        while self.running:
            time.sleep(0.5)
            if not self.auto_play:
                continue

            now = time.time()
            for tid, hs in list(self.tables.items()):
                # We tried to play, but it's been >5.0s and the server hasn't sent our ActionBRC
                if hs.last_auto_play_time > 0 and not hs.hero_acted:
                    if now - hs.last_auto_play_time >= 5.0:
                        print(f"  >>> [Retry] No confirmation after 5s, retrying action on table {tid} <<<")
                        # Reset timer so we don't spam clicks every 0.1s
                        hs.last_auto_play_time = now
                        # We must reset this to 0 inside `_auto_play_allin` so debounce passes:
                        # Actually _auto_play_allin checks `time.time() - last_auto_play < 1.0`
                        # Since we set it to `now`, _auto_play_allin will reject it!
                        # So we must set `last_auto_play_time = 0` BEFORE calling `_auto_play_allin`
                        hs.last_auto_play_time = 0.0
                        self._auto_play_allin(tid)

    # ============ Connection ============

    def start(self):
        init_packet_db()
        init_hands_db()

        print(f"Attaching to {self.process_name}...")

        found_pid = None
        try:
            result = subprocess.run(
                ["tasklist", "/FI", f"IMAGENAME eq {self.process_name}", "/FO", "CSV", "/NH"],
                capture_output=True, text=True, timeout=5
            )
            for line in result.stdout.strip().split("\n"):
                if self.process_name.lower() in line.lower():
                    parts = line.strip('"').split('","')
                    if len(parts) >= 2:
                        found_pid = int(parts[1])
                        print(f"Found PID: {found_pid}")
                        break
        except Exception as e:
            print(f"tasklist failed: {e}")

        if not found_pid:
            print(f"ERROR: {self.process_name} not found. Is PPPoker running?")
            sys.exit(1)

        try:
            self.session = frida.attach(found_pid)
        except Exception as e:
            print(f"ERROR: Failed to attach to PID {found_pid}: {e}")
            sys.exit(1)

        script_code = SCRIPT_PATH.read_text(encoding="utf-8")
        self.script = self.session.create_script(script_code)
        self.script.on("message", self.on_message)

        print("Loading hook script...")
        self.script.load()
        self.running = True

        import threading
        self.retry_thread = threading.Thread(target=self._retry_loop, daemon=True)
        self.retry_thread.start()

        hero_str = f" hero_uid={self.hero_uid}" if self.hero_uid else " (spectator mode)"
        solver_str = " solver=ON" if self.enable_solver else ""
        auto_str = " AUTO-PLAY=ON" if self.auto_play else ""
        print(f"\nCapturing packets...{hero_str}{solver_str}{auto_str}")
        print(f"Packets: {DB_PATH}")
        print(f"Hands:   {HANDS_DB_PATH}")
        print(f"{'='*60}\n")

    def stop(self):
        if self.script:
            try:
                self.script.unload()
            except Exception:
                pass
        if self.session:
            try:
                self.session.detach()
            except Exception:
                pass
        self.running = False
        print(f"\nStopped. Hands: {self.hand_count} seen, {self.hands_saved} saved.")

    def run(self):
        self.start()

        def signal_handler(sig, frame):
            self.stop()
            sys.exit(0)

        signal.signal(signal.SIGINT, signal_handler)

        try:
            while self.running:
                time.sleep(0.1)
        except KeyboardInterrupt:
            self.stop()


# ============ CLI ============

def main():
    import argparse
    parser = argparse.ArgumentParser(description="PPPoker AoF packet capture + hand recorder")
    parser.add_argument("--process", default="PPPoker.exe",
                        help="Process name to attach to")
    parser.add_argument("--quiet", action="store_true",
                        help="Less verbose output")
    parser.add_argument("--hero-uid", type=int, default=0,
                        help="Your PPPoker UID (for hero detection). 0 = spectator mode")
    parser.add_argument("--no-solver", action="store_true",
                        help="Disable solver API integration")
    parser.add_argument("--auto-play", action="store_true",
                        help="Enable auto-play via ADB (taps Fold/AllIn on Android device)")
    parser.add_argument("--max-hands", type=int, default=0,
                        help="Auto-leave after N hands (0 = unlimited)")
    parser.add_argument("--stop-time", type=str, default="",
                        help="Auto-leave at this time (HH:MM format, e.g. '23:30')")
    parser.add_argument("--leave-if-disadvantaged", action="store_true",
                        help="Auto-leave if losing badly (>20BB down after 30 hands)")
    args = parser.parse_args()

    capture = PacketCapture(
        process_name=args.process,
        verbose=not args.quiet,
        enable_solver=not args.no_solver,
        hero_uid=args.hero_uid,
        auto_play=args.auto_play,
        max_hands=args.max_hands,
        stop_time=args.stop_time,
        leave_if_disadvantaged=args.leave_if_disadvantaged,
    )
    capture.run()


if __name__ == "__main__":
    main()
