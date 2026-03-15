"""PPPoker OFC real-time overlay GUI.

Transparent overlay window showing opponent discards and remaining deck.
Captures packets via Frida (using OFCCapture) and displays in real-time.

Usage:
    python ofc_overlay.py
    python ofc_overlay.py --hero-uid 8723567
"""

import sys
import json
import queue
import threading
import time
import tkinter as tk
from tkinter import font as tkfont
from pathlib import Path
from datetime import datetime

sys.path.insert(0, str(Path(__file__).parent))

from ofc_capture import OFCCapture
from card_tracker import (
    CardTracker, RANKS_DISPLAY, SUITS_ORDER, FULL_DECK,
)

# ============ UI Constants ============

BG = "#1a1a2e"
BG_SECTION = "#16213e"
BG_HEADER = "#0f3460"
FG = "#e0e0e0"
FG_DIM = "#333344"
FG_ACCENT = "#e94560"

SUIT_COLORS = {
    "s": "#c0c0c0",  # spades - silver
    "h": "#ff4444",   # hearts - red
    "d": "#4499ff",   # diamonds - blue
    "c": "#44cc44",   # clubs - green
}
SUIT_ICONS = {"s": "\u2660", "h": "\u2665", "d": "\u2666", "c": "\u2663"}

CONFIG_PATH = Path(__file__).parent / "data" / "overlay_config.json"
DATA_LOG_PATH = Path(__file__).parent / "data" / "ofc_hands.jsonl"


# ============ Overlay GUI ============

class OFCOverlay:
    def __init__(self, hero_uid: int = 0):
        self.root = tk.Tk()
        self.event_queue = queue.Queue()
        self.tracker = CardTracker(hero_uid=hero_uid)
        self.capture = None
        self.capture_thread = None
        self._drag_data = {"x": 0, "y": 0}

        self._load_config()
        self._setup_window()
        self._build_ui()
        self._start_capture()
        self._poll_queue()

    # ---- Config persistence ----

    def _load_config(self):
        self._config = {"hero_uid": self.tracker.hero_uid, "x": 50, "y": 50}
        try:
            if CONFIG_PATH.exists():
                with open(CONFIG_PATH) as f:
                    saved = json.load(f)
                self._config.update(saved)
                if self._config["hero_uid"] and self.tracker.hero_uid == 0:
                    self.tracker.hero_uid = self._config["hero_uid"]
        except Exception:
            pass

    def _save_config(self):
        try:
            self._config["x"] = self.root.winfo_x()
            self._config["y"] = self.root.winfo_y()
            self._config["hero_uid"] = self.tracker.hero_uid
            CONFIG_PATH.parent.mkdir(parents=True, exist_ok=True)
            with open(CONFIG_PATH, "w") as f:
                json.dump(self._config, f)
        except Exception:
            pass

    # ---- Window setup ----

    def _setup_window(self):
        self.root.title("OFC Overlay")
        self.root.overrideredirect(True)
        self.root.wm_attributes("-topmost", True)
        self.root.wm_attributes("-alpha", 0.88)
        self.root.configure(bg=BG)

        x, y = self._config.get("x", 50), self._config.get("y", 50)
        self.root.geometry(f"400x480+{x}+{y}")

        self.root.protocol("WM_DELETE_WINDOW", self._on_close)

    def _on_close(self):
        self._save_config()
        if self.capture:
            self.capture.running = False
        self.root.destroy()

    # ---- Drag support ----

    def _start_drag(self, event):
        self._drag_data["x"] = event.x
        self._drag_data["y"] = event.y

    def _do_drag(self, event):
        dx = event.x - self._drag_data["x"]
        dy = event.y - self._drag_data["y"]
        x = self.root.winfo_x() + dx
        y = self.root.winfo_y() + dy
        self.root.geometry(f"+{x}+{y}")

    # ---- Build UI ----

    def _build_ui(self):
        # Fonts
        self.font_title = tkfont.Font(family="Consolas", size=11, weight="bold")
        self.font_normal = tkfont.Font(family="Consolas", size=10)
        self.font_card = tkfont.Font(family="Consolas", size=12, weight="bold")
        self.font_card_dim = tkfont.Font(family="Consolas", size=12)
        self.font_small = tkfont.Font(family="Consolas", size=9)
        self.font_suit = tkfont.Font(family="Segoe UI Symbol", size=13, weight="bold")

        # --- Header (draggable) ---
        header = tk.Frame(self.root, bg=BG_HEADER, padx=8, pady=4)
        header.pack(fill="x")
        header.bind("<Button-1>", self._start_drag)
        header.bind("<B1-Motion>", self._do_drag)

        lbl = tk.Label(header, text="OFC Overlay", font=self.font_title,
                       fg=FG_ACCENT, bg=BG_HEADER)
        lbl.pack(side="left")
        lbl.bind("<Button-1>", self._start_drag)
        lbl.bind("<B1-Motion>", self._do_drag)

        close_btn = tk.Label(header, text=" X ", font=self.font_title,
                             fg="#ff6666", bg=BG_HEADER, cursor="hand2")
        close_btn.pack(side="right")
        close_btn.bind("<Button-1>", lambda e: self._on_close())

        # --- Status line ---
        status_frame = tk.Frame(self.root, bg=BG, padx=8, pady=2)
        status_frame.pack(fill="x")
        self.lbl_status = tk.Label(status_frame, text="Hand: -  Round: -  Deck: 52",
                                   font=self.font_normal, fg=FG, bg=BG, anchor="w")
        self.lbl_status.pack(fill="x")

        # --- Opponent discards ---
        self._build_discard_section("相手の捨てカード", "opp")

        # --- Hero discards ---
        self._build_discard_section("自分の捨てカード", "hero")

        # --- Remaining deck ---
        deck_header = tk.Frame(self.root, bg=BG, padx=8, pady=2)
        deck_header.pack(fill="x")
        self.lbl_deck_title = tk.Label(deck_header, text="残りデッキ (52枚)",
                                       font=self.font_normal, fg=FG_ACCENT, bg=BG, anchor="w")
        self.lbl_deck_title.pack(fill="x")

        deck_frame = tk.Frame(self.root, bg=BG_SECTION, padx=8, pady=6)
        deck_frame.pack(fill="x", padx=6, pady=2)

        self.card_labels: Dict[str, tk.Label] = {}  # "As" -> Label

        for row, suit in enumerate(SUITS_ORDER):
            color = SUIT_COLORS[suit]
            icon = SUIT_ICONS[suit]

            suit_lbl = tk.Label(deck_frame, text=icon, fg=color, bg=BG_SECTION,
                                font=self.font_suit, width=2)
            suit_lbl.grid(row=row, column=0, padx=(0, 4), pady=1)

            for col, rank in enumerate(RANKS_DISPLAY):
                card_str = rank + suit
                lbl = tk.Label(deck_frame, text=rank, fg=color, bg=BG_SECTION,
                               font=self.font_card, width=2)
                lbl.grid(row=row, column=col + 1, padx=1, pady=1)
                self.card_labels[card_str] = lbl

        # Joker row
        joker_row = len(SUITS_ORDER)
        tk.Label(deck_frame, text="\u2605", fg="#ffcc00", bg=BG_SECTION,
                 font=self.font_suit, width=2).grid(row=joker_row, column=0, padx=(0, 4), pady=1)
        self.joker_labels = []
        for j in range(2):
            lbl = tk.Label(deck_frame, text="JK", fg="#ffcc00", bg=BG_SECTION,
                           font=self.font_card, width=2)
            lbl.grid(row=joker_row, column=j + 1, padx=1, pady=1)
            self.joker_labels.append(lbl)

        # --- Footer: UID + connection status ---
        footer = tk.Frame(self.root, bg=BG, padx=8, pady=4)
        footer.pack(fill="x", side="bottom")

        tk.Label(footer, text="UID:", font=self.font_small, fg="#888",
                 bg=BG).pack(side="left")
        self.uid_var = tk.StringVar(value=str(self.tracker.hero_uid) if self.tracker.hero_uid else "auto")
        uid_entry = tk.Entry(footer, textvariable=self.uid_var, font=self.font_small,
                             width=12, bg="#222", fg=FG, insertbackground=FG,
                             relief="flat", bd=2)
        uid_entry.pack(side="left", padx=4)
        uid_entry.bind("<Return>", self._on_uid_change)

        self.lbl_conn = tk.Label(footer, text="Connecting...", font=self.font_small,
                                 fg="#ffaa00", bg=BG)
        self.lbl_conn.pack(side="right")

    def _build_discard_section(self, title: str, prefix: str):
        """Build a discard display section (opponent or hero)."""
        frame = tk.Frame(self.root, bg=BG, padx=8, pady=2)
        frame.pack(fill="x")
        tk.Label(frame, text=title, font=self.font_normal, fg=FG_ACCENT,
                 bg=BG, anchor="w").pack(fill="x")

        row_frame = tk.Frame(self.root, bg=BG_SECTION, padx=8, pady=4)
        row_frame.pack(fill="x", padx=6, pady=2)

        labels = {}
        for i, r in enumerate(range(2, 6)):
            tk.Label(row_frame, text=f"R{r}:", font=self.font_small, fg="#888",
                     bg=BG_SECTION).grid(row=0, column=i*2, padx=(8 if i else 0, 2))
            lbl = tk.Label(row_frame, text="---", font=self.font_card, fg="#ffaa00",
                           bg=BG_SECTION, width=3)
            lbl.grid(row=0, column=i*2+1, padx=(0, 8))
            labels[r] = lbl

        setattr(self, f"{prefix}_discard_labels", labels)

    # ---- UID change ----

    def _on_uid_change(self, event=None):
        val = self.uid_var.get().strip()
        if val and val != "auto":
            try:
                self.tracker.hero_uid = int(val)
                self.tracker._hero_detected = True
            except ValueError:
                pass
        else:
            self.tracker.hero_uid = 0
            self.tracker._hero_detected = False

    # ---- Capture thread ----

    def _start_capture(self):
        def run():
            try:
                self.capture = OFCCapture(process_name="PPPoker.exe", verbose=False)
                self.capture.add_callback(self._capture_callback)
                self.capture.start()
                self.event_queue.put(("_connected", 0, {}))
                while self.capture.running:
                    time.sleep(0.1)
            except SystemExit:
                self.event_queue.put(("_error", 0, {"msg": "PPPoker not found"}))
            except Exception as e:
                self.event_queue.put(("_error", 0, {"msg": str(e)}))

        self.capture_thread = threading.Thread(target=run, daemon=True)
        self.capture_thread.start()

    def _capture_callback(self, event_type: str, table_id: int, data: dict):
        """Called from Frida thread - put into queue for main thread."""
        self.event_queue.put((event_type, table_id, data))

    # ---- Queue polling ----

    def _poll_queue(self):
        try:
            while True:
                event_type, table_id, data = self.event_queue.get_nowait()
                self._handle_event(event_type, table_id, data)
        except queue.Empty:
            pass
        self.root.after(100, self._poll_queue)

    def _handle_event(self, event_type: str, table_id: int, data: dict):
        if event_type == "_connected":
            self.lbl_conn.configure(text="Connected", fg="#44cc44")
            return
        if event_type == "_error":
            self.lbl_conn.configure(text=data.get("msg", "Error"), fg="#ff4444")
            return

        if event_type == "game_start":
            self.tracker.on_game_start(data)
            self._update_status()
            self._clear_discards()
            self._update_deck_display()

        elif event_type == "hand_card":
            self.tracker.on_hand_card(data)
            # Update UID display if auto-detected
            if self.tracker._hero_detected and self.uid_var.get() == "auto":
                self.uid_var.set(str(self.tracker.hero_uid))
            self._update_status()
            self._update_deck_display()

        elif event_type == "action":
            self.tracker.on_action(data)
            self._update_discards()
            self._update_status()
            self._update_deck_display()

        elif event_type == "result":
            self.tracker.on_result(data)
            self._update_discards()
            self._update_status()
            self._update_deck_display()
            self._save_hand_data(data)

    # ---- Data logging ----

    def _save_hand_data(self, result_data: dict):
        """Save hand data to JSONL file on hand completion."""
        try:
            record = self.tracker.export_hand()
            record["timestamp"] = datetime.now().isoformat()

            # Add profit info from result
            for p in result_data.get("players", []):
                uid = p.get("uid", 0)
                is_hero = (uid == self.tracker.hero_uid) if self.tracker.hero_uid else False
                if is_hero:
                    record["hero_profit"] = p.get("profit", 0)
                    record["hero_bust"] = p.get("bust", False)
                else:
                    record["opp_uid"] = uid
                    record["opp_name"] = p.get("name", "")
                    record["opp_profit"] = p.get("profit", 0)
                    record["opp_bust"] = p.get("bust", False)

            DATA_LOG_PATH.parent.mkdir(parents=True, exist_ok=True)
            with open(DATA_LOG_PATH, "a", encoding="utf-8") as f:
                f.write(json.dumps(record, ensure_ascii=False) + "\n")
        except Exception as e:
            print(f"[Data Log Error] {e}")

    # ---- UI updates ----

    def _update_status(self):
        hand = self.tracker.hand_num or "-"
        rnd = self.tracker.round_num or "-"
        rem = self.tracker.remaining_count()
        self.lbl_status.configure(text=f"Hand: {hand}  Round: {rnd}  Deck: {rem}")
        self.lbl_deck_title.configure(text=f"残りデッキ ({rem}枚)")

    def _clear_discards(self):
        for r in range(2, 6):
            self.opp_discard_labels[r].configure(text="---", fg="#555555")
            self.hero_discard_labels[r].configure(text="---", fg="#555555")

    def _update_discards(self):
        # Hero discards (real-time)
        for r in range(2, 6):
            card = self.tracker.hero_discard_by_round.get(r, "")
            if card:
                color = SUIT_COLORS.get(card[1], FG) if len(card) == 2 else FG
                self.hero_discard_labels[r].configure(text=card, fg=color)
            else:
                self.hero_discard_labels[r].configure(text="---", fg="#555555")

        # Opponent discards (only at result)
        for r in range(2, 6):
            card = self.tracker.opp_discard_by_round.get(r, "")
            if card:
                color = SUIT_COLORS.get(card[1], FG) if len(card) == 2 else FG
                self.opp_discard_labels[r].configure(text=card, fg=color)
            else:
                self.opp_discard_labels[r].configure(text="---", fg="#555555")

    def _update_deck_display(self):
        for card_str, lbl in self.card_labels.items():
            suit = card_str[1] if len(card_str) == 2 else ""
            if self.tracker.is_remaining(card_str):
                # Still in deck - bright
                lbl.configure(fg=SUIT_COLORS.get(suit, FG), font=self.font_card)
            else:
                # Used - dim
                lbl.configure(fg=FG_DIM, font=self.font_card_dim)

    # ---- Run ----

    def run(self):
        self.root.mainloop()


# ============ CLI ============

def main():
    import argparse
    parser = argparse.ArgumentParser(description="PPPoker OFC Overlay")
    parser.add_argument("--hero-uid", type=int, default=0,
                        help="Hero player UID (0 = auto-detect)")
    parser.add_argument("--process", default="PPPoker.exe",
                        help="Process name")
    args = parser.parse_args()

    overlay = OFCOverlay(hero_uid=args.hero_uid)
    overlay.run()


if __name__ == "__main__":
    main()
