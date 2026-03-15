"""Screen capture and card/text recognition for PPPoker AoF tables."""

import cv2
import numpy as np
from pathlib import Path
from typing import Optional, Tuple, List
import mss
import mss.tools

from config import Region


class ScreenCapture:
    """Captures the PPPoker window screen."""

    def __init__(self):
        self.sct = mss.mss()

    def capture_region(self, region: Region) -> np.ndarray:
        """Capture a specific screen region as a numpy array (BGR)."""
        monitor = {
            "left": region.x,
            "top": region.y,
            "width": region.w,
            "height": region.h,
        }
        img = self.sct.grab(monitor)
        return np.array(img)[:, :, :3]  # Drop alpha channel

    def capture_full_screen(self) -> np.ndarray:
        """Capture the full primary monitor."""
        monitor = self.sct.monitors[1]
        img = self.sct.grab(monitor)
        return np.array(img)[:, :, :3]


class CardRecognizer:
    """Recognizes poker cards using template matching."""

    RANKS = "23456789TJQKA"
    SUITS = "cdhs"

    def __init__(self, template_dir: str = "tablemaps"):
        self.template_dir = Path(template_dir)
        self.templates = {}
        self._load_templates()

    def _load_templates(self):
        """Load card template images from the template directory."""
        for rank in self.RANKS:
            for suit in self.SUITS:
                name = f"{rank}{suit}"
                path = self.template_dir / f"{name}.png"
                if path.exists():
                    self.templates[name] = cv2.imread(str(path))

        if not self.templates:
            print(f"Warning: No card templates found in {self.template_dir}")
            print("Run the calibration tool to create card templates.")

    def recognize_card(self, img: np.ndarray) -> Optional[str]:
        """Recognize a single card from an image region.

        Args:
            img: BGR image of the card region

        Returns:
            Card string like "Ac" or None if not recognized
        """
        if not self.templates:
            return None

        best_match = None
        best_score = 0.0
        threshold = 0.8  # Minimum match confidence

        for name, template in self.templates.items():
            # Resize template to match the input if needed
            if template.shape[0] != img.shape[0] or template.shape[1] != img.shape[1]:
                template = cv2.resize(template, (img.shape[1], img.shape[0]))

            result = cv2.matchTemplate(img, template, cv2.TM_CCOEFF_NORMED)
            _, max_val, _, _ = cv2.minMaxLoc(result)

            if max_val > best_score and max_val > threshold:
                best_score = max_val
                best_match = name

        return best_match


class TextRecognizer:
    """OCR for reading text (stack sizes, player names) from screen regions."""

    def __init__(self):
        self._reader = None

    @property
    def reader(self):
        """Lazy-load easyocr reader."""
        if self._reader is None:
            import easyocr
            self._reader = easyocr.Reader(["en"], gpu=False)
        return self._reader

    def read_number(self, img: np.ndarray) -> Optional[float]:
        """Read a number from an image region (e.g., stack size)."""
        # Preprocess: convert to grayscale, threshold
        gray = cv2.cvtColor(img, cv2.COLOR_BGR2GRAY)
        _, thresh = cv2.threshold(gray, 127, 255, cv2.THRESH_BINARY)

        results = self.reader.readtext(thresh, detail=0)
        for text in results:
            # Clean up the text and try to parse as a number
            cleaned = text.strip().replace(",", "").replace(" ", "")
            try:
                return float(cleaned)
            except ValueError:
                continue
        return None

    def read_text(self, img: np.ndarray) -> str:
        """Read text from an image region (e.g., player name)."""
        gray = cv2.cvtColor(img, cv2.COLOR_BGR2GRAY)
        results = self.reader.readtext(gray, detail=0)
        return " ".join(results).strip()


class ActionDetector:
    """Detects game state (whose turn, button locations, etc.)."""

    def __init__(self):
        pass

    def is_button_visible(self, img: np.ndarray, button_type: str = "allin") -> bool:
        """Check if the fold/allin button is visible (indicating it's our turn).

        Uses color detection: buttons typically have distinct colors.
        """
        hsv = cv2.cvtColor(img, cv2.COLOR_BGR2HSV)

        if button_type == "allin":
            # All-in button is typically red/orange
            lower = np.array([0, 100, 100])
            upper = np.array([20, 255, 255])
        else:  # fold
            # Fold button is typically gray/blue
            lower = np.array([90, 50, 50])
            upper = np.array([130, 255, 255])

        mask = cv2.inRange(hsv, lower, upper)
        pixel_count = cv2.countNonZero(mask)
        total_pixels = img.shape[0] * img.shape[1]

        # Button is visible if enough colored pixels are detected
        return pixel_count > total_pixels * 0.1

    def detect_player_action(self, img: np.ndarray) -> Optional[str]:
        """Detect if a player's action indicator shows Fold or AllIn.

        Returns 'F', 'A', or None.
        """
        gray = cv2.cvtColor(img, cv2.COLOR_BGR2GRAY)
        mean_brightness = np.mean(gray)

        # Very dim = empty/no action yet
        if mean_brightness < 30:
            return None

        # Check for "Fold" text or folded card visual (darker area)
        if mean_brightness < 100:
            return "F"

        # Otherwise, could be all-in indicator
        return "A"
