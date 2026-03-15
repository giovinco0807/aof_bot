import cv2
import re
import time
import sys
from pathlib import Path

# Add automation directory to path
sys.path.insert(0, r"d:\aof_bot\automation")
from card_collector import detect_card as hash_detect, load_hash_db
from card_reader import CardReader

SAMPLES = Path(r"d:\aof_bot\automation\data\card_samples")

def run():
    pattern = re.compile(r'card[12]_([A-Za-z0-9]{2})\.png$')
    
    samples = []
    for f in list(SAMPLES.glob("*_card*_*.png")):
        m = pattern.search(f.name)
        if not m:
            continue
        card_name = m.group(1).capitalize()
        if len(card_name) == 2 and card_name[1].islower():
            samples.append((f, card_name))
            
    print(f"Loaded {len(samples)} validly labeled samples.")
    
    reader = CardReader()
    
    # Pre-load hash DB
    load_hash_db()
    
    hash_correct = 0
    reader_correct = 0
    
    t0 = time.time()
    for f, label in samples:
        img = cv2.imread(str(f))
        if img is None: continue
        
        det_hash = hash_detect(img)
        if det_hash.capitalize() == label:
            hash_correct += 1
            
    t1 = time.time()
    
    for f, label in samples:
        img = cv2.imread(str(f))
        if img is None: continue
        
        s = reader._detect_suit(img)
        r = reader._detect_rank(img)
        det_read = f"{r}{s}".capitalize()
        if det_read == label:
            reader_correct += 1
        else:
             print(f"Reader mismatch: {label} vs {det_read} ({f.name})")
            
    t2 = time.time()
    
    print(f"Hash Correct: {hash_correct}/{len(samples)} ({t1-t0:.2f}s)")
    print(f"Reader Correct: {reader_correct}/{len(samples)} ({t2-t1:.2f}s)")

if __name__ == "__main__":
    run()
