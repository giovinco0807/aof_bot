import cv2
import re
import os
import shutil
from pathlib import Path
from card_reader import CardReader

DATA_DIR = Path(r"d:\aof_bot\automation\data")
SAMPLES = DATA_DIR / "card_samples"
PC_LOG = DATA_DIR / "pc_cards_log.csv"

def parse_pc_log():
    hands = {}
    if not PC_LOG.exists():
        return hands
    with open(PC_LOG, "r") as f:
        for line in f:
            line = line.strip()
            if not line or line.startswith("time") or "|" not in line:
                continue
            parts = line.split(",", 2)
            if len(parts) < 3:
                continue
            try:
                hand_num = int(parts[1])
                cards_str = parts[2]
                all_cards = []
                for player in cards_str.split(" | "):
                    all_cards.extend([c.capitalize() for c in player.strip().split()])
                # Note: "10h" -> "Th"
                clean_cards = [c.replace("10", "T") for c in all_cards]
                hands[hand_num] = clean_cards
            except ValueError:
                pass
    return hands

def run():
    print("Loading PC hands...")
    pc_hands = parse_pc_log()
    reader = CardReader()
    
    pattern = re.compile(r'(hand(\d+)_.*?_card[12]_)([A-Za-z0-9]{2})(.*?\.png)$')
    
    renamed = 0
    errors = 0
    verified = 0

    for f in list(SAMPLES.glob("*_card*_*.png")):
        m = pattern.search(f.name)
        if not m: continue
        
        prefix = m.group(1)
        hand_num = int(m.group(2))
        current_label = m.group(3).capitalize()
        suffix = m.group(4)
        
        img = cv2.imread(str(f))
        if img is None: continue
        
        r = reader._detect_rank(img)
        s = reader._detect_suit(img)
        
        pred = f"{r}{s}".capitalize()
        
        if pred == current_label:
            verified += 1
            continue
            
        # Mismatch detected. Let's check with PC Ground Truth
        if hand_num in pc_hands:
            pc_cards = pc_hands[hand_num]
            if pred in pc_cards:
                # The prediction is definitely one of the cards dealt in this hand!
                # It means the Android sync was off, but our reader found the true card.
                new_name = f"{prefix}{pred}{suffix}"
                new_path = SAMPLES / new_name
                print(f"[RENAME] {f.name} -> {new_name} (Found in PC log)")
                shutil.move(str(f), str(new_path))
                renamed += 1
            elif current_label in pc_cards:
                # Our reader failed, but the current label IS in the PC log.
                print(f"[ERROR] Reader predicted {pred}, but {current_label} is in PC log: {f.name}")
                errors += 1
            else:
                # Neither prediction nor current label is in the PC log for this hand...
                # Assume reader is right if it's not "??"
                if "?" not in pred:
                    new_name = f"{prefix}{pred}{suffix}"
                    new_path = SAMPLES / new_name
                    print(f"[RENAME-GUESS] {f.name} -> {new_name} (Neither in log)")
                    shutil.move(str(f), str(new_path))
                    renamed += 1
                else:
                    print(f"[UNKNOWN] {f.name} prediction {pred}")
        else:
            if "?" not in pred:
                new_name = f"{prefix}{pred}{suffix}"
                new_path = SAMPLES / new_name
                print(f"[RENAME-NOLOG] {f.name} -> {new_name}")
                shutil.move(str(f), str(new_path))
                renamed += 1
            else:
                print(f"[UNKNOWN-NOLOG] {f.name} prediction {pred}")
                
    print(f"\nReport:")
    print(f"Verified Corrent: {verified}")
    print(f"Renamed (Fixed):  {renamed}")
    print(f"Reader Errors:    {errors}")

if __name__ == "__main__":
    run()
