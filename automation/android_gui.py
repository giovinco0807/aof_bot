"""Unified AoF Bot GUI - PC (Frida hook) + Android (ADB) combined.

Architecture:
  - PC Thread: Runs PacketCapture (Frida hook) → intercepts PPPoker packets
    → gets all players' cards, actions, winners → records to hands.db
    → writes pc_hero_cards.json for Android card auto-learning
  - Android Thread: Uses card_collector functions (hash-based recognition)
    → GTO decision → auto-tap
  - GUI: Unified tkinter interface

Usage:
    python android_gui.py
"""

import tkinter as tk
from tkinter import ttk, scrolledtext
import threading
import time
import sys
import csv
import json
import random
from pathlib import Path
from datetime import datetime
import subprocess
import ctypes
import ctypes.wintypes as wt

# Adjust path for imports
sys.path.insert(0, str(Path(__file__).parent.parent / "hook"))
sys.path.insert(0, str(Path(__file__).parent))

# Reuse card_collector exactly as-is
from card_collector import (
    take_screenshot, crop, detect_fold_visible, detect_card,
    load_hash_db, save_hash_db, learn_cards, card_hash_db, card_hash,
    fmt_card, tap, FOLD_TAP, ALLIN_TAP,
    DEFAULT_CARD1, DEFAULT_CARD2, ADB,
    HASH_DB_PATH, DATA_DIR,
)
from gto_lookup import GtoLookup, cards_to_hand_name
from hand_db import get_all_player_stats, init_db
from adb_input import AdbController

# ============ Config ============
HISTORY_CSV = DATA_DIR / "hand_history.csv"
PC_CARDS_PATH = DATA_DIR / "pc_hero_cards.json"
HANDS_DB = DATA_DIR / "hands.db"
ANDROID_UID = 13393284

SEAT_REGIONS = [
    {"x": 420, "y": 390, "w": 200, "h": 120},
    {"x": 30,  "y": 1000, "w": 130, "h": 90},
    {"x": 920, "y": 1000, "w": 130, "h": 90},
]

SUIT_SYM = {"s": "♠", "h": "♥", "d": "♦", "c": "♣"}
SUIT_CLR = {"s": "#AAAAAA", "h": "#FF4444", "d": "#4488FF", "c": "#44BB44"}

def fmt(c):
    if not c or c == "??": return "??"
    return c[:-1] + SUIT_SYM.get(c[-1], "?")

def _db_size():
    return len(card_hash_db.get("1", {})) + len(card_hash_db.get("2", {}))


# ============ ADB helpers ============
import cv2
import numpy as np

def count_p(img):
    c = 1
    for s in SEAT_REGIONS:
        roi = crop(img, s)
        if np.std(cv2.cvtColor(roi, cv2.COLOR_BGR2GRAY)) > 30:
            c += 1
    return min(c, 4)


# ============ PC Click (Windows API) ============
PC_WINDOW_TITLE = "PPPoker v"
PC_BUTTONS = {
    "allin": {"x": 801, "y": 929},
    "fold":  {"x": 628, "y": 929},
}

def pc_find_hwnd():
    result = [None]
    @ctypes.WINFUNCTYPE(ctypes.c_bool, ctypes.c_void_p, ctypes.c_void_p)
    def cb(hwnd, _):
        buf = ctypes.create_unicode_buffer(256)
        ctypes.windll.user32.GetWindowTextW(hwnd, buf, 256)
        if PC_WINDOW_TITLE.lower() in buf.value.lower():
            result[0] = hwnd
            return False
        return True
    ctypes.windll.user32.EnumWindows(cb, 0)
    return result[0]

def pc_click(hwnd, btn_key="allin"):
    btn = PC_BUTTONS[btn_key]
    rect = wt.RECT()
    ctypes.windll.user32.GetWindowRect(hwnd, ctypes.byref(rect))
    sx = rect.left + btn["x"]
    sy = rect.top + btn["y"]
    # Save cursor position
    pt = wt.POINT()
    ctypes.windll.user32.GetCursorPos(ctypes.byref(pt))
    # Alt key trick to allow SetForegroundWindow
    ctypes.windll.user32.keybd_event(0x12, 0, 0, 0)  # Alt down
    ctypes.windll.user32.SetForegroundWindow(hwnd)
    ctypes.windll.user32.keybd_event(0x12, 0, 2, 0)  # Alt up
    time.sleep(0.05)
    ctypes.windll.user32.SetCursorPos(sx, sy)
    time.sleep(0.03)
    ctypes.windll.user32.mouse_event(0x0002, 0, 0, 0, 0)
    time.sleep(0.02)
    ctypes.windll.user32.mouse_event(0x0004, 0, 0, 0, 0)
    # Restore cursor
    time.sleep(0.02)
    ctypes.windll.user32.SetCursorPos(pt.x, pt.y)


# ============ PC Data Reader ============
def read_pc_data():
    """Read latest PC hook data from pc_hero_cards.json."""
    if not PC_CARDS_PATH.exists():
        return None
    try:
        with open(PC_CARDS_PATH, "r") as f:
            data = json.load(f)
        return data
    except:
        return None


# ============ GUI ============
class App:
    BG = "#1a1a2e"
    FG = "#eaeaea"
    CB = "#16213e"
    ACCENT = "#e94560"
    GREEN = "#00b894"
    RED = "#d63031"
    YELLOW = "#fdcb6e"
    DIM = "#666666"

    def __init__(self):
        self.root = tk.Tk()
        self.root.title("AoF GTO Bot")
        self.root.geometry("600x850")
        self.root.configure(bg=self.BG)
        self.root.resizable(True, True)

        self.running = False
        self.pc_running = False
        self.pc_capture = None
        self.gto = None
        self.hand_count = 0
        self.last_hand = None
        self.last_pc_hand = -1
        self.last_card_imgs = (None, None)  # For auto-learn
        self.snap_consumed = True  # True = images already used for learning

        self._build()
        self._init()
        self._start_hotkey()

    def _init(self):
        load_hash_db()
        self.gto = GtoLookup()
        self.adb = AdbController()
        # Ensure AdbController uses the exact SAME coordinates
        self.adb.set_button("fold", FOLD_TAP[0], FOLD_TAP[1])
        self.adb.set_button("allin", ALLIN_TAP[0], ALLIN_TAP[1])
        self.adb.enabled = True  # We gate actions with self.auto_var anyway

        init_db(HANDS_DB)
        self._log(f"ハッシュDB: {_db_size()}枚 | GTO チャート読込完了", "info")

        # Check ADB
        try:
            r = subprocess.run([ADB, "devices"], capture_output=True, timeout=5, text=True)
            devs = [l for l in r.stdout.strip().split("\n")[1:] if "device" in l]
            if devs:
                self._log(f"Android: ✓ {devs[0].split()[0]}", "info")
            else:
                self._log("Android: ✗ 未接続", "warn")
        except:
            self._log("ADB: エラー", "warn")

    def _build(self):
        B, F, CB, A = self.BG, self.FG, self.CB, self.ACCENT

        # Title
        tk.Label(self.root, text="♠ AoF GTO Bot ♥  [Ctrl+Shift+S]", font=("Segoe UI", 16, "bold"),
                 bg=B, fg=A).pack(pady=(8, 2))

        # Status bar
        sf = tk.Frame(self.root, bg=CB, relief="ridge", bd=1)
        sf.pack(fill="x", padx=10, pady=3)
        self.status_lbl = tk.Label(sf, text="● 停止中", font=("Segoe UI", 10),
                                    bg=CB, fg=self.RED, padx=8, pady=2)
        self.status_lbl.pack(side="left")
        self.pc_status_lbl = tk.Label(sf, text="PC: --", font=("Segoe UI", 10),
                                       bg=CB, fg=self.DIM, padx=8)
        self.pc_status_lbl.pack(side="left")
        self.count_lbl = tk.Label(sf, text="Hands: 0", font=("Segoe UI", 10),
                                   bg=CB, fg=F, padx=8)
        self.count_lbl.pack(side="right")
        self.hashdb_lbl = tk.Label(sf, text=f"DB:{_db_size()}", font=("Segoe UI", 10),
                                    bg=CB, fg=self.DIM, padx=8)
        self.hashdb_lbl.pack(side="right")

        # Card display
        cf = tk.Frame(self.root, bg=CB, relief="ridge", bd=1)
        cf.pack(fill="x", padx=10, pady=3)
        tk.Label(cf, text="HERO (Android)", font=("Segoe UI", 8, "bold"),
                 bg=CB, fg=self.DIM).pack(pady=(4,0))

        cr = tk.Frame(cf, bg=CB)
        cr.pack(pady=2)
        self.c1_lbl = tk.Label(cr, text="🂠", font=("Segoe UI", 30), bg=CB, fg="#444", width=3)
        self.c1_lbl.pack(side="left", padx=6)
        self.c2_lbl = tk.Label(cr, text="🂠", font=("Segoe UI", 30), bg=CB, fg="#444", width=3)
        self.c2_lbl.pack(side="left", padx=6)

        ir = tk.Frame(cf, bg=CB)
        ir.pack(pady=(0, 2))
        self.hand_lbl = tk.Label(ir, text="--", font=("Segoe UI", 14, "bold"), bg=CB, fg=F)
        self.hand_lbl.pack(side="left", padx=10)
        self.freq_lbl = tk.Label(ir, text="", font=("Segoe UI", 12), bg=CB, fg="#999")
        self.freq_lbl.pack(side="left", padx=4)
        self.dec_lbl = tk.Label(ir, text="", font=("Segoe UI", 14, "bold"), bg=CB, fg="#999")
        self.dec_lbl.pack(side="left", padx=10)

        pr = tk.Frame(cf, bg=CB)
        pr.pack(pady=(0, 4))
        self.np_lbl = tk.Label(pr, text="--P", font=("Segoe UI", 10), bg=CB, fg="#888")
        self.np_lbl.pack(side="left", padx=10)
        self.pos_lbl = tk.Label(pr, text="", font=("Segoe UI", 10), bg=CB, fg="#888")
        self.pos_lbl.pack(side="left", padx=10)

        # PC Showdown info
        pcf = tk.Frame(self.root, bg=CB, relief="ridge", bd=1)
        pcf.pack(fill="x", padx=10, pady=3)
        tk.Label(pcf, text="PC SHOWDOWN (最新)", font=("Segoe UI", 8, "bold"),
                 bg=CB, fg=self.DIM).pack(pady=(3,0))
        self.pc_info_lbl = tk.Label(pcf, text="-- データなし --",
                                     font=("Consolas", 10), bg=CB, fg="#999",
                                     justify="left", anchor="w")
        self.pc_info_lbl.pack(fill="x", padx=10, pady=4)

        # Controls
        bf = tk.Frame(self.root, bg=B)
        bf.pack(fill="x", padx=10, pady=3)
        self.start_btn = tk.Button(bf, text="▶ ALL START", font=("Segoe UI", 11, "bold"),
                                    bg=self.GREEN, fg="white", relief="flat",
                                    command=self._start_all, width=13)
        self.start_btn.pack(side="left", padx=3, expand=True, fill="x")
        self.stop_btn = tk.Button(bf, text="■ STOP", font=("Segoe UI", 11, "bold"),
                                   bg=self.RED, fg="white", relief="flat",
                                   command=self._stop_all, width=13, state="disabled")
        self.stop_btn.pack(side="left", padx=3, expand=True, fill="x")

        # Settings
        sf2 = tk.LabelFrame(self.root, text=" 設定 ", font=("Segoe UI", 9),
                             bg=B, fg=self.DIM, bd=1, relief="groove")
        sf2.pack(fill="x", padx=10, pady=3)
        self.auto_var = tk.BooleanVar(value=True)
        tk.Checkbutton(sf2, text="自動タップ", variable=self.auto_var,
                       font=("Segoe UI", 10), bg=B, fg=F,
                       selectcolor=CB, activebackground=B, activeforeground=F).pack(side="left", padx=6)
        self.learn_var = tk.BooleanVar(value=True)
        tk.Checkbutton(sf2, text="学習モード", variable=self.learn_var,
                       font=("Segoe UI", 10), bg=B, fg="#a29bfe",
                       selectcolor=CB, activebackground=B, activeforeground=F).pack(side="left", padx=6)
        tk.Label(sf2, text="速度:", font=("Segoe UI", 9), bg=B, fg=self.DIM).pack(side="left", padx=(10,2))
        self.speed_var = tk.DoubleVar(value=0.1)
        tk.Scale(sf2, from_=0.1, to=3.0, resolution=0.1,
                 orient="horizontal", variable=self.speed_var,
                 length=120, bg=B, fg=F, troughcolor=CB,
                 highlightthickness=0, font=("Segoe UI", 8)).pack(side="left", padx=2)
        tk.Label(sf2, text="秒", font=("Segoe UI", 9), bg=B, fg=self.DIM).pack(side="left")

        # Tabs
        nb = ttk.Notebook(self.root)
        nb.pack(fill="both", expand=True, padx=10, pady=3)

        # Tab 1: Log
        lf = tk.Frame(nb, bg=B)
        nb.add(lf, text=" ログ ")
        self.log_text = scrolledtext.ScrolledText(
            lf, height=8, font=("Consolas", 9),
            bg="#0f0f23", fg="#cccccc", relief="flat", bd=0, wrap="word")
        self.log_text.pack(fill="both", expand=True, padx=2, pady=2)
        self.log_text.tag_config("push", foreground=self.GREEN)
        self.log_text.tag_config("fold", foreground=self.RED)
        self.log_text.tag_config("info", foreground="#6c5ce7")
        self.log_text.tag_config("warn", foreground=self.YELLOW)
        self.log_text.tag_config("pc", foreground="#74b9ff")
        self.log_text.tag_config("learn", foreground="#a29bfe")

        # Tab 2: Opponent Stats
        stf = tk.Frame(nb, bg=B)
        nb.add(stf, text=" 対戦相手Stats ")
        cols = ("player", "hands", "push", "rate")
        self.stats_tree = ttk.Treeview(stf, columns=cols, show="headings", height=8)
        self.stats_tree.heading("player", text="Player ID")
        self.stats_tree.heading("hands", text="Hands")
        self.stats_tree.heading("push", text="Push")
        self.stats_tree.heading("rate", text="率%")
        self.stats_tree.column("player", width=180)
        self.stats_tree.column("hands", width=60, anchor="center")
        self.stats_tree.column("push", width=60, anchor="center")
        self.stats_tree.column("rate", width=60, anchor="center")
        self.stats_tree.pack(fill="both", expand=True, padx=2, pady=2)
        tk.Button(stf, text="更新", font=("Segoe UI", 9), bg=CB, fg=F,
                  relief="flat", command=self._refresh_stats).pack(pady=3)

        # Tab 3: PC History
        hf = tk.Frame(nb, bg=B)
        nb.add(hf, text=" PC履歴 ")
        self.hist_text = scrolledtext.ScrolledText(
            hf, height=8, font=("Consolas", 9),
            bg="#0f0f23", fg="#cccccc", relief="flat", bd=0, wrap="word")
        self.hist_text.pack(fill="both", expand=True, padx=2, pady=2)

    # ---- Logging ----
    def _log(self, msg, tag=None):
        ts = datetime.now().strftime("%H:%M:%S")
        self.log_text.insert("end", f"[{ts}] {msg}\n", tag)
        self.log_text.see("end")

    def _safe_log(self, msg, tag=None):
        self.root.after(0, lambda: self._log(msg, tag))

    # ---- Stats ----
    def _refresh_stats(self):
        for row in self.stats_tree.get_children():
            self.stats_tree.delete(row)
        try:
            stats = get_all_player_stats(HANDS_DB)
            for s in stats:
                rate = f"{s['push_rate']*100:.0f}"
                self.stats_tree.insert("", "end",
                    values=(s["player_id"], s["hands_seen"], s["hands_pushed"], rate))
        except Exception as e:
            self._safe_log(f"Stats error: {e}", "warn")

    # ---- Card display ----
    def _show_cards(self, d1, d2, hand="", freq=-1, action="", pos="", np_=0):
        def _ct(c):
            if not c or c == "??": return "🂠", "#444"
            return c[:-1] + SUIT_SYM.get(c[-1], "?"), SUIT_CLR.get(c[-1], "#ccc")
        t1, cl1 = _ct(d1)
        t2, cl2 = _ct(d2)
        self.c1_lbl.config(text=t1, fg=cl1)
        self.c2_lbl.config(text=t2, fg=cl2)
        self.hand_lbl.config(text=hand or "--")
        self.np_lbl.config(text=f"{np_}P" if np_ else "--P")
        self.pos_lbl.config(text=pos)
        self.freq_lbl.config(text=f"{freq*100:.0f}%" if freq >= 0 else "")
        if action == "allin":
            self.dec_lbl.config(text="ALL-IN", fg=self.GREEN)
        elif action == "fold":
            self.dec_lbl.config(text="FOLD", fg=self.RED)
        else:
            self.dec_lbl.config(text="")

    # ---- Record ----
    def _record_csv(self, hand, np_, pos, freq, act, d1, d2):
        HISTORY_CSV.parent.mkdir(parents=True, exist_ok=True)
        new = not HISTORY_CSV.exists()
        with open(HISTORY_CSV, "a", newline="") as f:
            w = csv.writer(f)
            if new:
                w.writerow(["time","hand","players","pos","freq","action","c1","c2"])
            w.writerow([datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
                        hand, np_, pos, f"{freq:.3f}", act, d1, d2])

    # ---- Start/Stop ----
    def _start_all(self):
        if self.running: return
        self.running = True
        self.start_btn.config(state="disabled")
        self.stop_btn.config(state="normal")
        self.status_lbl.config(text="● 稼働中", fg=self.GREEN)
        self._log("Bot起動!", "info")

        # PC capture thread
        self.pc_running = True
        threading.Thread(target=self._pc_loop, daemon=True).start()

        # PC auto-click thread
        threading.Thread(target=self._pc_click_loop, daemon=True).start()

        # Android bot thread
        threading.Thread(target=self._android_loop, daemon=True).start()
        self._log("Android Bot + PC自動ALL-IN + PC監視 起動", "info")

    def _stop_all(self):
        self.running = False
        self.pc_running = False
        self.start_btn.config(state="normal")
        self.stop_btn.config(state="disabled")
        self.status_lbl.config(text="● 停止中", fg=self.RED)
        self.pc_status_lbl.config(text="PC: 停止", fg=self.DIM)
        self._log(f"全停止 ({self.hand_count} hands)", "warn")

    # ---- PC Loop ----
    def _pc_loop(self):
        """Read PC hook data, trigger screenshot on new hand, auto-learn hashes."""
        self.root.after(0, lambda: self.pc_status_lbl.config(text="PC: 監視中", fg="#74b9ff"))

        # Try Frida
        try:
            from packet_capture import PacketCapture
            cap = PacketCapture(
                process_name="PPPoker.exe",
                verbose=False,
                enable_solver=False,
                hero_uid=0,
                auto_play=False,
            )
            cap.start()
            self.pc_capture = cap
            self._safe_log("Frida hook 接続成功!", "pc")
        except SystemExit:
            self._safe_log("PPPoker.exe未検出 → pc_hero_cards.json監視モード", "warn")
        except Exception as e:
            self._safe_log(f"Frida未使用: {e}", "warn")

        last_hand_num = -1

        while self.pc_running:
            try:
                pc = read_pc_data()
                if not pc or pc.get("hand", -1) == last_hand_num:
                    time.sleep(0.5)
                    continue

                new_hand = pc["hand"]
                all_hands = pc.get("all_hands", [])
                if not all_hands:
                    time.sleep(0.5)
                    continue

                # ---- New hand detected! ----
                last_hand_num = new_hand

                # Learn from card images saved by Android loop
                # Only learn if we have unconsumed card images
                if self.learn_var.get() and self.last_card_imgs[0] is not None and not self.snap_consumed:
                    android_cards = None
                    for h in all_hands:
                        if isinstance(h, dict) and h.get("uid") == ANDROID_UID:
                            android_cards = h.get("cards", [])
                            break
                    if android_cards and len(android_cards) == 2:
                        c1_img, c2_img = self.last_card_imgs
                        old_size = _db_size()
                        learn_cards(c1_img, c2_img, android_cards)
                        new_size = _db_size()
                        self.snap_consumed = True  # Mark as used
                        if new_size > old_size:
                            self._safe_log(
                                f"[LEARN] #{new_hand} {fmt_card(android_cards[0])} {fmt_card(android_cards[1])} "
                                f"(DB: {old_size}→{new_size}) ✓", "learn")
                            self.root.after(0, lambda n=new_size:
                                self.hashdb_lbl.config(text=f"DB:{n}"))
                            # Save sample images for verification
                            sample_dir = DATA_DIR / "card_samples_new"
                            sample_dir.mkdir(exist_ok=True)
                            ts = datetime.now().strftime("%H%M%S")
                            cv2.imwrite(str(sample_dir / f"h{new_hand}_{ts}_c1_{android_cards[0]}.png"), c1_img)
                            cv2.imwrite(str(sample_dir / f"h{new_hand}_{ts}_c2_{android_cards[1]}.png"), c2_img)
                        else:
                            self._safe_log(
                                f"[OK] {fmt_card(android_cards[0])} {fmt_card(android_cards[1])} "
                                f"already in DB ({new_size})", "learn")
                            self.root.after(0, lambda n=new_size:
                                self.hashdb_lbl.config(text=f"DB:{n}"))

                # Show all players' cards
                parts = []
                for h in all_hands:
                    if isinstance(h, dict):
                        uid = h.get("uid", "?")
                        cards = h.get("cards", [])
                        cs = " ".join(fmt_card(c) for c in cards)
                        marker = "★" if uid == ANDROID_UID else " "
                        parts.append(f"{marker}[{uid}] {cs}")

                pc_text = "\n".join(parts)
                self.root.after(0, lambda t=pc_text: self.pc_info_lbl.config(text=t))
                self._safe_log(f"PC #{new_hand}: {' | '.join(parts)}", "pc")

                # History tab
                hist_line = f"#{new_hand} {pc.get('time','')} | {' | '.join(parts)}"
                self.root.after(0, lambda l=hist_line:
                    (self.hist_text.insert("end", l + "\n"),
                     self.hist_text.see("end")))

                time.sleep(0.5)
            except Exception as e:
                self._safe_log(f"PC error: {e}", "warn")
                time.sleep(3)

    # ---- PC Auto-Click Loop ----
    def _pc_click_loop(self):
        """Auto-click ALL-IN on PC PPPoker window every 1.5 seconds."""
        hwnd = pc_find_hwnd()
        if not hwnd:
            self._safe_log("PC: PPPokerウィンドウ未検出", "warn")
            return
        self._safe_log("PC自動ALL-IN 開始", "pc")
        count = 0
        while self.pc_running:
            try:
                hwnd = pc_find_hwnd()
                if not hwnd:
                    time.sleep(2); continue
                if self.auto_var.get():
                    pc_click(hwnd, "allin")
                    count += 1
                    if count % 10 == 1:
                        self._safe_log(f"PC ALL-IN #{count}", "pc")
                time.sleep(1.5)
            except Exception as e:
                self._safe_log(f"PC click error: {e}", "warn")
                time.sleep(3)

    # ---- Android Bot Loop ----
    def _android_loop(self):
        last = None
        while self.running:
            try:
                poll = self.speed_var.get()

                # === Learning mode: screenshot THEN tap ALL-IN ===
                if self.learn_var.get():
                    if self.auto_var.get():
                        # Take screenshot BEFORE tapping (cards are still visible)
                        img = take_screenshot()
                        if img is not None and detect_fold_visible(img):
                            c1 = crop(img, DEFAULT_CARD1)
                            c2 = crop(img, DEFAULT_CARD2)
                            self.last_card_imgs = (c1.copy(), c2.copy())
                            self.snap_consumed = False  # Mark as fresh/unconsumed
                            d1 = detect_card(c1, slot=1)
                            d2 = detect_card(c2, slot=2)
                            self.root.after(0, lambda a=d1, b=d2: self._show_cards(a, b))
                            # Tap ALL-IN
                            self.adb.tap_allin()
                            self.hand_count += 1
                            self.root.after(0, lambda: self.count_lbl.config(
                                text=f"Hands: {self.hand_count}"))
                            self._safe_log(
                                f"[LEARN] #{self.hand_count} {fmt_card(d1)} {fmt_card(d2)} → ALL-IN "
                                f"(DB:{_db_size()})", "learn")
                            self.root.after(0, lambda n=_db_size():
                                self.hashdb_lbl.config(text=f"DB:{n}"))
                        else:
                            # No fold button = between hands, just tap
                            self.adb.tap_allin()
                    time.sleep(1.5); continue

                # === GTO mode: need screenshot + card recognition ===
                img = take_screenshot()
                if img is None:
                    time.sleep(1); continue

                if not detect_fold_visible(img):
                    self.root.after(0, lambda: self._show_cards("??", "??"))
                    last = None
                    time.sleep(poll); continue

                c1 = crop(img, DEFAULT_CARD1)
                c2 = crop(img, DEFAULT_CARD2)
                self.last_card_imgs = (c1.copy(), c2.copy())
                d1, d2 = detect_card(c1, slot=1), detect_card(c2, slot=2)

                if d1 == "??" or d2 == "??":
                    self.root.after(0, lambda a=d1, b=d2: self._show_cards(a, b))
                    time.sleep(poll); continue

                hand = cards_to_hand_name(d1, d2)
                if hand == last:
                    time.sleep(poll); continue

                np_ = count_p(img)

                # GTO lookup
                positions = {2: ["SB","BB"], 3: ["BTN","SB","BB"], 4: ["CO","BTN","SB","BB"]}
                freq, pos = 0.0, "?"
                for p in positions.get(np_, ["SB"]):
                    f = self.gto.get_push_freq(hand, np_, p, "")
                    if f >= 0:
                        freq, pos = f, p; break

                # Decision
                if freq >= 0.5:
                    act = "allin"
                elif freq > 0:
                    act = "allin" if random.random() < freq else "fold"
                else:
                    act = "fold"

                self.hand_count += 1
                last = hand

                # Update GUI
                self.root.after(0, lambda a=d1,b=d2,h=hand,f=freq,c=act,p=pos,n=np_:
                    self._show_cards(a,b,h,f,c,p,n))
                self.root.after(0, lambda: self.count_lbl.config(text=f"Hands: {self.hand_count}"))

                tag = "push" if act == "allin" else "fold"
                msg = f"#{self.hand_count:3d} {fmt_card(d1)} {fmt_card(d2)}={hand} {np_}P {pos} {freq*100:.0f}% → {act.upper()}"
                self._safe_log(msg, tag)

                self._record_csv(hand, np_, pos, freq, act, d1, d2)

                # Tap
                if self.auto_var.get():
                    if act == "allin":
                        self.adb.tap_allin()
                    else:
                        self.adb.tap_fold()

                time.sleep(max(poll, 0.5))

            except Exception as e:
                self._safe_log(f"Android error: {e}", "warn")
                time.sleep(2)

    # ---- Global Hotkey (Ctrl+Shift+S) ----
    def _start_hotkey(self):
        """Poll GetAsyncKeyState for Ctrl+Shift+S to toggle start/stop."""
        threading.Thread(target=self._hotkey_loop, daemon=True).start()

    def _hotkey_loop(self):
        VK_S = 0x53
        VK_CONTROL = 0x11
        VK_SHIFT = 0x10
        get_key = ctypes.windll.user32.GetAsyncKeyState
        was_pressed = False
        while True:
            ctrl = get_key(VK_CONTROL) & 0x8000
            shift = get_key(VK_SHIFT) & 0x8000
            s = get_key(VK_S) & 0x8000
            pressed = bool(ctrl and shift and s)
            if pressed and not was_pressed:
                self.root.after(0, self._toggle)
            was_pressed = pressed
            time.sleep(0.2)

    def _toggle(self):
        if self.running:
            self._stop_all()
        else:
            self._start_all()

    def run(self):
        self.root.protocol("WM_DELETE_WINDOW", self._close)
        self._refresh_stats()
        self.root.mainloop()

    def _close(self):
        self.running = False
        self.pc_running = False
        if self.pc_capture:
            try: self.pc_capture.stop()
            except: pass
        self.root.destroy()


if __name__ == "__main__":
    app = App()
    app.run()
