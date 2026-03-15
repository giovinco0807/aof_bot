"""PPPoker OFC (Pineapple) packet capture via Frida hook.

Attaches to the PPPoker process, injects pppoker_hook.js,
and logs OFC game data to SQLite.

Usage:
    python -u ofc_capture.py [--process PPPoker.exe] [--quiet]
"""

import frida
import json
import sys
import time
import signal
import sqlite3
import subprocess
from pathlib import Path
from datetime import datetime
from dataclasses import dataclass, field
from typing import Optional, Dict, List

# ============ Constants ============

HAND_TYPES = {
    0: "None", 1: "High Card", 2: "Pair", 3: "Two Pair",
    4: "Three Kind", 5: "Straight", 6: "Flush", 7: "Full House",
    8: "Four Kind", 9: "Straight Flush", 10: "Royal Flush",
}

ROW_NAMES = {0: "Discard", 1: "Top", 2: "Mid", 3: "Bot", 4: "Hand"}

# Paths
DB_PATH = Path(__file__).parent / "data" / "ofc.db"
SCRIPT_PATH = Path(__file__).parent.parent / "hook" / "pppoker_hook.js"

# OFC packet types we handle
OFC_PACKETS = {
    "PineHandCardBRC", "PineActionBRC", "PineResultBRC",
    "PineGameStartBRC", "PineSitDownBRC", "PineStandUpBRC",
    "PineRoomStatusBRC", "EnterRoomRSP",
}


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


def decode_cards(raw_list: list) -> str:
    """Decode a list of card ints to space-separated string."""
    return " ".join(decode_card(c) for c in raw_list if c > 0)


# ============ Database ============

def init_db(db_path: Path = DB_PATH):
    db_path.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(str(db_path))
    conn.execute("""
        CREATE TABLE IF NOT EXISTS ofc_hands (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            timestamp TEXT NOT NULL,
            table_id TEXT,
            game_id TEXT,
            num_players INTEGER NOT NULL,
            dealer_seat INTEGER,
            player_data TEXT NOT NULL,
            raw_json TEXT
        )
    """)
    conn.execute("""
        CREATE TABLE IF NOT EXISTS ofc_player_stats (
            player_id TEXT PRIMARY KEY,
            name TEXT,
            hands_played INTEGER DEFAULT 0,
            total_score INTEGER DEFAULT 0,
            fantasies INTEGER DEFAULT 0,
            busts INTEGER DEFAULT 0,
            last_seen TEXT
        )
    """)
    conn.execute("""
        CREATE TABLE IF NOT EXISTS ofc_packets (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            timestamp TEXT NOT NULL,
            packet_type TEXT NOT NULL,
            table_id INTEGER,
            data TEXT
        )
    """)
    conn.execute("CREATE INDEX IF NOT EXISTS idx_ofc_hands_ts ON ofc_hands(timestamp)")
    conn.execute("CREATE INDEX IF NOT EXISTS idx_ofc_packets_type ON ofc_packets(packet_type)")
    conn.commit()
    conn.close()


def save_packet(packet_type: str, table_id: int, data: dict, db_path: Path = DB_PATH):
    conn = sqlite3.connect(str(db_path))
    conn.execute(
        "INSERT INTO ofc_packets (timestamp, packet_type, table_id, data) VALUES (?, ?, ?, ?)",
        (datetime.now().isoformat(), packet_type, table_id, json.dumps(data, ensure_ascii=False))
    )
    conn.commit()
    conn.close()


# ============ Per-Table State ============

@dataclass
class OFCPlayer:
    uid: int = 0
    name: str = ""
    seat_id: int = -1
    chips: int = 0
    fantasy: int = 0
    # Current hand card state
    top: List[str] = field(default_factory=list)     # 3 cards
    mid: List[str] = field(default_factory=list)     # 5 cards
    bot: List[str] = field(default_factory=list)     # 5 cards
    discard: List[str] = field(default_factory=list)
    bust: bool = False
    # Scores
    head_score: int = 0
    mid_score: int = 0
    tail_score: int = 0
    head_type: int = 0
    mid_type: int = 0
    tail_type: int = 0
    total_profit: int = 0


@dataclass
class OFCTableState:
    table_id: int = 0
    game_id: str = ""
    dealer_seat: int = -1
    players: Dict[int, OFCPlayer] = field(default_factory=dict)  # seat_id -> player
    round_num: int = 0
    hand_complete: bool = False


class OFCCapture:
    def __init__(self, process_name="PPPoker.exe", verbose=True):
        self.process_name = process_name
        self.verbose = verbose
        self.session = None
        self.script = None
        self.running = False

        self.tables: Dict[int, OFCTableState] = {}
        self.hand_count = 0
        self.hands_saved = 0
        self.callbacks: List[callable] = []

    def add_callback(self, fn):
        """Register callback: fn(event_type: str, table_id: int, data: dict)."""
        self.callbacks.append(fn)

    def _notify(self, event_type: str, table_id: int, data: dict):
        """Notify all registered callbacks (called from Frida thread)."""
        for fn in self.callbacks:
            try:
                fn(event_type, table_id, data)
            except Exception as e:
                print(f"[Callback Error] {event_type}: {e}")

    def _get_table(self, table_id: int) -> OFCTableState:
        if table_id not in self.tables:
            self.tables[table_id] = OFCTableState(table_id=table_id)
        return self.tables[table_id]

    # ============ Frida Message Handler ============

    def on_message(self, message, data):
        if message["type"] == "send":
            payload = message["payload"]
            msg_type = payload.get("type", "")

            if msg_type == "info":
                print(f"[INFO] {payload['message']}")
            elif msg_type == "error":
                print(f"[ERROR] {payload['message']}")
            elif msg_type == "packet_type":
                name = payload['name']
                if self.verbose and name.startswith("Pine"):
                    print(f"  [{name}] table={payload['tableId']}")
            elif msg_type == "packet":
                self._handle_packet(payload)
        elif message["type"] == "error":
            print(f"[FRIDA ERROR] {message.get('description', message)}")

    # ============ Packet Dispatch ============

    def _handle_packet(self, payload):
        name = payload["name"]
        table_id = payload["tableId"]
        pkt = payload.get("data", {})

        if name not in OFC_PACKETS:
            return  # Not OFC related

        save_packet(name, table_id, pkt)

        handler = {
            "EnterRoomRSP": self._on_enter_room,
            "PineGameStartBRC": self._on_game_start,
            "PineHandCardBRC": self._on_hand_card,
            "PineActionBRC": self._on_action,
            "PineResultBRC": self._on_result,
            "PineSitDownBRC": self._on_sitdown,
            "PineStandUpBRC": self._on_standup,
            "PineRoomStatusBRC": self._on_room_status,
        }.get(name)

        if handler:
            handler(table_id, pkt)

    # ============ Packet Handlers ============

    def _on_enter_room(self, table_id: int, pkt: dict):
        room_info = pkt.get("roomInfo", {})
        game_mode = room_info.get("gameMode", 0)
        # OFC gameMode: check if it's a Pineapple room
        # gameMode values for OFC are not confirmed yet, log all EnterRoomRSP
        print(f"\n{'='*60}")
        print(f"[EnterRoom] table={table_id} gameMode={game_mode} "
              f"blind={room_info.get('blind', 0)} seats={room_info.get('seatNum', 0)}")

    def _on_game_start(self, table_id: int, pkt: dict):
        ts = self._get_table(table_id)
        ts.dealer_seat = pkt.get("dealerSeatId", -1)
        ts.game_id = pkt.get("gameId", "")
        ts.hand_complete = False
        ts.round_num = 0
        self.hand_count += 1

        # Reset player hands
        for p in ts.players.values():
            p.top = []
            p.mid = []
            p.bot = []
            p.discard = []
            p.bust = False
            p.head_score = p.mid_score = p.tail_score = 0
            p.total_profit = 0

        start_info = pkt.get("startInfo", [])
        for si in start_info:
            sid = si.get("seatId", -1)
            chips = si.get("chips", 0)
            if sid in ts.players:
                ts.players[sid].chips = chips

        action_type = pkt.get("actionType", 0)
        at_str = "Sequential" if action_type == 2 else "Concurrent" if action_type == 1 else str(action_type)

        print(f"\n{'='*60}")
        print(f"[OFC Start] #{self.hand_count} table={table_id} dealer=seat{ts.dealer_seat} "
              f"mode={at_str} players={len(start_info)}")

        self._notify("game_start", table_id, {
            "game_id": ts.game_id, "hand_num": self.hand_count,
        })

    def _on_hand_card(self, table_id: int, pkt: dict):
        ts = self._get_table(table_id)
        hand_cards = pkt.get("handCards", [])

        for hc in hand_cards:
            uid = hc.get("uid", 0)
            seat_id = hc.get("seatId", -1)
            cards_raw = hc.get("cards", [])
            round_num = hc.get("round", 0)
            fantasy = hc.get("fantasy", 0)

            cards_str = decode_cards(cards_raw)
            ts.round_num = round_num

            if seat_id not in ts.players:
                ts.players[seat_id] = OFCPlayer(uid=uid, seat_id=seat_id)
            ts.players[seat_id].uid = uid
            ts.players[seat_id].fantasy = fantasy

            fl_str = " [FANTASY]" if fantasy else ""
            print(f"  [Deal] R{round_num} Seat {seat_id} (uid={uid}): {cards_str}{fl_str}")

            self._notify("hand_card", table_id, {
                "uid": uid, "seat_id": seat_id, "round": round_num,
                "cards_raw": cards_raw, "fantasy": fantasy,
            })

    def _on_action(self, table_id: int, pkt: dict):
        ts = self._get_table(table_id)
        uid = pkt.get("uid", 0)
        seat_id = pkt.get("seatId", -1)
        head_raw = pkt.get("headCard", [])
        mid_raw = pkt.get("middleCard", [])
        tail_raw = pkt.get("tailCard", [])

        head_str = decode_cards(head_raw)
        mid_str = decode_cards(mid_raw)
        tail_str = decode_cards(tail_raw)

        # Update from PineCard if available
        card = pkt.get("card", {})
        if card:
            if seat_id not in ts.players:
                ts.players[seat_id] = OFCPlayer(uid=uid, seat_id=seat_id)
            p = ts.players[seat_id]
            p.top = [decode_card(c) for c in card.get("headCard", []) if c > 0]
            p.mid = [decode_card(c) for c in card.get("middleCard", []) if c > 0]
            p.bot = [decode_card(c) for c in card.get("tailCard", []) if c > 0]
            p.discard = [decode_card(c) for c in card.get("abandonCard", []) if c > 0]
            p.head_type = card.get("headType", 0)
            p.mid_type = card.get("middleType", 0)
            p.tail_type = card.get("tailType", 0)
            p.bust = card.get("bust", False)

        placed = []
        if head_str: placed.append(f"Top:{head_str}")
        if mid_str: placed.append(f"Mid:{mid_str}")
        if tail_str: placed.append(f"Bot:{tail_str}")

        print(f"  [Place] Seat {seat_id}: {' | '.join(placed) if placed else '(no new cards)'}")

        # Notify with raw card data from PineCard
        if card:
            self._notify("action", table_id, {
                "uid": uid, "seat_id": seat_id,
                "top_raw": card.get("headCard", []),
                "mid_raw": card.get("middleCard", []),
                "bot_raw": card.get("tailCard", []),
                "discard_raw": card.get("abandonCard", []),
            })

    def _on_result(self, table_id: int, pkt: dict):
        ts = self._get_table(table_id)
        results = pkt.get("playerResults", [])

        print(f"\n{'='*60}")
        print(f"[OFC Result] table={table_id}")

        player_data = []

        for pr in results:
            uid = pr.get("uid", 0)
            seat_id = pr.get("seatId", -1)
            name = pr.get("name", "")
            chips = pr.get("chips", 0)
            fantasy = pr.get("fantasy", 0)
            card = pr.get("card", {})
            scores = pr.get("scores", [])

            # Decode final card placement
            top = decode_cards(card.get("headCard", [])) if card else ""
            mid = decode_cards(card.get("middleCard", [])) if card else ""
            bot = decode_cards(card.get("tailCard", [])) if card else ""
            discarded = decode_cards(card.get("abandonCard", [])) if card else ""
            bust = card.get("bust", False) if card else False

            head_type = HAND_TYPES.get(card.get("headType", 0), "?") if card else "?"
            mid_type = HAND_TYPES.get(card.get("middleType", 0), "?") if card else "?"
            tail_type = HAND_TYPES.get(card.get("tailType", 0), "?") if card else "?"

            # Aggregate scores
            total_profit = 0
            score_details = []
            for s in scores:
                profit = s.get("profit", 0)
                total_profit += profit
                vs_seat = s.get("seatId", -1)
                hs = s.get("headScore", 0)
                ms = s.get("middleScore", 0)
                ts_score = s.get("tailScore", 0)
                aw = s.get("allwinScore", 0)
                score_details.append({
                    "vs": vs_seat, "head": hs, "mid": ms, "tail": ts_score,
                    "allwin": aw, "profit": profit
                })

            bust_str = " BUST!" if bust else ""
            fl_str = " FANTASY!" if fantasy else ""

            print(f"  Seat {seat_id} ({name} uid={uid}):{bust_str}{fl_str}")
            print(f"    Top: [{top}] {head_type}")
            print(f"    Mid: [{mid}] {mid_type}")
            print(f"    Bot: [{bot}] {tail_type}")
            if discarded:
                print(f"    Discard: [{discarded}]")
            print(f"    Profit: {total_profit:+d} chips")

            player_data.append({
                "uid": uid, "name": name, "seatId": seat_id,
                "chips": chips, "fantasy": fantasy, "bust": bust,
                "top": top, "mid": mid, "bot": bot, "discard": discarded,
                "headType": card.get("headType", 0) if card else 0,
                "midType": card.get("middleType", 0) if card else 0,
                "tailType": card.get("tailType", 0) if card else 0,
                "scores": score_details, "profit": total_profit,
            })

        # Notify with raw card data for all players
        result_players = []
        for pr in results:
            card_r = pr.get("card", {})
            result_players.append({
                "uid": pr.get("uid", 0),
                "seat_id": pr.get("seatId", -1),
                "name": pr.get("name", ""),
                "top_raw": card_r.get("headCard", []) if card_r else [],
                "mid_raw": card_r.get("middleCard", []) if card_r else [],
                "bot_raw": card_r.get("tailCard", []) if card_r else [],
                "discard_raw": card_r.get("abandonCard", []) if card_r else [],
                "bust": card_r.get("bust", False) if card_r else False,
                "profit": sum(s.get("profit", 0) for s in pr.get("scores", [])),
            })
        self._notify("result", table_id, {"players": result_players})

        # Save to DB
        ts.hand_complete = True
        self._save_hand(ts, player_data)

    def _on_sitdown(self, table_id: int, pkt: dict):
        ts = self._get_table(table_id)
        player = pkt.get("player", {})
        if not player:
            return
        uid = player.get("uid", 0)
        seat_id = player.get("seatId", -1)
        name = player.get("name", "")
        chips = player.get("chips", 0)

        ts.players[seat_id] = OFCPlayer(uid=uid, seat_id=seat_id, name=name, chips=chips)
        print(f"  [OFC SitDown] Seat {seat_id}: {name} (uid={uid}) chips={chips}")

    def _on_standup(self, table_id: int, pkt: dict):
        ts = self._get_table(table_id)
        seat_id = pkt.get("seatId", -1)
        if seat_id in ts.players:
            name = ts.players[seat_id].name
            del ts.players[seat_id]
            print(f"  [OFC StandUp] Seat {seat_id}: {name}")

    def _on_room_status(self, table_id: int, pkt: dict):
        ts = self._get_table(table_id)
        players = pkt.get("players", [])

        print(f"\n{'='*60}")
        print(f"[OFC RoomStatus] table={table_id} players={len(players)}")

        for ps in players:
            uid = ps.get("uid", 0)
            seat_id = ps.get("seatId", -1)
            name = ps.get("name", "")
            chips = ps.get("chips", 0)
            fantasy = ps.get("fantasy", 0)
            ready = ps.get("ready", False)

            ts.players[seat_id] = OFCPlayer(
                uid=uid, seat_id=seat_id, name=name, chips=chips, fantasy=fantasy
            )
            fl_str = " [FANTASY]" if fantasy else ""
            rd_str = " [READY]" if ready else ""
            print(f"  Seat {seat_id}: {name} (uid={uid}) chips={chips}{fl_str}{rd_str}")

    # ============ Hand Recording ============

    def _save_hand(self, ts: OFCTableState, player_data: list):
        now = datetime.now().isoformat()
        record = {
            "timestamp": now,
            "table_id": str(ts.table_id),
            "game_id": ts.game_id,
            "num_players": len(player_data),
            "dealer_seat": ts.dealer_seat,
            "player_data": json.dumps(player_data, ensure_ascii=False),
            "raw_json": json.dumps(player_data, ensure_ascii=False),
        }

        try:
            conn = sqlite3.connect(str(DB_PATH))
            cur = conn.execute("""
                INSERT INTO ofc_hands (timestamp, table_id, game_id, num_players,
                                       dealer_seat, player_data, raw_json)
                VALUES (?, ?, ?, ?, ?, ?, ?)
            """, (
                record["timestamp"], record["table_id"], record["game_id"],
                record["num_players"], record["dealer_seat"],
                record["player_data"], record["raw_json"],
            ))
            hand_id = cur.lastrowid

            # Update player stats
            for pd in player_data:
                uid = str(pd["uid"]) if pd["uid"] else ""
                if not uid:
                    continue
                name = pd.get("name", "")
                fantasy = 1 if pd.get("fantasy", 0) else 0
                bust = 1 if pd.get("bust", False) else 0
                profit = pd.get("profit", 0)
                # Score = sum of head/mid/tail vs each opponent
                total_score = sum(
                    s.get("head", 0) + s.get("mid", 0) + s.get("tail", 0) + s.get("allwin", 0)
                    for s in pd.get("scores", [])
                )

                conn.execute("""
                    INSERT INTO ofc_player_stats (player_id, name, hands_played,
                                                  total_score, fantasies, busts, last_seen)
                    VALUES (?, ?, 1, ?, ?, ?, ?)
                    ON CONFLICT(player_id) DO UPDATE SET
                        name = ?,
                        hands_played = hands_played + 1,
                        total_score = total_score + ?,
                        fantasies = fantasies + ?,
                        busts = busts + ?,
                        last_seen = ?
                """, (uid, name, total_score, fantasy, bust, now,
                      name, total_score, fantasy, bust, now))

            conn.commit()
            conn.close()
            self.hands_saved += 1
            print(f"  [DB] OFC hand #{self.hands_saved} saved (id={hand_id})")
        except Exception as e:
            print(f"  [DB Error] {e}")

    # ============ Connection ============

    def start(self):
        init_db()

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

        print(f"\nCapturing OFC packets...")
        print(f"DB: {DB_PATH}")
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
        print(f"\nStopped. OFC Hands: {self.hand_count} seen, {self.hands_saved} saved.")

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
    parser = argparse.ArgumentParser(description="PPPoker OFC (Pineapple) packet capture")
    parser.add_argument("--process", default="PPPoker.exe",
                        help="Process name to attach to")
    parser.add_argument("--quiet", action="store_true",
                        help="Less verbose output")
    args = parser.parse_args()

    capture = OFCCapture(
        process_name=args.process,
        verbose=not args.quiet,
    )
    capture.run()


if __name__ == "__main__":
    main()
