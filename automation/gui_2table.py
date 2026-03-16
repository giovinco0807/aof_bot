"""2-Table AoF Bot GUI — Dual GTO Advisor Display.

Shows GTO advisor for two tables side-by-side.
Each panel shows: Hero cards, position, push/fold decision, and seat table.

Launch: python gui_2table.py
"""

import ctypes
import os
import tkinter as tk
from tkinter import ttk, messagebox, scrolledtext
import threading
import sys
import json
import queue
from pathlib import Path
from datetime import datetime


def ensure_admin():
    """Re-launch as administrator if not already elevated."""
    if os.name != "nt":
        return
    try:
        if ctypes.windll.shell32.IsUserAnAdmin():
            return
    except Exception:
        return
    script = sys.argv[0]
    params = " ".join(f'"{a}"' for a in sys.argv[1:])
    ctypes.windll.shell32.ShellExecuteW(
        None, "runas", sys.executable, f'"{script}" {params}', None, 1
    )
    sys.exit(0)

sys.path.insert(0, str(Path(__file__).parent.parent / "hook"))
sys.path.insert(0, str(Path(__file__).parent))

CONFIG_PATH = Path(__file__).parent / "data" / "gui_config.json"

DEFAULT_GUI_CONFIG = {
    "hero_uid": 0,
    "process_name": "PPPoker.exe",
    "auto_play": False,
    "max_hands": 0,
    "stop_time": "",
    "leave_if_disadvantaged": False,
}


class LogRedirector:
    def __init__(self, log_queue: queue.Queue):
        self.queue = log_queue

    def write(self, text):
        if text.strip():
            self.queue.put(text)

    def flush(self):
        pass


class TablePanel:
    """A single GTO advisor panel for one table."""

    def __init__(self, parent, label: str):
        self.gto = None
        self.frame = ttk.LabelFrame(parent, text=label, padding=5)

        # --- Hero Info Row ---
        hero_row = ttk.Frame(self.frame)
        hero_row.pack(fill="x", pady=2)

        ttk.Label(hero_row, text="Cards:", font=("", 9)).pack(side="left")
        self.var_cards = tk.StringVar(value="??")
        self.lbl_cards = tk.Label(hero_row, textvariable=self.var_cards,
                                  font=("Consolas", 16, "bold"), fg="navy")
        self.lbl_cards.pack(side="left", padx=5)

        ttk.Label(hero_row, text="Pos:", font=("", 9)).pack(side="left", padx=(10, 0))
        self.var_pos = tk.StringVar(value="-")
        ttk.Label(hero_row, textvariable=self.var_pos,
                  font=("Consolas", 14, "bold")).pack(side="left", padx=3)

        ttk.Label(hero_row, text="NP:", font=("", 9)).pack(side="left", padx=(10, 0))
        self.var_np = tk.StringVar(value="-")
        ttk.Label(hero_row, textvariable=self.var_np,
                  font=("Consolas", 11)).pack(side="left", padx=3)

        # --- Decision Row ---
        dec_row = ttk.Frame(self.frame)
        dec_row.pack(fill="x", pady=2)

        ttk.Label(dec_row, text="Hand:", font=("", 9)).pack(side="left")
        self.var_hand = tk.StringVar(value="-")
        ttk.Label(dec_row, textvariable=self.var_hand,
                  font=("Consolas", 12, "bold")).pack(side="left", padx=5)

        ttk.Label(dec_row, text="Freq:", font=("", 9)).pack(side="left", padx=(10, 0))
        self.var_freq = tk.StringVar(value="-")
        ttk.Label(dec_row, textvariable=self.var_freq,
                  font=("Consolas", 12)).pack(side="left", padx=3)

        ttk.Label(dec_row, text="Prior:", font=("", 9)).pack(side="left", padx=(10, 0))
        self.var_prior = tk.StringVar(value="-")
        ttk.Label(dec_row, textvariable=self.var_prior,
                  font=("Consolas", 10)).pack(side="left", padx=3)

        # --- Big Decision Label ---
        self.var_decision = tk.StringVar(value="Waiting...")
        self.lbl_decision = tk.Label(self.frame, textvariable=self.var_decision,
                                      font=("Consolas", 28, "bold"), fg="gray",
                                      bg="#e0e0e0", relief="ridge", width=10)
        self.lbl_decision.pack(pady=5)

        # --- Seats Table ---
        cols = ("seat", "pos", "player", "action", "vpip")
        self.seats_tree = ttk.Treeview(self.frame, columns=cols, show="headings", height=4)
        self.seats_tree.heading("seat", text="S")
        self.seats_tree.heading("pos", text="Pos")
        self.seats_tree.heading("player", text="Player")
        self.seats_tree.heading("action", text="Action")
        self.seats_tree.heading("vpip", text="VPIP%")

        self.seats_tree.column("seat", width=30, anchor="center")
        self.seats_tree.column("pos", width=40, anchor="center")
        self.seats_tree.column("player", width=90, anchor="center")
        self.seats_tree.column("action", width=60, anchor="center")
        self.seats_tree.column("vpip", width=50, anchor="center")
        self.seats_tree.pack(fill="x")

        # Track table_id
        self.table_id = None

    def update(self, evt: dict, player_stats: dict):
        """Update this panel with hand_update event data."""
        self.table_id = evt.get("table_id")

        hero_cards = evt.get("hero_cards", "")
        if hero_cards and len(hero_cards) == 4:
            c1, c2 = hero_cards[0:2], hero_cards[2:4]
            self.var_cards.set(f"{c1} {c2}")

            try:
                from gto_lookup import GtoLookup, cards_to_hand_name
                if self.gto is None:
                    self.gto = GtoLookup()
                hand_name = cards_to_hand_name(c1, c2)
                self.var_hand.set(hand_name)
                np = evt.get("num_players", 0)
                pos = evt.get("hero_position", "")
                prior = evt.get("prior_actions", "")
                self.var_prior.set(f"'{prior}'" if prior else "(1st)")

                if np and pos:
                    freq = self.gto.get_push_freq(hand_name, np, pos, prior)
                    if freq >= 0:
                        self.var_freq.set(f"{freq*100:.0f}%")
                        if freq >= 0.5:
                            self.var_decision.set("PUSH")
                            self.lbl_decision.config(fg="white", bg="#2e7d32")
                        else:
                            self.var_decision.set("FOLD")
                            self.lbl_decision.config(fg="white", bg="#c62828")
                    else:
                        self.var_freq.set("N/A")
                        self.var_decision.set("???")
                        self.lbl_decision.config(fg="gray", bg="#e0e0e0")
            except Exception:
                self.var_hand.set("?")
        else:
            self.var_cards.set("??")
            self.var_decision.set("Waiting...")
            self.lbl_decision.config(fg="gray", bg="#e0e0e0")

        self.var_pos.set(evt.get("hero_position", "-") or "?")
        np = evt.get("num_players", 0)
        self.var_np.set(f"{np}P" if np else "-")

        # Update seats
        for item in self.seats_tree.get_children():
            self.seats_tree.delete(item)

        for s in evt.get("seats", []):
            uid = s.get("uid", "")
            name = s.get("name", "") or uid
            action = s.get("action", "")
            action_d = {"A": "ALL-IN", "F": "FOLD", "": "..."}.get(action, action)
            pos = s.get("position", "")
            marker = " ★" if s.get("is_hero") else ""
            stats = player_stats.get(uid, {})
            vpip = stats.get("vpip", "-")

            tag = "hero" if s.get("is_hero") else ""
            self.seats_tree.insert("", "end", values=(
                s.get("seat_id", ""), pos, name + marker, action_d, vpip
            ), tags=(tag,))
        self.seats_tree.tag_configure("hero", background="#e3f2fd")


class TwoTableGUI:
    def __init__(self):
        self.root = tk.Tk()
        self.root.title("AoF Bot — 2 Table Mode")
        self.root.geometry("900x700")
        self.root.resizable(True, True)
        self.root.protocol("WM_DELETE_WINDOW", self._on_closing)

        self.capture = None
        self.capture_thread = None
        self.running = False
        self.log_queue = queue.Queue()
        self.gui_data_queue = queue.Queue()
        self.config = self._load_config()

        # Table ID → panel index mapping (first two unique table_ids seen)
        self.table_map = {}

        self._build_ui()
        self._poll_log()
        self._poll_gui_data()

    def _load_config(self) -> dict:
        if CONFIG_PATH.exists():
            with open(CONFIG_PATH, "r") as f:
                saved = json.load(f)
                cfg = DEFAULT_GUI_CONFIG.copy()
                cfg.update(saved)
                return cfg
        return DEFAULT_GUI_CONFIG.copy()

    def _save_config(self):
        CONFIG_PATH.parent.mkdir(parents=True, exist_ok=True)
        cfg = {
            "hero_uid": self.var_hero_uid.get(),
            "process_name": self.var_process.get(),
            "auto_play": self.var_auto_play.get(),
            "max_hands": self.var_max_hands.get(),
            "stop_time": self.var_stop_time.get(),
        }
        with open(CONFIG_PATH, "w") as f:
            json.dump(cfg, f, indent=2)

    def _build_ui(self):
        # --- Settings Row ---
        settings = ttk.Frame(self.root, padding=5)
        settings.pack(fill="x")

        ttk.Label(settings, text="Process:").pack(side="left")
        self.var_process = tk.StringVar(value=self.config["process_name"])
        ttk.Entry(settings, textvariable=self.var_process, width=14).pack(side="left", padx=3)

        ttk.Label(settings, text="Hero UID:").pack(side="left", padx=(8, 0))
        self.var_hero_uid = tk.IntVar(value=self.config["hero_uid"])
        ttk.Entry(settings, textvariable=self.var_hero_uid, width=10).pack(side="left", padx=3)

        self.var_auto_play = tk.BooleanVar(value=self.config["auto_play"])
        ttk.Checkbutton(settings, text="Auto-Play", variable=self.var_auto_play).pack(side="left", padx=5)

        self.var_max_hands = tk.IntVar(value=self.config["max_hands"])
        self.var_stop_time = tk.StringVar(value=self.config["stop_time"])

        self.btn_start = ttk.Button(settings, text="START", command=self._on_start)
        self.btn_start.pack(side="left", padx=5)

        self.btn_stop = ttk.Button(settings, text="STOP", command=self._on_stop, state="disabled")
        self.btn_stop.pack(side="left", padx=3)

        self.var_status = tk.StringVar(value="Stopped")
        ttk.Label(settings, textvariable=self.var_status, font=("", 10, "bold")).pack(side="left", padx=10)

        self.var_stats = tk.StringVar(value="Hands: 0")
        ttk.Label(settings, textvariable=self.var_stats).pack(side="right")

        # --- Dual GTO Panels (side by side) ---
        panels_frame = ttk.Frame(self.root)
        panels_frame.pack(fill="both", expand=True, padx=5, pady=3)

        self.panel_left = TablePanel(panels_frame, "Table 1 (Left)")
        self.panel_left.frame.pack(side="left", fill="both", expand=True, padx=(0, 2))

        self.panel_right = TablePanel(panels_frame, "Table 2 (Right)")
        self.panel_right.frame.pack(side="right", fill="both", expand=True, padx=(2, 0))

        self.panels = [self.panel_left, self.panel_right]

        # --- Log ---
        log_frame = ttk.LabelFrame(self.root, text="Log", padding=3)
        log_frame.pack(fill="both", expand=True, padx=5, pady=3)

        self.log_text = scrolledtext.ScrolledText(log_frame, height=8, font=("Consolas", 8),
                                                   state="disabled", wrap="word")
        self.log_text.pack(fill="both", expand=True)

    def _poll_log(self):
        while not self.log_queue.empty():
            try:
                msg = self.log_queue.get_nowait()
                self.log_text.configure(state="normal")
                ts = datetime.now().strftime("%H:%M:%S")
                self.log_text.insert("end", f"[{ts}] {msg}\n")
                self.log_text.see("end")
                self.log_text.configure(state="disabled")
            except queue.Empty:
                break

        if self.capture:
            self.var_stats.set(f"Hands: {self.capture.hand_count} | "
                              f"Saved: {self.capture.hands_saved} | "
                              f"Hero: {self.capture.hero_hands_played}")

        self.root.after(100, self._poll_log)

    def _poll_gui_data(self):
        player_stats = None
        while not self.gui_data_queue.empty():
            try:
                evt = self.gui_data_queue.get_nowait()
                if evt.get("type") == "hand_update":
                    table_id = evt.get("table_id", 0)

                    # Map table_id to panel index (first 2 unique tables)
                    if table_id not in self.table_map:
                        if len(self.table_map) < 2:
                            idx = len(self.table_map)
                            self.table_map[table_id] = idx
                            panel_name = "Left" if idx == 0 else "Right"
                            self.panels[idx].frame.config(text=f"Table {idx+1} ({panel_name}) — ID: {table_id}")
                        else:
                            continue  # Ignore 3rd+ tables

                    panel_idx = self.table_map.get(table_id)
                    if panel_idx is not None and panel_idx < 2:
                        if player_stats is None:
                            player_stats = self._get_player_stats_cache()
                        self.panels[panel_idx].update(evt, player_stats)
            except queue.Empty:
                break
        self.root.after(100, self._poll_gui_data)

    def _get_player_stats_cache(self):
        import sqlite3
        db_path = Path(__file__).parent / "data" / "hands.db"
        if not db_path.exists():
            return {}
        try:
            conn = sqlite3.connect(str(db_path), timeout=2)
            rows = conn.execute("""
                SELECT player_id,
                       COUNT(*) as hands,
                       SUM(CASE WHEN action IN ('A','raise','call') THEN 1 ELSE 0 END) as pushed
                FROM hand_players
                GROUP BY player_id
            """).fetchall()
            conn.close()
            result = {}
            for pid, seen, pushed in rows:
                vpip = f"{pushed/seen*100:.0f}%" if seen > 0 else "-"
                result[str(pid)] = {"vpip": vpip, "hands": seen}
            return result
        except Exception:
            return {}

    def _on_start(self):
        if self.running:
            return
        self._save_config()
        self.table_map.clear()  # Reset table mapping

        hero_uid = self.var_hero_uid.get()
        if not hero_uid:
            messagebox.showwarning("Warning", "Hero UID is 0. Running in spectator mode.")

        self.var_status.set("Starting...")
        self.btn_start.configure(state="disabled")
        self.btn_stop.configure(state="normal")

        self.capture_thread = threading.Thread(target=self._run_capture, daemon=True)
        self.capture_thread.start()

    def _run_capture(self):
        from packet_capture import PacketCapture

        old_stdout = sys.stdout
        sys.stdout = LogRedirector(self.log_queue)

        try:
            self.capture = PacketCapture(
                process_name=self.var_process.get(),
                verbose=True,
                enable_solver=True,
                hero_uid=self.var_hero_uid.get(),
                auto_play=self.var_auto_play.get(),
                max_hands=self.var_max_hands.get(),
                stop_time=self.var_stop_time.get(),
                gui_queue=self.gui_data_queue,
            )
            self.running = True
            self.root.after(0, lambda: self.var_status.set("Running"))
            self.capture.start()

            import time
            while self.running and self.capture.running:
                time.sleep(0.1)
        except Exception as e:
            self.log_queue.put(f"ERROR: {e}")
        finally:
            sys.stdout = old_stdout
            self.running = False
            if self.capture:
                self.capture.stop()
            self.root.after(0, self._on_stopped)

    def _on_stop(self):
        if not self.running:
            return
        self.running = False
        if self.capture:
            self.capture.stop()

    def _on_stopped(self):
        self.var_status.set("Stopped")
        self.btn_start.configure(state="normal")
        self.btn_stop.configure(state="disabled")

    def _on_closing(self):
        self.running = False
        if self.capture:
            self.capture.stop()
        self.root.destroy()

    def run(self):
        self.root.mainloop()


if __name__ == "__main__":
    ensure_admin()
    app = TwoTableGUI()
    app.run()
