"""Standalone Android GTO Bot for PPPoker AoF.

Uses hash-based card recognition (from card_collector.py) + GTO chart lookup.
No API server needed. Fast action.

Usage:
    python android_gto_bot.py              # Run bot
    python android_gto_bot.py --dry-run    # Observe only, don't tap
    python android_gto_bot.py --test       # Test card reading once
"""

import cv2
import numpy as np
import subprocess
import time
import sys
import json
import random
import argparse
from pathlib import Path
from datetime import datetime

from gto_lookup import GtoLookup, cards_to_hand_name
from adb_input import AdbController

# ============ Config ============

ADB = r"C:\Users\Owner\Desktop\scrcpy-win64-v3.3.3\adb.exe"
DATA_DIR = Path(__file__).parent / "data"
HASH_DB_PATH = DATA_DIR / "card_hashes.json"

# Calibrated card regions (1080x2400)
CARD1_REGION = {"x": 577, "y": 1795, "w": 70, "h": 100}
CARD2_REGION = {"x": 657, "y": 1795, "w": 70, "h": 100}

# Button tap coordinates
FOLD_TAP  = (200, 2320)
ALLIN_TAP = (570, 2320)

# Fold button detection region
FOLD_BTN = {"x": 30, "y": 2280, "w": 340, "h": 90}

# Seat regions for player count detection (same as card_reader.py)
SEAT_REGIONS = [
    {"x": 420, "y": 390, "w": 200, "h": 120},   # top
    {"x": 30,  "y": 1000, "w": 130, "h": 90},    # left
    {"x": 920, "y": 1000, "w": 130, "h": 90},    # right
]

# ============ Hash DB ============

card_hash_db = {}

def load_hash_db():
    global card_hash_db
    if HASH_DB_PATH.exists():
        with open(HASH_DB_PATH, "r") as f:
            card_hash_db = json.load(f)
    print(f"  Loaded {len(card_hash_db)} card hashes")

def card_hash(card_img) -> str:
    gray = cv2.cvtColor(card_img, cv2.COLOR_BGR2GRAY)
    small = cv2.resize(gray, (16, 16), interpolation=cv2.INTER_AREA)
    mean = small.mean()
    bits = (small > mean).flatten()
    h = 0
    for b in bits:
        h = (h << 1) | int(b)
    return format(h, '064x')

def detect_card(card_img) -> str:
    h = card_hash(card_img)
    if h in card_hash_db:
        return card_hash_db[h]
    # Fuzzy match
    best_match, best_dist = None, 999
    h_int = int(h, 16)
    for db_hash, card_name in card_hash_db.items():
        diff = h_int ^ int(db_hash, 16)
        dist = bin(diff).count('1')
        if dist < best_dist:
            best_dist = dist
            best_match = card_name
    if best_match and best_dist <= 20:
        return best_match
    return "??"

# ============ ADB helpers ============

def adb_cmd(*args):
    return subprocess.run([ADB, *args], capture_output=True, timeout=10)

def take_screenshot() -> np.ndarray | None:
    try:
        adb_cmd("shell", "screencap -p /sdcard/aof_tmp.png")
        local = str(DATA_DIR / "live.png")
        adb_cmd("pull", "/sdcard/aof_tmp.png", local)
        adb_cmd("shell", "rm /sdcard/aof_tmp.png")
        return cv2.imread(local)
    except:
        return None

def tap(x, y):
    adb_cmd("shell", f"input tap {x} {y}")

def crop(img, region):
    return img[region["y"]:region["y"]+region["h"],
               region["x"]:region["x"]+region["w"]]

def detect_fold_visible(img) -> bool:
    roi = crop(img, FOLD_BTN)
    hsv = cv2.cvtColor(roi, cv2.COLOR_BGR2HSV)
    r1 = cv2.inRange(hsv, (0, 80, 80), (10, 255, 255))
    r2 = cv2.inRange(hsv, (170, 80, 80), (180, 255, 255))
    ratio = (cv2.countNonZero(r1) + cv2.countNonZero(r2)) / max(roi.size // 3, 1)
    return ratio > 0.10

def count_players(img) -> int:
    count = 1  # hero
    for seat in SEAT_REGIONS:
        roi = crop(img, seat)
        gray = cv2.cvtColor(roi, cv2.COLOR_BGR2GRAY)
        if np.std(gray) > 30:
            count += 1
    return min(count, 4)

# ============ GTO decision ============

def get_decision(gto, hand_name, num_players, prior_actions=""):
    """Try all positions to find a matching chart."""
    positions = {
        2: ["SB", "BB"],
        3: ["BTN", "SB", "BB"],
        4: ["CO", "BTN", "SB", "BB"],
    }
    
    for pos in positions.get(num_players, ["SB"]):
        freq = gto.get_push_freq(hand_name, num_players, pos, prior_actions)
        if freq >= 0:
            return freq, pos
    
    return 0.0, "?"

# ============ Main bot ============

def now():
    return datetime.now().strftime("%H:%M:%S")

SUIT_MAP = {"s": "♠", "h": "♥", "d": "♦", "c": "♣"}
def fmt(card):
    if card == "??": return "??"
    return card[:-1] + SUIT_MAP.get(card[-1], "?")

def run_bot(dry_run=False, poll=0.3):
    print("=" * 50)
    print("  Android GTO Bot")
    print("=" * 50)

    load_hash_db()
    gto = GtoLookup()
    adb = AdbController()
    adb.enabled = not dry_run

    # Override AdbController coordinates with the calibrated ones for this bot
    adb.set_button("fold", FOLD_TAP[0], FOLD_TAP[1])
    adb.set_button("allin", ALLIN_TAP[0], ALLIN_TAP[1])

    print(f"  Poll: {poll}s | Dry run: {dry_run}")
    print("  Ctrl+C to stop\n")

    last_hand = None
    count = 0

    while True:
        try:
            img = take_screenshot()
            if img is None:
                time.sleep(1)
                continue

            if not detect_fold_visible(img):
                sys.stdout.write(f"\r  [{now()}] Waiting...   ")
                sys.stdout.flush()
                last_hand = None
                time.sleep(poll)
                continue

            # Read cards
            c1 = crop(img, CARD1_REGION)
            c2 = crop(img, CARD2_REGION)
            det1 = detect_card(c1)
            det2 = detect_card(c2)

            if det1 == "??" or det2 == "??":
                sys.stdout.write(f"\r  [{now()}] Cards: [{fmt(det1)}][{fmt(det2)}] unrecognized")
                sys.stdout.flush()
                time.sleep(poll)
                continue

            hand_name = cards_to_hand_name(det1, det2)

            # Skip if same hand
            if hand_name == last_hand:
                time.sleep(poll)
                continue

            num_players = count_players(img)
            freq, pos = get_decision(gto, hand_name, num_players)

            # Decision
            if freq >= 0.5:
                action = "allin"
            elif freq > 0 and freq < 0.5:
                action = "allin" if random.random() < freq else "fold"
            else:
                action = "fold"

            count += 1
            print(f"\n  [{now()}] #{count:3d} | {fmt(det1)} {fmt(det2)} = {hand_name:4s} | "
                  f"{num_players}P {pos} | {freq*100:.0f}% → {action.upper()}")

            if not dry_run:
                if action == "allin":
                    adb.tap_allin()
                else:
                    adb.tap_fold()

            last_hand = hand_name
            time.sleep(1.0)

        except KeyboardInterrupt:
            print(f"\n\n  Stopped. {count} hands.")
            break
        except Exception as e:
            print(f"\n  Error: {e}")
            time.sleep(2)


def test_read():
    """Test card reading once."""
    print("=== Test ===")
    load_hash_db()

    img = take_screenshot()
    if img is None:
        print("  Screenshot failed!")
        return

    fold = detect_fold_visible(img)
    print(f"  Fold visible: {fold}")

    c1 = crop(img, CARD1_REGION)
    c2 = crop(img, CARD2_REGION)
    det1 = detect_card(c1)
    det2 = detect_card(c2)
    print(f"  Cards: [{fmt(det1)}] [{fmt(det2)}]")

    np_ = count_players(img)
    print(f"  Players: {np_}")

    if det1 != "??" and det2 != "??":
        hand = cards_to_hand_name(det1, det2)
        print(f"  Hand: {hand}")

        gto = GtoLookup()
        freq, pos = get_decision(gto, hand, np_)
        action = "PUSH" if freq >= 0.5 else "FOLD"
        print(f"  {pos}: freq={freq*100:.0f}% → {action}")


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Android GTO Bot")
    parser.add_argument("--dry-run", action="store_true", help="No taps")
    parser.add_argument("--test", action="store_true", help="Test once")
    parser.add_argument("--poll", type=float, default=0.3, help="Poll interval")
    args = parser.parse_args()

    if args.test:
        test_read()
    else:
        run_bot(dry_run=args.dry_run, poll=args.poll)
