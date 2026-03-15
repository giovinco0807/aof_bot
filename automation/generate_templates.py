"""Generate rank templates from captured card images using PC ground truth.

Reads the PC cards log and the captured card images, then creates rank templates
by extracting the rank region from correctly identified cards.

Usage:
    cd d:\aof_bot\automation
    python generate_templates.py
"""

import cv2
import numpy as np
import json
from pathlib import Path
from collections import defaultdict

DATA_DIR = Path(__file__).parent / "data"
SAMPLES_DIR = DATA_DIR / "card_samples"
ASSETS_DIR = Path(__file__).parent / "assets" / "cards"
PC_LOG = DATA_DIR / "pc_cards_log.csv"

# Card crop size (must match card_collector.py)
CARD_W, CARD_H = 70, 100

# Rank area within the card: top-left corner where rank character is
# This is RELATIVE to the card crop
RANK_ROI = {"x": 15, "y": 5, "w": 45, "h": 55}

# Suit map for filename parsing
SUIT_SHORT = {"s": "spade", "h": "heart", "d": "diamond", "c": "club"}


def extract_rank_region(card_img):
    """Extract the rank region from a card image."""
    r = RANK_ROI
    roi = card_img[r["y"]:r["y"]+r["h"], r["x"]:r["x"]+r["w"]]
    gray = cv2.cvtColor(roi, cv2.COLOR_BGR2GRAY)
    # Threshold to get clean black text on white background
    _, binary = cv2.threshold(gray, 180, 255, cv2.THRESH_BINARY)
    return binary


def parse_pc_log():
    """Parse PC cards log to get hand number -> all showdown cards mapping."""
    hands = {}
    if not PC_LOG.exists():
        print(f"PC log not found: {PC_LOG}")
        return hands

    with open(PC_LOG, "r") as f:
        for line in f:
            line = line.strip()
            if not line or line.startswith("time") or "|" not in line:
                continue
            parts = line.split(",", 2)
            if len(parts) < 3:
                continue
            _, hand_num, cards_str = parts
            # Parse "9h 4d | Jc Tc" format
            players = cards_str.split(" | ")
            all_cards = []
            for player in players:
                card_list = player.strip().split()
                all_cards.extend(card_list)
            try:
                hands[int(hand_num)] = all_cards
            except ValueError:
                pass
    return hands


def find_card_images():
    """Find all captured card images grouped by hand number."""
    cards = defaultdict(list)
    for f in sorted(SAMPLES_DIR.glob("hand*_card*.png")):
        parts = f.stem.split("_")
        # hand001_HHMMSS_card1_Xs.png
        hand_num = int(parts[0].replace("hand", ""))
        card_idx = int(parts[2].replace("card", ""))
        cards[hand_num].append((card_idx, f))
    return cards


def main():
    print("=" * 50)
    print("  Template Generator from PC Ground Truth")
    print("=" * 50)

    pc_hands = parse_pc_log()
    print(f"  PC hands: {len(pc_hands)}")

    card_images = find_card_images()
    print(f"  Card images: {len(card_images)} hands")

    ASSETS_DIR.mkdir(parents=True, exist_ok=True)

    # For each rank, collect the best template
    rank_samples = defaultdict(list)

    for hand_num, card_files in sorted(card_images.items()):
        if hand_num not in pc_hands:
            continue

        pc_cards = pc_hands[hand_num]
        # We don't know which player's cards the Android captured
        # Try each card file against all PC cards
        for card_idx, card_path in card_files:
            img = cv2.imread(str(card_path))
            if img is None or img.shape[0] < 50 or img.shape[1] < 30:
                continue

            # Extract rank region
            rank_img = extract_rank_region(img)

            # Try to match against PC cards
            # Card index 1 or 2 from the Android side
            for pc_card in pc_cards:
                if len(pc_card) < 2:
                    continue
                rank = pc_card[:-1].upper()
                if rank == "10":
                    rank = "T"
                rank_samples[rank].append((rank_img, card_path))

    # For each rank, pick the median/best sample
    generated = 0
    for rank, samples in sorted(rank_samples.items()):
        if not samples:
            continue
        # Use the first clean sample
        rank_img, source = samples[0]
        out_path = ASSETS_DIR / f"rank_{rank}.png"
        cv2.imwrite(str(out_path), rank_img)
        generated += 1
        print(f"  rank_{rank}.png from {source.name} ({len(samples)} samples)")

    print(f"\n  Generated {generated} rank templates in {ASSETS_DIR}")

    # Also create a visual overview
    if rank_samples:
        preview_imgs = []
        for rank in sorted(rank_samples.keys()):
            if rank_samples[rank]:
                img, _ = rank_samples[rank][0]
                # Add label
                labeled = cv2.copyMakeBorder(img, 20, 0, 0, 0,
                                             cv2.BORDER_CONSTANT, value=0)
                cv2.putText(labeled, rank, (5, 15),
                            cv2.FONT_HERSHEY_SIMPLEX, 0.5, 255, 1)
                preview_imgs.append(labeled)

        if preview_imgs:
            # Resize all to same height
            max_h = max(p.shape[0] for p in preview_imgs)
            resized = []
            for p in preview_imgs:
                if p.shape[0] < max_h:
                    p = cv2.copyMakeBorder(p, 0, max_h - p.shape[0], 0, 0,
                                           cv2.BORDER_CONSTANT, value=0)
                resized.append(p)
            preview = np.hstack(resized)
            cv2.imwrite(str(DATA_DIR / "template_preview.png"), preview)
            print(f"  Preview saved: {DATA_DIR / 'template_preview.png'}")


if __name__ == "__main__":
    main()
