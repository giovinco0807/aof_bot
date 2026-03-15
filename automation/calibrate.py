"""Calibration tool for PPPoker AoF bot.

Usage:
    python calibrate.py screenshot    # Take a screenshot and save it
    python calibrate.py pick          # Interactive point picker on saved screenshot
    python calibrate.py cards         # Crop card templates from screenshot
    python calibrate.py test          # Test OCR on saved screenshot
"""

import sys
import json
import time
from pathlib import Path

import cv2
import numpy as np
import mss
import mss.tools


SCREENSHOT_DIR = Path("screenshots")
TEMPLATE_DIR = Path("tablemaps")
CALIBRATION_FILE = Path("calibration.json")


def take_screenshot(name: str = "table"):
    """Capture the full screen and save it."""
    SCREENSHOT_DIR.mkdir(exist_ok=True)
    sct = mss.mss()

    print("Taking screenshot in 3 seconds... Switch to PPPoker window!")
    for i in range(3, 0, -1):
        print(f"  {i}...")
        time.sleep(1)

    monitor = sct.monitors[1]  # Primary monitor
    img = sct.grab(monitor)
    arr = np.array(img)[:, :, :3]  # Drop alpha, BGRA -> BGR

    timestamp = time.strftime("%Y%m%d_%H%M%S")
    filename = SCREENSHOT_DIR / f"{name}_{timestamp}.png"
    cv2.imwrite(str(filename), arr)
    print(f"Saved: {filename} ({arr.shape[1]}x{arr.shape[0]})")

    # Also save as 'latest'
    latest = SCREENSHOT_DIR / f"{name}_latest.png"
    cv2.imwrite(str(latest), arr)
    print(f"Saved: {latest}")
    return str(latest)


def pick_points():
    """Interactive tool to pick coordinates on a screenshot."""
    latest = SCREENSHOT_DIR / "table_latest.png"
    if not latest.exists():
        print(f"No screenshot found at {latest}")
        print("Run: python calibrate.py screenshot")
        return

    img = cv2.imread(str(latest))
    points = []
    labels = []

    # Define what to pick
    pick_items = [
        ("hero_card1_topleft", "Click TOP-LEFT of hero's FIRST card"),
        ("hero_card1_bottomright", "Click BOTTOM-RIGHT of hero's FIRST card"),
        ("hero_card2_topleft", "Click TOP-LEFT of hero's SECOND card"),
        ("hero_card2_bottomright", "Click BOTTOM-RIGHT of hero's SECOND card"),
        ("hero_name_topleft", "Click TOP-LEFT of hero's name text"),
        ("hero_name_bottomright", "Click BOTTOM-RIGHT of hero's name text"),
        ("hero_stack_topleft", "Click TOP-LEFT of hero's stack number"),
        ("hero_stack_bottomright", "Click BOTTOM-RIGHT of hero's stack number"),
        ("opp_top_card1_topleft", "Click TOP-LEFT of top opponent's FIRST card"),
        ("opp_top_card1_bottomright", "Click BOTTOM-RIGHT of top opponent's FIRST card"),
        ("opp_top_card2_topleft", "Click TOP-LEFT of top opponent's SECOND card"),
        ("opp_top_card2_bottomright", "Click BOTTOM-RIGHT of top opponent's SECOND card"),
        ("opp_top_name_topleft", "Click TOP-LEFT of top opponent's name"),
        ("opp_top_name_bottomright", "Click BOTTOM-RIGHT of top opponent's name"),
        ("opp_top_stack_topleft", "Click TOP-LEFT of top opponent's stack"),
        ("opp_top_stack_bottomright", "Click BOTTOM-RIGHT of top opponent's stack"),
        ("board_card1_topleft", "Click TOP-LEFT of FIRST board card"),
        ("board_card1_bottomright", "Click BOTTOM-RIGHT of FIRST board card"),
        ("board_card5_topleft", "Click TOP-LEFT of LAST (5th) board card"),
        ("board_card5_bottomright", "Click BOTTOM-RIGHT of LAST (5th) board card"),
        ("dealer_button", "Click the CENTER of the dealer 'D' button"),
        ("blinds_text_topleft", "Click TOP-LEFT of the blinds text area"),
        ("blinds_text_bottomright", "Click BOTTOM-RIGHT of the blinds text area"),
    ]

    current_idx = [0]
    result = {}

    def mouse_callback(event, x, y, flags, param):
        if event == cv2.EVENT_LBUTTONDOWN:
            if current_idx[0] < len(pick_items):
                label = pick_items[current_idx[0]][0]
                result[label] = (x, y)
                print(f"  {label} = ({x}, {y})")
                current_idx[0] += 1

                # Draw marker
                cv2.circle(img, (x, y), 5, (0, 0, 255), -1)
                cv2.putText(img, label.split("_")[-1], (x + 8, y + 4),
                            cv2.FONT_HERSHEY_SIMPLEX, 0.4, (0, 255, 0), 1)

                if current_idx[0] < len(pick_items):
                    print(f"\n>> {pick_items[current_idx[0]][1]}")
                else:
                    print("\nDone! Press any key to save.")

    cv2.namedWindow("Calibrate", cv2.WINDOW_NORMAL)
    cv2.setMouseCallback("Calibrate", mouse_callback)

    print(f"\n>> {pick_items[0][1]}")
    print("(Press 'u' to undo last point, 'q' to quit)\n")

    while True:
        cv2.imshow("Calibrate", img)
        key = cv2.waitKey(50) & 0xFF

        if key == ord('q'):
            break
        elif key == ord('u') and current_idx[0] > 0:
            current_idx[0] -= 1
            label = pick_items[current_idx[0]][0]
            if label in result:
                del result[label]
            print(f"  Undone. >> {pick_items[current_idx[0]][1]}")
        elif current_idx[0] >= len(pick_items) and key != 255:
            break

    cv2.destroyAllWindows()

    if result:
        with open(CALIBRATION_FILE, "w") as f:
            json.dump(result, f, indent=2)
        print(f"\nCalibration saved to {CALIBRATION_FILE}")
        print(json.dumps(result, indent=2))


def detect_card_suit_by_color(card_img: np.ndarray) -> str:
    """Detect card suit from its background color.

    PPPoker card colors:
      Diamonds = blue, Hearts = red, Spades = dark/black, Clubs = green
    """
    hsv = cv2.cvtColor(card_img, cv2.COLOR_BGR2HSV)

    # Sample the center area of the card for dominant color
    h, w = card_img.shape[:2]
    center = hsv[h // 4:3 * h // 4, w // 4:3 * w // 4]

    mean_h = np.mean(center[:, :, 0])
    mean_s = np.mean(center[:, :, 1])
    mean_v = np.mean(center[:, :, 2])

    # Low saturation + low value = black/dark = spades
    if mean_s < 50 and mean_v < 100:
        return "s"
    # Low saturation + high value = could be white/gray
    if mean_s < 40:
        return "s"  # Default dark

    # By hue ranges (OpenCV hue is 0-180):
    # Red: 0-10 or 170-180
    # Green: 35-85
    # Blue: 100-130
    if mean_h < 10 or mean_h > 170:
        return "h"  # Hearts (red)
    elif 35 <= mean_h <= 85:
        return "c"  # Clubs (green)
    elif 100 <= mean_h <= 140:
        return "d"  # Diamonds (blue)
    else:
        return "s"  # Default to spades


def detect_card_rank_by_ocr(card_img: np.ndarray) -> str:
    """Detect card rank using OCR on the top-left corner."""
    import easyocr

    h, w = card_img.shape[:2]
    # Crop top-left quadrant where rank is displayed
    rank_region = card_img[0:h // 3, 0:w // 2]

    # Convert to grayscale and threshold for better OCR
    gray = cv2.cvtColor(rank_region, cv2.COLOR_BGR2GRAY)
    _, thresh = cv2.threshold(gray, 200, 255, cv2.THRESH_BINARY)

    reader = easyocr.Reader(["en"], gpu=False)
    results = reader.readtext(thresh, detail=0, allowlist="A23456789TJQK10")

    for text in results:
        text = text.strip().upper()
        if text == "10":
            return "T"
        if text in "A23456789TJQK":
            return text

    return ""


def test_card_recognition():
    """Test card recognition on the latest screenshot."""
    latest = SCREENSHOT_DIR / "table_latest.png"
    if not latest.exists():
        print("No screenshot found. Run: python calibrate.py screenshot")
        return

    img = cv2.imread(str(latest))
    print(f"Image size: {img.shape[1]}x{img.shape[0]}")

    if not CALIBRATION_FILE.exists():
        print("No calibration found. Run: python calibrate.py pick")
        return

    with open(CALIBRATION_FILE) as f:
        cal = json.load(f)

    # Test hero cards if calibrated
    if "hero_card1_topleft" in cal and "hero_card1_bottomright" in cal:
        x1, y1 = cal["hero_card1_topleft"]
        x2, y2 = cal["hero_card1_bottomright"]
        card1_img = img[y1:y2, x1:x2]

        suit = detect_card_suit_by_color(card1_img)
        print(f"Hero card 1 suit (by color): {suit}")

        cv2.imshow("Hero Card 1", card1_img)

    if "hero_card2_topleft" in cal and "hero_card2_bottomright" in cal:
        x1, y1 = cal["hero_card2_topleft"]
        x2, y2 = cal["hero_card2_bottomright"]
        card2_img = img[y1:y2, x1:x2]

        suit = detect_card_suit_by_color(card2_img)
        print(f"Hero card 2 suit (by color): {suit}")

        cv2.imshow("Hero Card 2", card2_img)

    # Show board cards
    if "board_card1_topleft" in cal and "board_card5_bottomright" in cal:
        x1, y1 = cal["board_card1_topleft"]
        x2, y2 = cal["board_card5_bottomright"]
        board_img = img[y1:y2, x1:x2]
        cv2.imshow("Board", board_img)

    cv2.waitKey(0)
    cv2.destroyAllWindows()


def crop_card_templates():
    """Crop individual card images from screenshot for template matching."""
    TEMPLATE_DIR.mkdir(exist_ok=True)

    latest = SCREENSHOT_DIR / "table_latest.png"
    if not latest.exists():
        print("No screenshot found.")
        return

    img = cv2.imread(str(latest))
    print(f"Image: {img.shape[1]}x{img.shape[0]}")
    print("\nClick on each card's top-left then bottom-right corner.")
    print("Then type the card name (e.g., 'Ah' for Ace of hearts).")
    print("Press 'q' to quit.\n")

    coords = []

    def mouse_cb(event, x, y, flags, param):
        if event == cv2.EVENT_LBUTTONDOWN:
            coords.append((x, y))
            cv2.circle(img, (x, y), 3, (0, 0, 255), -1)
            print(f"  Point: ({x}, {y})")

    cv2.namedWindow("Cards", cv2.WINDOW_NORMAL)
    cv2.setMouseCallback("Cards", mouse_cb)

    while True:
        cv2.imshow("Cards", img)
        key = cv2.waitKey(50) & 0xFF

        if key == ord('q'):
            break

        if len(coords) >= 2:
            x1, y1 = coords[-2]
            x2, y2 = coords[-1]
            card_img = cv2.imread(str(latest))[min(y1, y2):max(y1, y2), min(x1, x2):max(x1, x2)]

            cv2.imshow("Cropped Card", card_img)
            print("Enter card name (e.g., Ah, Kd, Ts): ", end="", flush=True)

            name = input().strip()
            if name and len(name) >= 2:
                path = TEMPLATE_DIR / f"{name}.png"
                cv2.imwrite(str(path), card_img)
                print(f"  Saved: {path}")
            coords.clear()

    cv2.destroyAllWindows()


if __name__ == "__main__":
    if len(sys.argv) < 2:
        print(__doc__)
        sys.exit(0)

    cmd = sys.argv[1]
    if cmd == "screenshot":
        take_screenshot()
    elif cmd == "pick":
        pick_points()
    elif cmd == "cards":
        crop_card_templates()
    elif cmd == "test":
        test_card_recognition()
    else:
        print(f"Unknown command: {cmd}")
        print(__doc__)
