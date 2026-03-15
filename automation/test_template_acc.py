import cv2
import re
import numpy as np
import time
from collections import defaultdict
from pathlib import Path

SAMPLES = Path(r"d:\aof_bot\automation\data\card_samples")

def mse(imageA, imageB):
    err = np.sum((imageA.astype("float") - imageB.astype("float")) ** 2)
    err /= float(imageA.shape[0] * imageA.shape[1] * imageA.shape[2])
    return err

def run():
    pattern = re.compile(r'card([12])_([A-Za-z0-9]{2})\.png$')
    
    samples = defaultdict(lambda: defaultdict(list))
    
    for f in list(SAMPLES.glob("*_card*_*.png")):
        m = pattern.search(f.name)
        if not m:
            continue
        slot = m.group(1)
        card_name = m.group(2).capitalize()
        # skip bad labels like "th" instead of "Th", wait, capitalize makes "Th"
        if len(card_name) == 2 and card_name[1].islower():
            samples[slot][card_name].append(cv2.imread(str(f)))
            
    print(f"Loaded Slot 1: {len(samples['1'])} labels")
    print(f"Loaded Slot 2: {len(samples['2'])} labels")
    
    # Pick one template for each label/slot
    templates = {"1": {}, "2": {}}
    for slot in ["1", "2"]:
        for label, imgs in samples[slot].items():
            img = imgs[0][0:55, 0:60]
            templates[slot][label] = img
        
    correct = 0
    total = 0
    t0 = time.time()
    
    for slot in ["1", "2"]:
        for label, imgs in samples[slot].items():
            for full_img in imgs:
                total += 1
                img = full_img[0:55, 0:60]
                
                best_label = None
                best_err = float('inf')
                
                for t_label, t_img in templates[slot].items():
                    if t_img.shape != img.shape: continue
                    err = mse(img, t_img)
                    if err < best_err:
                        best_err = err
                        best_label = t_label
                        
                if best_label == label:
                    correct += 1
                 
    t1 = time.time()
    print(f"Split BGR MSE Correct: {correct}/{total} ({correct/total*100:.1f}%) in {t1-t0:.2f}s")
    
run()
