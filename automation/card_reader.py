"""Card reader for PPPoker Android via ADB screenshots.

Reads hero's hole cards from the screen using OpenCV template matching.
Also detects buttons (Fold/AllIn), player count, and position.

Coordinate system based on 1080x2400 resolution (PPPoker Android).
"""

import cv2
import numpy as np
from pathlib import Path
from typing import Optional, Tuple
import subprocess
import json

# Paths
ASSETS_DIR = Path(__file__).parent / "assets" / "cards"
CONFIG_PATH = Path(__file__).parent / "data" / "card_reader_config.json"
DATA_DIR = Path(__file__).parent / "data"

# ADB path
ADB_PATH = r"C:\Users\Owner\Desktop\scrcpy-win64-v3.3.3\adb.exe"

# Default config (calibrated for 1080x2400 PPPoker Android)
DEFAULT_CONFIG = {
    "screen_width": 1080,
    "screen_height": 2400,
    # Hero's two cards (absolute pixel coordinates)
    "card1": {"x": 590, "y": 1755, "w": 95, "h": 155},
    "card2": {"x": 685, "y": 1755, "w": 95, "h": 155},
    # Buttons
    "fold_button":  {"x": 30,  "y": 2280, "w": 340, "h": 90},
    "allin_button": {"x": 400, "y": 2280, "w": 340, "h": 90},
    # Dealer button marker (D icon)
    "dealer_marker": {"x": 380, "y": 1730, "w": 50, "h": 50},
    # Opponent seat regions (check occupied vs "Empty")
    "seats": [
        {"name": "top",   "x": 420, "y": 390, "w": 200, "h": 120},
        {"name": "left",  "x": 30,  "y": 1000, "w": 130, "h": 90},
        {"name": "right", "x": 920, "y": 1000, "w": 130, "h": 90},
    ],
}


class CardReader:
    """Read cards from PPPoker Android screenshots."""

    def __init__(self, adb_path: str = ADB_PATH):
        self.adb_path = adb_path
        self.config = self._load_config()
        self.rank_templates = {}
        self._load_rank_templates()

    # ------------------------------------------------------------------ config
    def _load_config(self) -> dict:
        if CONFIG_PATH.exists():
            with open(CONFIG_PATH) as f:
                saved = json.load(f)
                cfg = DEFAULT_CONFIG.copy()
                cfg.update(saved)
                return cfg
        return DEFAULT_CONFIG.copy()

    def save_config(self):
        CONFIG_PATH.parent.mkdir(parents=True, exist_ok=True)
        with open(CONFIG_PATH, "w") as f:
            json.dump(self.config, f, indent=2)
        print(f"  [CardReader] Config saved to {CONFIG_PATH}")

    # ---------------------------------------------------------- rank templates
    def _load_rank_templates(self):
        """Load rank template images (rank_A.png .. rank_2.png) from assets."""
        if not ASSETS_DIR.exists():
            return
        for f in ASSETS_DIR.glob("rank_*.png"):
            rank = f.stem.replace("rank_", "").upper()
            if rank == "10":
                rank = "T"
            tpl = cv2.imread(str(f), cv2.IMREAD_GRAYSCALE)
            if tpl is not None:
                self.rank_templates[rank] = tpl
        if self.rank_templates:
            print(f"  [CardReader] Loaded {len(self.rank_templates)} rank templates")

    # ------------------------------------------------------------ ADB helpers
    def take_screenshot(self) -> Optional[np.ndarray]:
        """Capture ADB screenshot → return OpenCV BGR image."""
        try:
            remote = "/sdcard/aof_screen.png"
            local = str(DATA_DIR / "screenshot.png")
            subprocess.run([self.adb_path, "shell", f"screencap -p {remote}"],
                           capture_output=True, timeout=5)
            subprocess.run([self.adb_path, "pull", remote, local],
                           capture_output=True, timeout=5)
            subprocess.run([self.adb_path, "shell", f"rm {remote}"],
                           capture_output=True, timeout=5)
            return cv2.imread(local)
        except Exception as e:
            print(f"  [CardReader] Screenshot failed: {e}")
            return None

    # --------------------------------------------------------- region helpers
    def _crop(self, img: np.ndarray, region: dict) -> np.ndarray:
        """Crop an image region with auto-scaling when resolution differs."""
        h, w = img.shape[:2]
        sx = w / self.config["screen_width"]
        sy = h / self.config["screen_height"]
        x = int(region["x"] * sx)
        y = int(region["y"] * sy)
        rw = int(region["w"] * sx)
        rh = int(region["h"] * sy)
        return img[y:y+rh, x:x+rw]

    # --------------------------------------------------------- button detect
    def detect_buttons(self, img: np.ndarray) -> dict:
        """Check if Fold / AllIn buttons are visible."""
        fold_roi = self._crop(img, self.config["fold_button"])
        fold_hsv = cv2.cvtColor(fold_roi, cv2.COLOR_BGR2HSV)
        red1 = cv2.inRange(fold_hsv, (0, 80, 80), (10, 255, 255))
        red2 = cv2.inRange(fold_hsv, (170, 80, 80), (180, 255, 255))
        red_ratio = (cv2.countNonZero(red1) + cv2.countNonZero(red2)) / max(fold_roi.size // 3, 1)

        allin_roi = self._crop(img, self.config["allin_button"])
        allin_hsv = cv2.cvtColor(allin_roi, cv2.COLOR_BGR2HSV)
        yellow = cv2.inRange(allin_hsv, (15, 60, 100), (35, 255, 255))
        yellow_ratio = cv2.countNonZero(yellow) / max(allin_roi.size // 3, 1)

        return {
            "fold_visible": red_ratio > 0.10,
            "allin_visible": yellow_ratio > 0.10,
            "needs_action": red_ratio > 0.10 and yellow_ratio > 0.10,
        }

    # -------------------------------------------------------------- suit detect
    def _detect_suit(self, card_roi: np.ndarray) -> str:
        """Detect suit from the card image by dominant color in the icon area."""
        hsv = cv2.cvtColor(card_roi, cv2.COLOR_BGR2HSV)
        h, w = card_roi.shape[:2]
        # Suit icon is roughly in the middle-left area of the card
        area = hsv[int(h * 0.35):int(h * 0.65), 5:int(w * 0.5)]
        if area.size == 0:
            return "?"
        mh = np.mean(area[:, :, 0])
        ms = np.mean(area[:, :, 1])
        # Classify (calibrated from real PPPoker card samples)
        if ms < 40:
            return "s"  # black → spade
        if mh < 10 or mh > 170:
            return "h"  # red → heart
        if 55 < mh < 100:
            return "d"  # blue → diamond (H≈60-73)
        if 30 < mh <= 55:
            return "c"  # green → club (H≈37-50)
        return "?"

    # -------------------------------------------------------------- rank detect
    def _detect_rank(self, card_roi: np.ndarray) -> str:
        """Detect rank via template matching (needs rank_*.png templates)."""
        if not self.rank_templates:
            return "?"
        h, w = card_roi.shape[:2]
        
        # Rank is clearly visible in the top-left area. 
        # Using a slightly larger crop to ensure the entire template fits 
        # (Template size is ~ 39x44)
        rank_area = card_roi[3:55, 3:55]
        if rank_area.size == 0 or rank_area.shape[0] < 20 or rank_area.shape[1] < 20:
             return "?"
             
        gray = cv2.cvtColor(rank_area, cv2.COLOR_BGR2GRAY)
        
        # Simple thresholding with 120 is very stable for both glowing and dark folded cards
        _, binary = cv2.threshold(gray, 120, 255, cv2.THRESH_BINARY)
        
        best, best_score = "?", 0.0
        for rank, tpl in self.rank_templates.items():
            t = tpl
            # Ensure template is smaller than target to use matchTemplate
            if t.shape[0] > binary.shape[0] or t.shape[1] > binary.shape[1]:
                s = min(binary.shape[0] / t.shape[0], binary.shape[1] / t.shape[1]) * 0.95
                t = cv2.resize(t, None, fx=s, fy=s)
                
            res = cv2.matchTemplate(binary, t, cv2.TM_CCOEFF_NORMED)
            _, mx, _, _ = cv2.minMaxLoc(res)
            if mx > best_score:
                best_score = mx
                best = rank
                
        return best if best_score > 0.40 else "?"

    # ---------------------------------------------------------------- read
    def read_cards(self, img: np.ndarray) -> Optional[Tuple[str, str]]:
        """Read hero's two hole cards.

        Returns ("6d", "Qc") or None if detection fails.
        """
        card1_roi = self._crop(img, self.config["card1"])
        card2_roi = self._crop(img, self.config["card2"])

        suit1 = self._detect_suit(card1_roi)
        suit2 = self._detect_suit(card2_roi)
        rank1 = self._detect_rank(card1_roi)
        rank2 = self._detect_rank(card2_roi)

        if "?" in (rank1, rank2, suit1, suit2):
            print(f"  [CardReader] Partial: {rank1}{suit1} {rank2}{suit2}")
            # Save debug crops
            dbg = DATA_DIR / "debug"
            dbg.mkdir(exist_ok=True)
            cv2.imwrite(str(dbg / "card1.png"), card1_roi)
            cv2.imwrite(str(dbg / "card2.png"), card2_roi)
            print(f"  [CardReader] Debug images → {dbg}")
            return None

        c1, c2 = rank1 + suit1, rank2 + suit2
        print(f"  [CardReader] Detected: {c1} {c2}")
        return (c1, c2)

    # --------------------------------------------------------- player count
    def count_players(self, img: np.ndarray) -> int:
        """Count active players (1=hero + occupied seats)."""
        count = 1
        for seat in self.config["seats"]:
            roi = self._crop(img, seat)
            gray = cv2.cvtColor(roi, cv2.COLOR_BGR2GRAY)
            if np.std(gray) > 30:  # occupied seats have avatar textures
                count += 1
        return min(count, 4)

    # ---------------------------------------------------------- calibration
    def calibrate(self, img: np.ndarray):
        """Run calibration: extract card crops, test button detection."""
        ASSETS_DIR.mkdir(parents=True, exist_ok=True)
        print("=== Card Reader Calibration ===")
        print(f"Image: {img.shape[1]}x{img.shape[0]}")

        # Buttons
        btns = self.detect_buttons(img)
        print(f"Fold visible:  {btns['fold_visible']}")
        print(f"AllIn visible: {btns['allin_visible']}")
        print(f"Needs action:  {btns['needs_action']}")

        # Players
        print(f"Players: {self.count_players(img)}")

        # Cards
        c1 = self._crop(img, self.config["card1"])
        c2 = self._crop(img, self.config["card2"])
        cv2.imwrite(str(ASSETS_DIR / "card1_sample.png"), c1)
        cv2.imwrite(str(ASSETS_DIR / "card2_sample.png"), c2)
        print(f"Suit 1: {self._detect_suit(c1)}")
        print(f"Suit 2: {self._detect_suit(c2)}")
        print(f"Rank 1: {self._detect_rank(c1)}")
        print(f"Rank 2: {self._detect_rank(c2)}")
        print(f"\nCard crops saved to {ASSETS_DIR}")

    def extract_rank_template(self, img: np.ndarray, card_index: int, rank_char: str):
        """Extract a rank template from a known card for future matching.

        card_index: 1 or 2  (which card to extract from)
        rank_char:  'A','K','Q','J','T','9'...'2'
        """
        ASSETS_DIR.mkdir(parents=True, exist_ok=True)
        cfg_key = f"card{card_index}"
        roi = self._crop(img, self.config[cfg_key])
        h, w = roi.shape[:2]
        rank_area = roi[5:int(h * 0.35), 3:int(w * 0.5)]
        gray = cv2.cvtColor(rank_area, cv2.COLOR_BGR2GRAY)
        out = ASSETS_DIR / f"rank_{rank_char}.png"
        cv2.imwrite(str(out), gray)
        print(f"  Saved rank template: {out}")


if __name__ == "__main__":
    import argparse
    parser = argparse.ArgumentParser()
    parser.add_argument("--calibrate", action="store_true")
    parser.add_argument("--screenshot", type=str)
    parser.add_argument("--extract-rank", nargs=3, metavar=("SCREENSHOT", "CARD_IDX", "RANK"),
                        help="Extract rank template, e.g. --extract-rank screenshot.png 1 6")
    args = parser.parse_args()

    reader = CardReader()

    if args.extract_rank:
        img = cv2.imread(args.extract_rank[0])
        reader.extract_rank_template(img, int(args.extract_rank[1]), args.extract_rank[2])
    elif args.calibrate:
        img = cv2.imread(args.screenshot) if args.screenshot else reader.take_screenshot()
        if img is not None:
            reader.calibrate(img)
    else:
        img = cv2.imread(args.screenshot) if args.screenshot else reader.take_screenshot()
        if img is not None:
            cards = reader.read_cards(img)
            btns = reader.detect_buttons(img)
            print(f"Cards: {cards}  Buttons: {btns}")
