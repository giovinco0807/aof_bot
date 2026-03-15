"""Auto ALL-IN + card verification loop with hash-based OCR.

Uses OpenHoldem-style perceptual hashing for card recognition.
Auto-learns card hashes from PC (Frida) showdown data.

Usage:
    cd d:\aof_bot\automation
    python card_collector.py
    python card_collector.py --card1 577,1795,70,100 --card2 657,1795,70,100
"""

import cv2
import numpy as np
import subprocess
import time
import sys
import json
import argparse
from pathlib import Path
from datetime import datetime

ADB = r"C:\Users\Owner\Desktop\scrcpy-win64-v3.3.3\adb.exe"
DATA_DIR = Path(__file__).parent / "data"
COLLECT_DIR = DATA_DIR / "card_samples"

# Default card regions (1080x2400, calibrated from screenshot)
DEFAULT_CARD1 = {"x": 577, "y": 1795, "w": 70, "h": 100}
DEFAULT_CARD2 = {"x": 657, "y": 1795, "w": 70, "h": 100}

# Button tap coordinates (center of each button)
FOLD_TAP  = (200, 2320)
ALLIN_TAP = (570, 2320)

# Fold button detection region
FOLD_BTN = {"x": 30, "y": 2280, "w": 340, "h": 90}

# Hash-based card recognition database
HASH_DB_PATH = DATA_DIR / "card_hashes.json"
card_hash_db = {}


# ============ Utility functions ============

def adb_cmd(*args):
    return subprocess.run([ADB, *args], capture_output=True, timeout=10)


def take_screenshot() -> np.ndarray | None:
    """Take ADB screenshot, return as OpenCV image.
    
    Uses exec-out to pipe PNG directly to memory (fast).
    Falls back to shell screencap + pull if exec-out fails.
    """
    try:
        # Fast path: pipe directly to memory
        r = subprocess.run(
            [ADB, "exec-out", "screencap", "-p"],
            capture_output=True, timeout=10
        )
        if r.returncode == 0 and len(r.stdout) > 1000:
            arr = np.frombuffer(r.stdout, dtype=np.uint8)
            img = cv2.imdecode(arr, cv2.IMREAD_COLOR)
            if img is not None:
                return img
    except Exception:
        pass

    # Fallback: file-based
    try:
        adb_cmd("shell", "screencap -p /sdcard/aof_tmp.png")
        local = str(DATA_DIR / "live.png")
        adb_cmd("pull", "/sdcard/aof_tmp.png", local)
        adb_cmd("shell", "rm /sdcard/aof_tmp.png")
        return cv2.imread(local)
    except Exception as e:
        print(f"  Screenshot error: {e}")
        return None


def tap(x: int, y: int):
    """ADB tap at coordinates."""
    adb_cmd("shell", f"input tap {x} {y}")


def crop(img, region):
    """Crop a region from the image."""
    return img[region["y"]:region["y"]+region["h"],
               region["x"]:region["x"]+region["w"]]


def detect_fold_visible(img) -> bool:
    """Check if fold button (red) is visible."""
    roi = crop(img, FOLD_BTN)
    hsv = cv2.cvtColor(roi, cv2.COLOR_BGR2HSV)
    r1 = cv2.inRange(hsv, (0, 80, 80), (10, 255, 255))
    r2 = cv2.inRange(hsv, (170, 80, 80), (180, 255, 255))
    ratio = (cv2.countNonZero(r1) + cv2.countNonZero(r2)) / max(roi.size // 3, 1)
    return ratio > 0.10


# ============ Hash-based card recognition ============

def load_hash_db():
    global card_hash_db
    if HASH_DB_PATH.exists():
        with open(HASH_DB_PATH, "r") as f:
            raw = json.load(f)
        # Auto-migrate old flat format → new split format
        if "1" in raw and "2" in raw and isinstance(raw["1"], dict):
            card_hash_db = raw
        else:
            # Old flat format: copy into both slots
            card_hash_db = {"1": dict(raw), "2": dict(raw)}
            save_hash_db()
    else:
        card_hash_db = {"1": {}, "2": {}}
    total = len(card_hash_db.get("1", {})) + len(card_hash_db.get("2", {}))
    if total:
        print(f"  Loaded {total} card hashes (slot1:{len(card_hash_db['1'])}, slot2:{len(card_hash_db['2'])})")


def save_hash_db():
    HASH_DB_PATH.parent.mkdir(parents=True, exist_ok=True)
    with open(HASH_DB_PATH, "w") as f:
        json.dump(card_hash_db, f, indent=2)


def card_hash(card_img) -> str:
    """Compute perceptual hash: resize to 16x16 grayscale, compare to mean."""
    gray = cv2.cvtColor(card_img, cv2.COLOR_BGR2GRAY)
    small = cv2.resize(gray, (16, 16), interpolation=cv2.INTER_AREA)
    mean = small.mean()
    bits = (small > mean).flatten()
    h = 0
    for b in bits:
        h = (h << 1) | int(b)
    return format(h, '064x')


def detect_card(card_img, slot=0) -> str:
    """Detect card using hash lookup. slot=1 or 2 for position-specific, 0 for any.
    Returns e.g. 'Ah', 'Kd', or '??'."""
    h = card_hash(card_img)

    # Determine which sub-dicts to search
    if slot in (1, 2):
        dbs = [card_hash_db.get(str(slot), {})]
    else:
        dbs = [card_hash_db.get("1", {}), card_hash_db.get("2", {})]

    # Exact match first
    for db in dbs:
        if h in db:
            return db[h]

    # Fuzzy match (hamming distance)
    best_match = None
    best_dist = 999
    h_int = int(h, 16)
    for db in dbs:
        for db_hash, card_name in db.items():
            db_int = int(db_hash, 16)
            dist = bin(h_int ^ db_int).count('1')
            if dist < best_dist:
                best_dist = dist
                best_match = card_name

    if best_match and best_dist <= 20:
        return best_match

    return "??"


def learn_cards(card1_img, card2_img, android_hand: list):
    """Learn card hashes from PC ground truth, stored per slot.
    
    android_hand is ["7c", "4h"] - the Android player's cards from showdown.
    """
    if not android_hand or len(android_hand) != 2:
        return

    h1 = card_hash(card1_img)
    h2 = card_hash(card2_img)
    pc1, pc2 = android_hand[0], android_hand[1]

    if "1" not in card_hash_db:
        card_hash_db["1"] = {}
    if "2" not in card_hash_db:
        card_hash_db["2"] = {}

    changed = False
    if h1 not in card_hash_db["1"]:
        card_hash_db["1"][h1] = pc1
        changed = True
    if h2 not in card_hash_db["2"]:
        card_hash_db["2"][h2] = pc2
        changed = True

    if changed:
        save_hash_db()
        print(f"  [LEARN] {pc1} + {pc2} (db:{len(card_hash_db)})")


# ============ Card name formatting ============

SUIT_MAP = {"s": "♠", "h": "♥", "d": "♦", "c": "♣"}

def fmt_card(card_str):
    if card_str == "??":
        return "??"
    rank = card_str[:-1]
    suit = card_str[-1]
    return f"{rank}{SUIT_MAP.get(suit, '?')}"


# ============ CLI argument parsing ============

def parse_region(s: str) -> dict:
    """Parse 'x,y,w,h' string into region dict."""
    parts = [int(x) for x in s.split(",")]
    if len(parts) != 4:
        raise ValueError("Region must be x,y,w,h")
    return {"x": parts[0], "y": parts[1], "w": parts[2], "h": parts[3]}

# ============ Calibration ============

def calibrate(card1_region, card2_region):
    """Interactive calibration: show screenshot with adjustable card regions."""
    print("=" * 50)
    print("  Interactive Calibration")
    print("=" * 50)
    print("  Taking screenshot...")

    img = take_screenshot()
    if img is None:
        print("  ERROR: Could not take screenshot. Is device connected?")
        return

    h, w = img.shape[:2]
    print(f"  Screenshot: {w}x{h}")

    regions = [
        dict(card1_region),  # 0 = card1
        dict(card2_region),  # 1 = card2
    ]
    names = ["CARD 1", "CARD 2"]
    colors = [(0, 0, 255), (255, 0, 0)]  # Red, Blue
    selected = 0  # Currently selected region
    step = 5  # Movement step

    print("\n  Controls:")
    print("    Arrow keys : Move selected region")
    print("    +/- or W/S : Resize width")
    print("    A/D        : Resize height")
    print("    Tab        : Switch card 1/2")
    print("    R          : Refresh screenshot")
    print("    Enter/Q    : Confirm and exit\n")

    def draw(base_img, regions, selected):
        disp = base_img.copy()
        for i, reg in enumerate(regions):
            x, y, rw, rh = reg["x"], reg["y"], reg["w"], reg["h"]
            thickness = 4 if i == selected else 2
            cv2.rectangle(disp, (x, y), (x + rw, y + rh), colors[i], thickness)
            label = f"{names[i]} ({x},{y},{rw}x{rh})"
            cv2.putText(disp, label, (x, y - 15),
                        cv2.FONT_HERSHEY_SIMPLEX, 0.7, colors[i], 2)

            # Draw card crop preview in top corner
            card_crop = base_img[y:y+rh, x:x+rw]
            if card_crop.size > 0:
                preview = cv2.resize(card_crop, (card_crop.shape[1]*2, card_crop.shape[0]*2))
                px = 10 + i * (preview.shape[1] + 20)
                py = 10
                disp[py:py+preview.shape[0], px:px+preview.shape[1]] = preview
                cv2.rectangle(disp, (px-2, py-2),
                             (px+preview.shape[1]+2, py+preview.shape[0]+2),
                             colors[i], 2)

        # Scale down for display
        scale = 0.5
        small = cv2.resize(disp, None, fx=scale, fy=scale)
        return small

    cv2.namedWindow("Calibrate", cv2.WINDOW_NORMAL)
    cv2.resizeWindow("Calibrate", w // 2, h // 2)

    while True:
        display = draw(img, regions, selected)
        cv2.imshow("Calibrate", display)
        key = cv2.waitKeyEx(0)  # waitKeyEx for arrow keys on Windows

        r = regions[selected]

        if key == 9:  # Tab
            selected = (selected + 1) % 2
            print(f"  Selected: {names[selected]}")
        elif key == 2490368:  # Up arrow (Windows)
            r["y"] = max(0, r["y"] - step)
        elif key == 2621440:  # Down arrow (Windows)
            r["y"] = min(h - r["h"], r["y"] + step)
        elif key == 2424832:  # Left arrow (Windows)
            r["x"] = max(0, r["x"] - step)
        elif key == 2555904:  # Right arrow (Windows)
            r["x"] = min(w - r["w"], r["x"] + step)
        elif key == ord('+') or key == ord('=') or key == ord('w'):
            r["w"] += step
        elif key == ord('-') or key == ord('s'):
            r["w"] = max(20, r["w"] - step)
        elif key == ord('d'):
            r["h"] += step
        elif key == ord('a'):
            r["h"] = max(20, r["h"] - step)
        elif key == ord('r'):
            print("  Refreshing screenshot...")
            new_img = take_screenshot()
            if new_img is not None:
                img = new_img
                print("  Done!")
        elif key == 13 or key == ord('q'):  # Enter or Q
            break

        # Print current coords
        c1, c2 = regions[0], regions[1]
        sys.stdout.write(f"\r  C1:({c1['x']},{c1['y']},{c1['w']}x{c1['h']})  C2:({c2['x']},{c2['y']},{c2['w']}x{c2['h']})  [{names[selected]}]   ")
        sys.stdout.flush()

    cv2.destroyAllWindows()

    c1, c2 = regions[0], regions[1]
    print(f"\n\n  Final coordinates:")
    print(f"    --card1 {c1['x']},{c1['y']},{c1['w']},{c1['h']}")
    print(f"    --card2 {c2['x']},{c2['y']},{c2['w']},{c2['h']}")
    print(f"\n  Run command:")
    print(f"    python card_collector.py --card1 {c1['x']},{c1['y']},{c1['w']},{c1['h']} --card2 {c2['x']},{c2['y']},{c2['w']},{c2['h']}")

    # Save annotated image
    out = img.copy()
    for i, reg in enumerate(regions):
        x, y, rw, rh = reg["x"], reg["y"], reg["w"], reg["h"]
        cv2.rectangle(out, (x, y), (x + rw, y + rh), colors[i], 3)
    cv2.imwrite(str(DATA_DIR / "calibrate.png"), out)
    for i, reg in enumerate(regions):
        card_crop = crop(img, reg)
        cv2.imwrite(str(DATA_DIR / f"calibrate_card{i+1}.png"), card_crop)



# ============ Main loop ============

def main():
    parser = argparse.ArgumentParser(description="Card Verifier - Hash OCR + Auto-Learn")
    parser.add_argument("--card1", type=str, default=None,
                        help="Card 1 region: x,y,w,h (default: 577,1795,70,100)")
    parser.add_argument("--card2", type=str, default=None,
                        help="Card 2 region: x,y,w,h (default: 657,1795,70,100)")
    parser.add_argument("--android-uid", type=int, default=13393284,
                        help="Android player UID (default: 13393284)")
    parser.add_argument("--calibrate", action="store_true",
                        help="Take screenshot and draw card regions for visual verification")
    args = parser.parse_args()

    card1_region = parse_region(args.card1) if args.card1 else DEFAULT_CARD1
    card2_region = parse_region(args.card2) if args.card2 else DEFAULT_CARD2

    DATA_DIR.mkdir(parents=True, exist_ok=True)

    if args.calibrate:
        calibrate(card1_region, card2_region)
        return

    COLLECT_DIR.mkdir(parents=True, exist_ok=True)
    DATA_DIR.mkdir(parents=True, exist_ok=True)

    print("=" * 50)
    print("  Card Verifier - Hash OCR + Auto-Learn")
    print("=" * 50)
    print(f"  Card 1: ({card1_region['x']},{card1_region['y']}) {card1_region['w']}x{card1_region['h']}")
    print(f"  Card 2: ({card2_region['x']},{card2_region['y']}) {card2_region['w']}x{card2_region['h']}")
    print(f"  Hash DB: {HASH_DB_PATH}")
    print(f"  Android UID: {args.android_uid}")

    load_hash_db()
    print("  Press Ctrl+C to stop\n")

    hand_count = 0
    wait_count = 0
    match_count = 0
    mismatch_count = 0
    total_compared = 0
    last_pc_hand = -1

    while True:
        try:
            img = take_screenshot()
            if img is None:
                time.sleep(1)
                continue

            if not detect_fold_visible(img):
                wait_count += 1
                sys.stdout.write(f"\r  Waiting for hand... ({wait_count}s)   ")
                sys.stdout.flush()
                time.sleep(1)
                continue

            wait_count = 0
            hand_count += 1
            ts = datetime.now().strftime("%H%M%S")

            # Crop cards
            c1 = crop(img, card1_region)
            c2 = crop(img, card2_region)

            # Hash-based detection
            det1 = detect_card(c1)
            det2 = detect_card(c2)

            label1 = fmt_card(det1)
            label2 = fmt_card(det2)

            # Save card images
            cv2.imwrite(str(COLLECT_DIR / f"hand{hand_count:03d}_{ts}_card1_{det1}.png"), c1)
            cv2.imwrite(str(COLLECT_DIR / f"hand{hand_count:03d}_{ts}_card2_{det2}.png"), c2)

            db_size = len(card_hash_db)
            print(f"\r  Hand #{hand_count:3d}: [{label1:>3s}] [{label2:>3s}] (db:{db_size})  → ALL-IN", end="")

            # Tap ALL-IN
            tap(*ALLIN_TAP)

            # Wait until buttons disappear (hand is over)
            for _ in range(30):
                time.sleep(1)
                img2 = take_screenshot()
                if img2 is None:
                    continue
                if not detect_fold_visible(img2):
                    break

            # Compare & Learn from PC showdown data
            time.sleep(2)
            pc_file = DATA_DIR / "pc_hero_cards.json"
            pc_match = ""
            if pc_file.exists():
                try:
                    with open(pc_file, "r") as pf:
                        pc = json.load(pf)
                    pc_hand_num = pc.get("hand", -1)
                    all_hands = pc.get("all_hands", [])

                    if pc_hand_num != last_pc_hand and all_hands:
                        last_pc_hand = pc_hand_num

                        # Find Android player's hand by UID
                        android_hand = None
                        uids_found = []
                        for h in all_hands:
                            if isinstance(h, dict):
                                uids_found.append(h.get("uid"))
                                if h.get("uid") == args.android_uid:
                                    android_hand = h.get("cards", [])
                                    break

                        if android_hand and len(android_hand) == 2:
                            expected = f"{fmt_card(android_hand[0])}{fmt_card(android_hand[1])}"

                            # Compare ORIGINAL detection (before learning)
                            if det1 == android_hand[0] and det2 == android_hand[1]:
                                # Already correct - no need to learn
                                pc_match = f" ✓ [{expected}]"
                                match_count += 1
                            elif det1 == "??" and det2 == "??":
                                # Unknown - learn
                                learn_cards(c1, c2, android_hand)
                                pc_match = f" [LEARN] {expected}"
                            else:
                                # Wrong detection - learn correct hash
                                learn_cards(c1, c2, android_hand)
                                pc_match = f" ✗→[LEARN] {expected}"
                                mismatch_count += 1
                        else:
                            pc_match = f" (no uid={args.android_uid} in {uids_found})"
                        total_compared += 1
                except Exception as e:
                    pc_match = f" (err: {e})"

            accuracy = f" ({match_count}/{total_compared})" if total_compared > 0 else ""
            print(f"{pc_match}{accuracy}")

        except KeyboardInterrupt:
            save_hash_db()
            print(f"\n\n  Stopped after {hand_count} hands.")
            print(f"  Hash DB: {len(card_hash_db)} cards learned")
            if total_compared > 0:
                acc = match_count / total_compared * 100
                print(f"  Accuracy: {match_count}/{total_compared} ({acc:.0f}%) matched with PC")
            break


if __name__ == "__main__":
    main()
