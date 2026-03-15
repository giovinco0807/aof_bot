"""Card recognition for PPPoker using color-based suit detection + OCR rank.

PPPoker card design:
  - Diamonds (♦) = blue background
  - Hearts (♥) = red background
  - Spades (♠) = dark/black background
  - Clubs (♣) = green background
  - Rank shown as large text in center of card (A, K, Q, J, 10, 9, ..., 2)
"""

import cv2
import numpy as np
from typing import Optional, Tuple


# HSV ranges for each suit's background color (OpenCV: H=0-179, S=0-255, V=0-255)
# Tuned from PPPoker screenshots
SUIT_COLORS = {
    "d": {  # Diamonds = blue
        "lower": np.array([95, 60, 60]),
        "upper": np.array([135, 255, 255]),
    },
    "h": {  # Hearts = red
        "lower1": np.array([0, 70, 70]),
        "upper1": np.array([12, 255, 255]),
        "lower2": np.array([165, 70, 70]),
        "upper2": np.array([179, 255, 255]),
    },
    "c": {  # Clubs = green
        "lower": np.array([35, 50, 50]),
        "upper": np.array([85, 255, 255]),
    },
    "s": {  # Spades = dark/black (low saturation, low value)
        "max_saturation": 60,
        "max_value": 120,
    },
}

# Rank characters
RANKS = "A23456789TJQK"


def detect_suit(card_img: np.ndarray) -> Optional[str]:
    """Detect card suit from background color.

    Args:
        card_img: BGR image of the card region.

    Returns:
        Suit character ('s', 'h', 'd', 'c') or None if undetected.
    """
    if card_img is None or card_img.size == 0:
        return None

    hsv = cv2.cvtColor(card_img, cv2.COLOR_BGR2HSV)

    h, w = card_img.shape[:2]
    # Sample center 50% of card to avoid edges
    cy1, cy2 = h // 4, 3 * h // 4
    cx1, cx2 = w // 4, 3 * w // 4
    center = hsv[cy1:cy2, cx1:cx2]
    total_pixels = center.shape[0] * center.shape[1]

    if total_pixels == 0:
        return None

    scores = {}

    # Blue (diamonds)
    mask_d = cv2.inRange(center, SUIT_COLORS["d"]["lower"], SUIT_COLORS["d"]["upper"])
    scores["d"] = cv2.countNonZero(mask_d) / total_pixels

    # Red (hearts) - wraps around hue 0/180
    mask_h1 = cv2.inRange(center, SUIT_COLORS["h"]["lower1"], SUIT_COLORS["h"]["upper1"])
    mask_h2 = cv2.inRange(center, SUIT_COLORS["h"]["lower2"], SUIT_COLORS["h"]["upper2"])
    scores["h"] = (cv2.countNonZero(mask_h1) + cv2.countNonZero(mask_h2)) / total_pixels

    # Green (clubs)
    mask_c = cv2.inRange(center, SUIT_COLORS["c"]["lower"], SUIT_COLORS["c"]["upper"])
    scores["c"] = cv2.countNonZero(mask_c) / total_pixels

    # Dark/black (spades) - low sat AND low value
    s_channel = center[:, :, 1]
    v_channel = center[:, :, 2]
    dark_mask = (s_channel < SUIT_COLORS["s"]["max_saturation"]) & \
                (v_channel < SUIT_COLORS["s"]["max_value"])
    scores["s"] = np.count_nonzero(dark_mask) / total_pixels

    # Return suit with highest score if above threshold
    best_suit = max(scores, key=scores.get)
    if scores[best_suit] > 0.15:
        return best_suit

    return None


def detect_rank(card_img: np.ndarray) -> Optional[str]:
    """Detect card rank by OCR on the rank region.

    The rank appears in the top-left of PPPoker cards.

    Args:
        card_img: BGR image of the card region.

    Returns:
        Rank character ('A', '2'-'9', 'T', 'J', 'Q', 'K') or None.
    """
    if card_img is None or card_img.size == 0:
        return None

    h, w = card_img.shape[:2]

    # Crop top portion where rank is shown (top ~40%, left ~60%)
    rank_region = card_img[0:int(h * 0.4), 0:int(w * 0.6)]

    if rank_region.size == 0:
        return None

    # Convert to grayscale
    gray = cv2.cvtColor(rank_region, cv2.COLOR_BGR2GRAY)

    # Threshold: rank text is white on colored background
    _, binary = cv2.threshold(gray, 180, 255, cv2.THRESH_BINARY)

    # Also try inverted for dark backgrounds (spades)
    _, binary_inv = cv2.threshold(gray, 80, 255, cv2.THRESH_BINARY_INV)

    # Count white pixels to decide which threshold works better
    white_normal = cv2.countNonZero(binary)
    white_inv = cv2.countNonZero(binary_inv)

    # Use the one that produces a reasonable amount of white (the text)
    total = binary.shape[0] * binary.shape[1]
    ratio_normal = white_normal / total
    ratio_inv = white_inv / total

    # Ideal: text covers 5-40% of the rank region
    if 0.05 < ratio_normal < 0.40:
        img_to_ocr = binary
    elif 0.05 < ratio_inv < 0.40:
        img_to_ocr = binary_inv
    else:
        img_to_ocr = binary

    # Try pytesseract first (lighter)
    rank = _ocr_rank_tesseract(img_to_ocr)
    if rank:
        return rank

    # Fallback to easyocr
    rank = _ocr_rank_easyocr(img_to_ocr)
    return rank


def _ocr_rank_tesseract(binary_img: np.ndarray) -> Optional[str]:
    """Try to read rank using pytesseract."""
    try:
        import pytesseract
        # Single character mode (PSM 10), digits + rank letters only
        config = "--psm 10 -c tessedit_char_whitelist=A23456789TJQK10"
        text = pytesseract.image_to_string(binary_img, config=config).strip()
        return _normalize_rank(text)
    except (ImportError, Exception):
        return None


def _ocr_rank_easyocr(binary_img: np.ndarray) -> Optional[str]:
    """Try to read rank using easyocr."""
    try:
        import easyocr
        reader = easyocr.Reader(["en"], gpu=False, verbose=False)
        results = reader.readtext(binary_img, detail=0,
                                  allowlist="A23456789TJQK10")
        for text in results:
            rank = _normalize_rank(text.strip())
            if rank:
                return rank
    except (ImportError, Exception):
        pass
    return None


def _normalize_rank(text: str) -> Optional[str]:
    """Normalize OCR output to a rank character."""
    text = text.strip().upper()
    if text == "10":
        return "T"
    if text == "1":  # OCR might read "10" as just "1"
        return "T"
    if text == "0":  # Sometimes reads "10" as "0"
        return "T"
    if len(text) == 1 and text in RANKS:
        return text
    # Try first char
    if len(text) > 0 and text[0] in RANKS:
        return text[0]
    return None


def recognize_card(card_img: np.ndarray) -> Optional[str]:
    """Recognize a single card from its image.

    Args:
        card_img: BGR image of the card.

    Returns:
        Card string like "Ah", "Td", etc. or None.
    """
    suit = detect_suit(card_img)
    rank = detect_rank(card_img)

    if suit and rank:
        return rank + suit
    return None


def is_card_present(card_img: np.ndarray) -> bool:
    """Check if a card is actually present in the region (vs empty/green felt).

    An empty region will be mostly uniform green (the table felt).
    """
    if card_img is None or card_img.size == 0:
        return False

    hsv = cv2.cvtColor(card_img, cv2.COLOR_BGR2HSV)

    # Table felt is dark green
    felt_lower = np.array([35, 30, 30])
    felt_upper = np.array([85, 255, 180])
    mask = cv2.inRange(hsv, felt_lower, felt_upper)
    felt_ratio = cv2.countNonZero(mask) / (card_img.shape[0] * card_img.shape[1])

    # If mostly green felt, no card present
    return felt_ratio < 0.6


def is_card_facedown(card_img: np.ndarray) -> bool:
    """Check if a card is face-down (card back pattern).

    Face-down cards typically have a uniform dark pattern.
    """
    if card_img is None or card_img.size == 0:
        return False

    gray = cv2.cvtColor(card_img, cv2.COLOR_BGR2GRAY)
    std = np.std(gray)

    # Face-down cards have low variance (uniform pattern)
    # Face-up cards have high variance (text, symbols, colors)
    return std < 25
