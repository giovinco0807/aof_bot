"""Network Diagnostics Tool GUI."""

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
    # Re-launch with elevation
    script = sys.argv[0]
    params = " ".join(f'"{a}"' for a in sys.argv[1:])
    ctypes.windll.shell32.ShellExecuteW(
        None, "runas", sys.executable, f'"{script}" {params}', None, 1
    )
    sys.exit(0)

# Add hook directory to path
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
    "enable_exploit": True,
}


class LogRedirector:
    """Redirects print output to a queue for the GUI."""
    def __init__(self, log_queue: queue.Queue):
        self.queue = log_queue

    def write(self, text):
        if text.strip():
            self.queue.put(text)

    def flush(self):
        pass


class AofBotGUI:
    def __init__(self):
        self.root = tk.Tk()
        self.root.title("Network Diagnostics Tool")
        self.root.geometry("700x650")
        self.root.resizable(True, True)
        self.root.protocol("WM_DELETE_WINDOW", self._on_closing)

        self.capture = None
        self.capture_thread = None
        self.running = False
        self.monitoring = False
        self.monitor_thread = None
        self.log_queue = queue.Queue()
        self.gui_data_queue = queue.Queue()  # Structured hand data from capture
        self.config = self._load_config()
        self.gto = None  # Lazy-loaded GTO lookup

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
            "leave_if_disadvantaged": self.var_leave_disadvantaged.get(),
            "enable_exploit": self.var_enable_exploit.get(),
        }
        with open(CONFIG_PATH, "w") as f:
            json.dump(cfg, f, indent=2)

    def _build_ui(self):
        # --- Settings Frame ---
        settings_frame = ttk.LabelFrame(self.root, text="Configuration", padding=10)
        settings_frame.pack(fill="x", padx=10, pady=5)

        # Row 1: Process + Hero UID
        row1 = ttk.Frame(settings_frame)
        row1.pack(fill="x", pady=2)

        ttk.Label(row1, text="Process:").pack(side="left")
        self.var_process = tk.StringVar(value=self.config["process_name"])
        ttk.Entry(row1, textvariable=self.var_process, width=18).pack(side="left", padx=5)

        ttk.Label(row1, text="Hero UID:").pack(side="left", padx=(15, 0))
        self.var_hero_uid = tk.IntVar(value=self.config["hero_uid"])
        ttk.Entry(row1, textvariable=self.var_hero_uid, width=12).pack(side="left", padx=5)

        # Row 2: Auto-play + ADB
        row2 = ttk.Frame(settings_frame)
        row2.pack(fill="x", pady=2)

        self.var_auto_play = tk.BooleanVar(value=self.config["auto_play"])
        ttk.Checkbutton(row2, text="Auto-Play (ADB)", variable=self.var_auto_play).pack(side="left")

        self.var_enable_exploit = tk.BooleanVar(value=self.config.get("enable_exploit", True))
        ttk.Checkbutton(row2, text="Node-Lock Exploit", variable=self.var_enable_exploit).pack(side="left", padx=10)

        ttk.Button(row2, text="Resize Window", command=self._on_resize_window).pack(side="left", padx=(15, 5))
        ttk.Button(row2, text="Calibrate (1 Table)", command=self._on_calibrate).pack(side="left", padx=5)
        ttk.Button(row2, text="Calibrate (2 Tables)", command=self._on_calibrate_2tables).pack(side="left", padx=5)
        ttk.Button(row2, text="Screenshot", command=self._on_screenshot).pack(side="left", padx=5)

        # --- Auto-Exit Frame ---
        exit_frame = ttk.LabelFrame(self.root, text="Auto-Exit", padding=10)
        exit_frame.pack(fill="x", padx=10, pady=5)

        row3 = ttk.Frame(exit_frame)
        row3.pack(fill="x", pady=2)

        ttk.Label(row3, text="Max Hands:").pack(side="left")
        self.var_max_hands = tk.IntVar(value=self.config["max_hands"])
        ttk.Entry(row3, textvariable=self.var_max_hands, width=8).pack(side="left", padx=5)
        ttk.Label(row3, text="(0 = unlimited)").pack(side="left")

        row4 = ttk.Frame(exit_frame)
        row4.pack(fill="x", pady=2)

        ttk.Label(row4, text="Stop Time:").pack(side="left")
        self.var_stop_time = tk.StringVar(value=self.config["stop_time"])
        ttk.Entry(row4, textvariable=self.var_stop_time, width=8).pack(side="left", padx=5)
        ttk.Label(row4, text="(HH:MM, empty = no limit)").pack(side="left")

        row5 = ttk.Frame(exit_frame)
        row5.pack(fill="x", pady=2)

        self.var_leave_disadvantaged = tk.BooleanVar(value=self.config["leave_if_disadvantaged"])
        ttk.Checkbutton(row5, text="Leave if disadvantaged (>20BB down after 30 hands)",
                        variable=self.var_leave_disadvantaged).pack(side="left")

        # --- Delay Tuning Frame ---
        delay_frame = ttk.LabelFrame(self.root, text="Delay Tuning", padding=10)
        delay_frame.pack(fill="x", padx=10, pady=5)

        # Load current delay values from pc_config
        pc_cfg_path = Path(__file__).parent / "data" / "pc_config.json"
        pc_cfg = {}
        if pc_cfg_path.exists():
            with open(pc_cfg_path, "r") as f:
                pc_cfg = json.load(f)

        delay_row1 = ttk.Frame(delay_frame)
        delay_row1.pack(fill="x", pady=1)

        ttk.Label(delay_row1, text="Min Delay (s):").pack(side="left")
        self.var_delay_floor = tk.DoubleVar(value=pc_cfg.get("delay_floor", 0.4))
        tk.Scale(delay_row1, variable=self.var_delay_floor, from_=0.1, to=2.0,
                 resolution=0.1, orient="horizontal", length=150,
                 command=lambda v: self._update_delay_config()).pack(side="left", padx=5)

        ttk.Label(delay_row1, text="Max Delay (s):").pack(side="left", padx=(10, 0))
        self.var_delay_cap = tk.DoubleVar(value=pc_cfg.get("delay_cap", 6.0))
        tk.Scale(delay_row1, variable=self.var_delay_cap, from_=1.0, to=10.0,
                 resolution=0.5, orient="horizontal", length=150,
                 command=lambda v: self._update_delay_config()).pack(side="left", padx=5)

        ttk.Label(delay_row1, text="Hesitation %:").pack(side="left", padx=(10, 0))
        self.var_hesitation = tk.DoubleVar(value=pc_cfg.get("hesitation_prob", 0.15) * 100)
        tk.Scale(delay_row1, variable=self.var_hesitation, from_=0, to=50,
                 resolution=5, orient="horizontal", length=120,
                 command=lambda v: self._update_delay_config()).pack(side="left", padx=5)

        ttk.Label(delay_row1, text="Think (s):").pack(side="left", padx=(10, 0))
        self.var_think_time = tk.DoubleVar(value=pc_cfg.get("min_think_time", 1.0))
        tk.Scale(delay_row1, variable=self.var_think_time, from_=0.0, to=3.0,
                 resolution=0.5, orient="horizontal", length=120,
                 command=lambda v: self._update_delay_config()).pack(side="left", padx=5)

        # --- Control Buttons ---
        ctrl_frame = ttk.Frame(self.root, padding=5)
        ctrl_frame.pack(fill="x", padx=10)

        self.btn_start = ttk.Button(ctrl_frame, text="START", command=self._on_start,
                                     style="Accent.TButton")
        self.btn_start.pack(side="left", padx=5)

        self.btn_stop = ttk.Button(ctrl_frame, text="STOP", command=self._on_stop,
                                    state="disabled")
        self.btn_stop.pack(side="left", padx=5)

        self.btn_restart = ttk.Button(ctrl_frame, text="RESTART", command=self._on_restart,
                                      state="disabled")
        self.btn_restart.pack(side="left", padx=5)

        ttk.Separator(ctrl_frame, orient="vertical").pack(side="left", fill="y", padx=8)

        self.btn_monitor = ttk.Button(ctrl_frame, text="MONITOR", command=self._on_monitor)
        self.btn_monitor.pack(side="left", padx=5)

        # Status
        self.var_status = tk.StringVar(value="Stopped")
        self.lbl_status = ttk.Label(ctrl_frame, textvariable=self.var_status,
                                     font=("", 10, "bold"))
        self.lbl_status.pack(side="left", padx=15)

        # Stats
        self.var_stats = tk.StringVar(value="Hands: 0 | Saved: 0")
        ttk.Label(ctrl_frame, textvariable=self.var_stats).pack(side="right")

        # --- Tabbed area (Log + Player Stats) ---
        notebook = ttk.Notebook(self.root)
        notebook.pack(fill="both", expand=True, padx=10, pady=5)

        # Tab 1: Log
        log_frame = ttk.Frame(notebook, padding=5)
        notebook.add(log_frame, text="Log")

        self.log_text = scrolledtext.ScrolledText(log_frame, height=15, font=("Consolas", 9),
                                                   state="disabled", wrap="word")
        self.log_text.pack(fill="both", expand=True)
        ttk.Button(log_frame, text="Clear", command=self._clear_log).pack(anchor="e", pady=2)

        # Tab 2: Player Stats
        stats_frame = ttk.Frame(notebook, padding=5)
        notebook.add(stats_frame, text="Player Stats")

        cols = ("player_id", "hands", "push%", "profit_chips", "profit_bb", "bb_per_100", "last_seen")
        self.stats_tree = ttk.Treeview(stats_frame, columns=cols, show="headings", height=12)
        self.stats_tree.heading("player_id", text="Player ID")
        self.stats_tree.heading("hands", text="Hands")
        self.stats_tree.heading("push%", text="Push%")
        self.stats_tree.heading("profit_chips", text="Profit")
        self.stats_tree.heading("profit_bb", text="BB")
        self.stats_tree.heading("bb_per_100", text="BB/100")
        self.stats_tree.heading("last_seen", text="Last Seen")

        self.stats_tree.column("player_id", width=100)
        self.stats_tree.column("hands", width=60, anchor="center")
        self.stats_tree.column("push%", width=60, anchor="center")
        self.stats_tree.column("profit_chips", width=80, anchor="e")
        self.stats_tree.column("profit_bb", width=70, anchor="e")
        self.stats_tree.column("bb_per_100", width=70, anchor="e")
        self.stats_tree.column("last_seen", width=130)

        scrollbar = ttk.Scrollbar(stats_frame, orient="vertical", command=self.stats_tree.yview)
        self.stats_tree.configure(yscrollcommand=scrollbar.set)
        self.stats_tree.pack(side="left", fill="both", expand=True)
        scrollbar.pack(side="right", fill="y")

        btn_frame = ttk.Frame(stats_frame)
        btn_frame.pack(fill="x", pady=2)
        ttk.Button(stats_frame, text="Refresh", command=self._refresh_stats).pack(anchor="e", pady=2)

        # Tab 3: GTO Advisor
        gto_frame = ttk.Frame(notebook, padding=5)
        notebook.add(gto_frame, text="GTO Advisor")
        self._build_gto_tab(gto_frame)

    def _build_gto_tab(self, parent):
        # --- Hero Info ---
        hero_frame = ttk.LabelFrame(parent, text="Hero", padding=8)
        hero_frame.pack(fill="x", pady=3)

        hero_row = ttk.Frame(hero_frame)
        hero_row.pack(fill="x")

        ttk.Label(hero_row, text="Hand #").pack(side="left")
        self.var_hand_num = tk.StringVar(value="-")
        ttk.Label(hero_row, textvariable=self.var_hand_num, font=("Consolas", 11)).pack(side="left", padx=5)

        ttk.Label(hero_row, text="NP:").pack(side="left", padx=(15, 0))
        self.var_np = tk.StringVar(value="-")
        ttk.Label(hero_row, textvariable=self.var_np, font=("Consolas", 11)).pack(side="left", padx=5)

        ttk.Label(hero_row, text="Position:").pack(side="left", padx=(15, 0))
        self.var_hero_pos = tk.StringVar(value="-")
        ttk.Label(hero_row, textvariable=self.var_hero_pos, font=("Consolas", 14, "bold")).pack(side="left", padx=5)

        ttk.Label(hero_row, text="Cards:").pack(side="left", padx=(15, 0))
        self.var_hero_cards = tk.StringVar(value="??")
        self.lbl_hero_cards = ttk.Label(hero_row, textvariable=self.var_hero_cards,
                                         font=("Consolas", 14, "bold"), foreground="navy")
        self.lbl_hero_cards.pack(side="left", padx=5)

        # --- GTO Decision ---
        decision_frame = ttk.LabelFrame(parent, text="GTO Decision", padding=8)
        decision_frame.pack(fill="x", pady=3)

        dec_row = ttk.Frame(decision_frame)
        dec_row.pack(fill="x")

        ttk.Label(dec_row, text="Prior:").pack(side="left")
        self.var_prior = tk.StringVar(value="-")
        ttk.Label(dec_row, textvariable=self.var_prior, font=("Consolas", 12)).pack(side="left", padx=5)

        ttk.Label(dec_row, text="Hand:").pack(side="left", padx=(15, 0))
        self.var_hand_name = tk.StringVar(value="-")
        ttk.Label(dec_row, textvariable=self.var_hand_name, font=("Consolas", 12, "bold")).pack(side="left", padx=5)

        ttk.Label(dec_row, text="Freq:").pack(side="left", padx=(15, 0))
        self.var_gto_freq = tk.StringVar(value="-")
        ttk.Label(dec_row, textvariable=self.var_gto_freq, font=("Consolas", 12)).pack(side="left", padx=5)

        self.var_gto_decision = tk.StringVar(value="Waiting...")
        self.lbl_decision = tk.Label(decision_frame, textvariable=self.var_gto_decision,
                                      font=("Consolas", 24, "bold"), fg="gray",
                                      bg="#e0e0e0", relief="ridge", width=12)
        self.lbl_decision.pack(pady=5)

        # --- Table Seats ---
        seats_frame = ttk.LabelFrame(parent, text="Table", padding=5)
        seats_frame.pack(fill="x", pady=3)

        cols = ("seat", "position", "uid", "action", "vpip", "hands")
        self.seats_tree = ttk.Treeview(seats_frame, columns=cols, show="headings", height=4)
        self.seats_tree.heading("seat", text="Seat")
        self.seats_tree.heading("position", text="Pos")
        self.seats_tree.heading("uid", text="Player")
        self.seats_tree.heading("action", text="Action")
        self.seats_tree.heading("vpip", text="VPIP%")
        self.seats_tree.heading("hands", text="Hands")

        self.seats_tree.column("seat", width=40, anchor="center")
        self.seats_tree.column("position", width=50, anchor="center")
        self.seats_tree.column("uid", width=100, anchor="center")
        self.seats_tree.column("action", width=60, anchor="center")
        self.seats_tree.column("vpip", width=60, anchor="center")
        self.seats_tree.column("hands", width=60, anchor="center")
        self.seats_tree.pack(fill="x")

    def _log(self, msg: str):
        self.log_text.configure(state="normal")
        timestamp = datetime.now().strftime("%H:%M:%S")
        self.log_text.insert("end", f"[{timestamp}] {msg}\n")
        self.log_text.see("end")
        self.log_text.configure(state="disabled")

    def _clear_log(self):
        self.log_text.configure(state="normal")
        self.log_text.delete("1.0", "end")
        self.log_text.configure(state="disabled")

    def _poll_log(self):
        """Poll the log queue and display messages."""
        while not self.log_queue.empty():
            try:
                msg = self.log_queue.get_nowait()
                self._log(msg)
            except queue.Empty:
                break

        # Update stats
        if self.capture:
            ofc = getattr(self.capture, 'ofc_hands_saved', 0)
            ofc_str = f" | OFC: {ofc}" if ofc else ""
            prof = getattr(self.capture, 'session_profit', 0)
            sign = "+" if prof >= 0 else ""
            ev = getattr(self.capture, 'session_ev', 0)
            ev_sign = "+" if ev >= 0 else ""
            ev_str = f" | EV:{ev_sign}{ev:.0f}" if ev != 0 else ""
            self.var_stats.set(f"Hands: {self.capture.hand_count} | "
                              f"Saved: {self.capture.hands_saved}{ofc_str} | "
                              f"Hero: {self.capture.hero_hands_played} | "
                              f"P/L: {sign}{prof}{ev_str}")

            # Auto-refresh player stats every 30 seconds
            if not hasattr(self, '_stats_counter'):
                self._stats_counter = 0
            self._stats_counter += 1
            if self._stats_counter % 300 == 0:  # 300 * 100ms = 30sec
                self._refresh_stats()

        self.root.after(100, self._poll_log)

    def _poll_gui_data(self):
        """Poll GUI data queue for hand updates."""
        while not self.gui_data_queue.empty():
            try:
                evt = self.gui_data_queue.get_nowait()
                if evt.get("type") == "hand_update":
                    self._update_gto_display(evt)
            except queue.Empty:
                break
        self.root.after(100, self._poll_gui_data)

    def _update_gto_display(self, evt):
        """Update GTO Advisor tab with hand data."""
        self.var_hand_num.set(str(evt.get("hand_num", "-")))
        self.var_np.set(str(evt.get("num_players", "-")) + "P")
        self.var_hero_pos.set(evt.get("hero_position", "-") or "?")

        hero_cards = evt.get("hero_cards", "")
        if hero_cards and len(hero_cards) == 4:
            c1 = hero_cards[0:2]
            c2 = hero_cards[2:4]
            self.var_hero_cards.set(f"{c1} {c2}")

            # GTO lookup
            try:
                from gto_lookup import GtoLookup, cards_to_hand_name
                if self.gto is None:
                    self.gto = GtoLookup()
                hand_name = cards_to_hand_name(c1, c2)
                self.var_hand_name.set(hand_name)
                np = evt.get("num_players", 0)
                pos = evt.get("hero_position", "")
                prior = evt.get("prior_actions", "")
                self.var_prior.set(f"'{prior}'" if prior else "(first)")

                if np and pos:
                    freq = self.gto.get_push_freq(hand_name, np, pos, prior)
                    if freq >= 0:
                        self.var_gto_freq.set(f"{freq*100:.0f}%")
                        if freq >= 0.5:
                            self.var_gto_decision.set("PUSH")
                            self.lbl_decision.config(fg="white", bg="#2e7d32")
                        else:
                            self.var_gto_decision.set("FOLD")
                            self.lbl_decision.config(fg="white", bg="#c62828")
                    else:
                        self.var_gto_freq.set("N/A")
                        self.var_gto_decision.set("???")
                        self.lbl_decision.config(fg="gray", bg="#e0e0e0")
            except Exception as e:
                self.var_hand_name.set("?")
                self.var_gto_freq.set(f"err")
        else:
            self.var_hero_cards.set("??")
            self.var_gto_decision.set("Waiting...")
            self.lbl_decision.config(fg="gray", bg="#e0e0e0")

        # Update seats table
        for item in self.seats_tree.get_children():
            self.seats_tree.delete(item)

        seats = evt.get("seats", [])
        player_stats = self._get_player_stats_cache()
        for s in seats:
            uid = s.get("uid", "")
            name = s.get("name", "") or uid
            action = s.get("action", "")
            action_display = {"A": "ALL-IN", "F": "FOLD", "": "..."}.get(action, action)
            pos = s.get("position", "")
            marker = " ★" if s.get("is_hero") else ""
            # Player stats
            stats = player_stats.get(uid, {})
            vpip = stats.get("vpip", "-")
            hands = stats.get("hands", "-")

            tag = "hero" if s.get("is_hero") else ""
            self.seats_tree.insert("", "end", values=(
                s.get("seat_id", ""),
                pos,
                name + marker,
                action_display,
                vpip,
                hands,
            ), tags=(tag,))
        self.seats_tree.tag_configure("hero", background="#e3f2fd")

    def _get_player_stats_cache(self):
        """Get player stats from hand_players (excludes old data)."""
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

        hero_uid = self.var_hero_uid.get()
        if not hero_uid:
            messagebox.showwarning("Warning", "Hero UID is 0. Running in spectator mode.")

        self._log("Starting capture...")
        self.var_status.set("Starting...")
        self.btn_start.configure(state="disabled")
        self.btn_stop.configure(state="normal")
        self.btn_restart.configure(state="normal")
        self.btn_monitor.configure(state="disabled")

        self.capture_thread = threading.Thread(target=self._run_capture, daemon=True)
        self.capture_thread.start()

    def _run_capture(self):
        from packet_capture import PacketCapture

        # Redirect stdout to our log queue
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
                leave_if_disadvantaged=self.var_leave_disadvantaged.get(),
                gui_queue=self.gui_data_queue,
                enable_exploit=self.var_enable_exploit.get(),
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

    def _update_delay_config(self):
        """Save delay tuning values to pc_config.json and update live PcController."""
        pc_cfg_path = Path(__file__).parent / "data" / "pc_config.json"
        try:
            pc_cfg = {}
            if pc_cfg_path.exists():
                with open(pc_cfg_path, "r") as f:
                    pc_cfg = json.load(f)
            pc_cfg["delay_floor"] = self.var_delay_floor.get()
            pc_cfg["delay_cap"] = self.var_delay_cap.get()
            pc_cfg["hesitation_prob"] = self.var_hesitation.get() / 100.0
            pc_cfg["min_think_time"] = self.var_think_time.get()
            with open(pc_cfg_path, "w") as f:
                json.dump(pc_cfg, f, indent=2)

            # Update live PcController config if capture is running
            if self.capture and hasattr(self.capture, 'adb') and self.capture.adb:
                self.capture.adb.config["delay_floor"] = pc_cfg["delay_floor"]
                self.capture.adb.config["delay_cap"] = pc_cfg["delay_cap"]
                self.capture.adb.config["hesitation_prob"] = pc_cfg["hesitation_prob"]
                self.capture.adb.config["min_think_time"] = pc_cfg["min_think_time"]
        except Exception:
            pass

    def _on_stop(self):
        if not self.running:
            return
        self._log("Stopping...")
        self.var_status.set("Stopping...")
        self.running = False
        if self.capture:
            self.capture.running = False

    def _on_stopped(self):
        self.var_status.set("Stopped")
        self.btn_start.configure(state="normal")
        self.btn_stop.configure(state="disabled")
        self.btn_restart.configure(state="disabled")
        self.btn_monitor.configure(state="normal")
        self._log("Stopped.")

        # Auto-restart if pending
        if getattr(self, '_restart_pending', False):
            self._restart_pending = False
            self.root.after(500, self._on_start)

    def _on_restart(self):
        """Stop current capture and auto-start again."""
        if not self.running:
            return
        self._log("Restarting...")
        self.var_status.set("Restarting...")
        self._restart_pending = True
        self.running = False
        if self.capture:
            self.capture.running = False

    def _on_monitor(self):
        """Toggle monitoring mode - attach to PPPoker and watch club rooms."""
        if self.monitoring:
            # Stop monitoring
            self.monitoring = False
            self._log("Stopping monitor...")
            self.var_status.set("Stopping...")
            if self.capture:
                self.capture.running = False
            return

        if self.running:
            return

        self._save_config()
        self._log("Starting monitor (auto-enter when 2+ players)...")
        self.var_status.set("Monitoring...")
        self.btn_start.configure(state="disabled")
        self.btn_stop.configure(state="disabled")
        self.btn_monitor.configure(text="STOP MONITOR")
        self.monitoring = True

        self.monitor_thread = threading.Thread(target=self._run_monitor, daemon=True)
        self.monitor_thread.start()

    def _run_monitor(self):
        """Run capture in monitor-only mode (no auto-play, just watch rooms)."""
        from packet_capture import PacketCapture

        old_stdout = sys.stdout
        sys.stdout = LogRedirector(self.log_queue)

        try:
            self.capture = PacketCapture(
                process_name=self.var_process.get(),
                verbose=True,
                enable_solver=False,
                hero_uid=self.var_hero_uid.get(),
                auto_play=False,
                gui_queue=self.gui_data_queue,
            )
            self.running = True
            self.root.after(0, lambda: self.var_status.set("Monitoring"))
            self.capture.start()

            import time
            while self.monitoring and self.capture.running:
                time.sleep(0.1)

        except Exception as e:
            self.log_queue.put(f"ERROR: {e}")
        finally:
            sys.stdout = old_stdout
            self.running = False
            self.monitoring = False
            if self.capture:
                self.capture.stop()
            self.root.after(0, self._on_monitor_stopped)

    def _on_monitor_stopped(self):
        self.var_status.set("Stopped")
        self.btn_start.configure(state="normal")
        self.btn_stop.configure(state="disabled")
        self.btn_monitor.configure(text="MONITOR", state="normal")
        self._log("Monitor stopped.")

    def _refresh_stats(self):
        """Load player stats from DB (hand_players only, excludes old data)."""
        import sqlite3
        db_path = Path(__file__).parent / "data" / "hands.db"
        if not db_path.exists():
            return

        # Clear existing
        for item in self.stats_tree.get_children():
            self.stats_tree.delete(item)

        try:
            conn = sqlite3.connect(str(db_path))
            rows = conn.execute("""
                SELECT hp.player_id,
                       COUNT(*) as hands,
                       SUM(CASE WHEN hp.action IN ('A','raise','call') THEN 1 ELSE 0 END) as pushed,
                       SUM(hp.profit_chips) as profit_c,
                       SUM(hp.profit_bb) as profit_bb,
                       MAX(h.timestamp) as last_seen
                FROM hand_players hp
                JOIN hands h ON hp.hand_id = h.id
                GROUP BY hp.player_id
                ORDER BY hands DESC
            """).fetchall()
            conn.close()

            for row in rows:
                pid, hands, pushed, profit_c, profit_bb, last_seen = row
                push_pct = f"{pushed/hands*100:.0f}%" if hands > 0 else "-"
                bb_per_100 = f"{profit_bb/hands*100:.1f}" if hands and profit_bb else "-"
                profit_c = (profit_c or 0) / 100.0
                profit_bb = profit_bb or 0
                sign_c = "+" if profit_c >= 0 else ""
                sign_b = "+" if profit_bb >= 0 else ""
                last = last_seen[:16] if last_seen else "-"
                self.stats_tree.insert("", "end", values=(
                    pid, hands, push_pct,
                    f"{sign_c}{profit_c:.2f}", f"{sign_b}{profit_bb:.1f}",
                    bb_per_100, last
                ))
        except Exception as e:
            self._log(f"Stats error: {e}")

    def _on_resize_window(self):
        """Force the PPPoker window to the standard 1064x970 size."""
        try:
            from pc_input import PcController
            ctrl = PcController()
            if ctrl.resize_window(1064, 970):
                self._log("Window resized to standard 1064x970.")
                from tkinter import messagebox
                messagebox.showinfo("Success", "PPPoker window resized to 1064x970.")
            else:
                self._log("Failed to resize window. Is PPPoker running?")
                from tkinter import messagebox
                messagebox.showerror("Error", "Could not find PPPoker window to resize.")
        except Exception as e:
            self._log(f"Resize error: {e}")
            from tkinter import messagebox
            messagebox.showerror("Error", f"Failed to resize window: {e}")

    def _on_calibrate(self):
        """Interactive screen overlay to pick Fold and All-in coordinates."""
        try:
            from pc_input import PcController
            ctrl = PcController()
            win_rect = ctrl._get_window_rect()
            if not win_rect:
                messagebox.showerror("Error", "PPPoker window not found")
                return

            wx, wy, ww, wh = win_rect

            overlay = tk.Toplevel(self.root)
            overlay.attributes("-alpha", 0.5)
            overlay.attributes("-topmost", True)
            overlay.overrideredirect(True)
            overlay.geometry(f"{ww}x{wh}+{wx}+{wy}")
            overlay.configure(bg="black")

            # Make it slightly transparent and clickable
            lbl = tk.Label(overlay, text="Click the center of the FOLD button", 
                           fg="white", bg="black", font=("Arial", 20, "bold"))
            lbl.pack(expand=True)

            clicks = []
            def on_click(event):
                # The coordinates relative to the PPPoker window
                clicks.append({"x": event.x, "y": event.y})
                if len(clicks) == 1:
                    lbl.configure(text="Click the center of the ALL IN button")
                elif len(clicks) == 2:
                    overlay.destroy()

            overlay.bind("<Button-1>", on_click)
            self.root.wait_window(overlay)

            if len(clicks) == 2:
                # Save the new absolute coordinates
                cfg = ctrl.config
                cfg["buttons"]["fold"]["x"] = clicks[0]["x"]
                cfg["buttons"]["fold"]["y"] = clicks[0]["y"]
                cfg["buttons"]["allin"]["x"] = clicks[1]["x"]
                cfg["buttons"]["allin"]["y"] = clicks[1]["y"]
                from pc_input import CONFIG_PATH
                with open(CONFIG_PATH, "w") as f:
                    import json
                    json.dump(cfg, f, indent=2)

                self._log(f"Calibration saved! Fold: {clicks[0]['x']},{clicks[0]['y']} | AllIn: {clicks[1]['x']},{clicks[1]['y']}")
                messagebox.showinfo("Success", "Calibration saved! You can now START auto-play.")
            else:
                self._log("Calibration cancelled.")

        except Exception as e:
            self._log(f"Calibration error: {e}")
            from tkinter import messagebox
            messagebox.showerror("Error", str(e))

    def _on_calibrate_2tables(self):
        """Interactive screen overlay to pick Fold and All-in coordinates for 2 tables."""
        from tkinter import messagebox
        try:
            from pc_input import PcController
            ctrl = PcController()
            win_rect = ctrl._get_window_rect()
            if not win_rect:
                messagebox.showerror("Error", "PPPoker window not found")
                return

            wx, wy, ww, wh = win_rect

            overlay = tk.Toplevel(self.root)
            overlay.attributes("-alpha", 0.5)
            overlay.attributes("-topmost", True)
            overlay.overrideredirect(True)
            overlay.geometry(f"{ww}x{wh}+{wx}+{wy}")
            overlay.configure(bg="black")

            lbl = tk.Label(overlay, text="Click FOLD on the LEFT table", 
                           fg="white", bg="black", font=("Arial", 20, "bold"))
            lbl.pack(expand=True)

            clicks = []
            def on_click(event):
                clicks.append({"x": event.x, "y": event.y})
                if len(clicks) == 1:
                    lbl.configure(text="Click ALL IN on the LEFT table")
                elif len(clicks) == 2:
                    lbl.configure(text="Click FOLD on the RIGHT table")
                elif len(clicks) == 3:
                    lbl.configure(text="Click ALL IN on the RIGHT table")
                elif len(clicks) == 4:
                    overlay.destroy()

            overlay.bind("<Button-1>", on_click)
            self.root.wait_window(overlay)

            if len(clicks) == 4:
                cfg = ctrl.config
                if "buttons" not in cfg:
                    cfg["buttons"] = {}
                # Left table (index 0)
                cfg["buttons"]["fold_0"] = {"x": clicks[0]["x"], "y": clicks[0]["y"]}
                cfg["buttons"]["allin_0"] = {"x": clicks[1]["x"], "y": clicks[1]["y"]}
                # Right table (index 1)
                cfg["buttons"]["fold_1"] = {"x": clicks[2]["x"], "y": clicks[2]["y"]}
                cfg["buttons"]["allin_1"] = {"x": clicks[3]["x"], "y": clicks[3]["y"]}
                # Keep legacy keys same as left just in case
                cfg["buttons"]["fold"] = {"x": clicks[0]["x"], "y": clicks[0]["y"]}
                cfg["buttons"]["allin"] = {"x": clicks[1]["x"], "y": clicks[1]["y"]}
                
                from pc_input import CONFIG_PATH
                with open(CONFIG_PATH, "w") as f:
                    import json
                    json.dump(cfg, f, indent=2)

                self._log("2-Table Calibration saved successfully!")
                messagebox.showinfo("Success", "2-Table Calibration saved! You can now START auto-play.")
            else:
                self._log("2-Table Calibration cancelled.")

        except Exception as e:
            import traceback
            traceback.print_exc()
            self._log(f"Calibration error: {e}")
            messagebox.showerror("Error", str(e))

    def _on_screenshot(self):
        try:
            from pc_input import PcController
            ctrl = PcController()
            if not ctrl.check_connection():
                messagebox.showerror("Error", "PPPoker window not found")
                return
            path = ctrl.screenshot()
            self._log(f"Screenshot: {path}")
        except Exception as e:
            messagebox.showerror("Error", str(e))

    def _on_closing(self):
        """Save settings and close application."""
        try:
            self._save_config()
        except:
            pass
        if self.running or self.monitoring:
            if self.capture:
                self.capture.running = False
        self.root.destroy()

    def run(self):
        self.root.mainloop()


if __name__ == "__main__":
    ensure_admin()
    app = AofBotGUI()
    app.run()
