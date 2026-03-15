import cv2
import re
import numpy as np
import time
import sys
from pathlib import Path

sys.path.insert(0, r"d:\aof_bot\automation")
from card_reader import CardReader

SAMPLES = Path(r"d:\aof_bot\automation\data\card_samples")

def run():
    pattern = re.compile(r'card([12])_([A-Za-z0-9]{2})\.png$')
    
    samples = []
    for f in list(SAMPLES.glob("*_card*_*.png")):
        m = pattern.search(f.name)
        if not m:
            continue
        card_name = m.group(2).capitalize()
        if len(card_name) == 2 and card_name[1].islower():
            samples.append((f, card_name))
            
    print(f"Loaded {len(samples)} validly labeled samples.")
    
    reader = CardReader()
    
    correct = 0
    t0 = time.time()
    for f, label in samples:
        img = cv2.imread(str(f))
        if img is None: continue
        
        # We must use card1 or card2 crops! But sample images ARE already cropped to 70x100!
        # wait! Are the sample images 70x100? Yes!
        # _detect_rank expects the 70x100 image.
        
        s = reader._detect_suit(img)
        r = reader._detect_rank(img)
        
        if s == '?' or r == '?':
            # print(f"Failed to detect: {label} -> {r}{s}")
            pass
            
        det_read = f"{r}{s}".capitalize()
        if det_read == label:
            correct += 1
        elif correct < 20: # Just print a few of them
             # Let's see the ranks
             print(f"Mismatch [{f.name}]: expected {label}, got rank: {r}, suit: {s}")
             
    t2 = time.time()
    print(f"Reader Correct: {correct}/{len(samples)} ({correct/len(samples)*100:.1f}%) in {t2-t0:.2f}s")

if __name__ == "__main__":
    run()
