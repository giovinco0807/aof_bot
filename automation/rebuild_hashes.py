"""Rebuild card_hashes.json from labeled card_samples images."""
import cv2, json, re
from pathlib import Path
from collections import defaultdict

DATA = Path(r"d:\aof_bot\automation\data")
SAMPLES = DATA / "card_samples"

def card_hash(img):
    g = cv2.cvtColor(img, cv2.COLOR_BGR2GRAY)
    s = cv2.resize(g, (16, 16), interpolation=cv2.INTER_AREA)
    bits = (s > s.mean()).flatten()
    h = 0
    for b in bits:
        h = (h << 1) | int(b)
    return format(h, '064x')

db = {"1": {}, "2": {}}
count = 0
pattern = re.compile(r'card([12])_([A-Za-z0-9]{2,3})\.png$')

for f in sorted(SAMPLES.glob("*_card*_*.png")):
    m = pattern.search(f.name)
    if not m:
        continue
    slot = m.group(1)
    card_name = m.group(2).capitalize()
    if len(card_name) != 2 or not card_name[1].islower():
        continue
    
    img = cv2.imread(str(f))
    if img is None:
        continue
    
    h = card_hash(img)
    if h not in db[slot]:
        db[slot][h] = card_name
        count += 1

# Save
with open(str(DATA / "card_hashes.json"), "w") as f:
    json.dump(db, f, indent=2)

print(f"Rebuilt: {count} unique hashes from {len(list(SAMPLES.glob('*_card*_*.png')))} samples.")
