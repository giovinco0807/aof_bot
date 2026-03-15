"""Capture button templates from PPPoker screenshots.

Usage:
    1. Make sure PPPoker is showing the FOLD / ALL-IN buttons
    2. Run: python automation/capture_buttons.py
"""
import cv2
import numpy as np
import pyautogui
from pathlib import Path

try:
    import pygetwindow as gw
except ImportError:
    gw = None

ASSETS_DIR = Path(__file__).parent / "assets"
ASSETS_DIR.mkdir(parents=True, exist_ok=True)


def find_pppoker_window():
    """Find PPPoker window rectangle."""
    if not gw:
        print("pygetwindow not installed")
        return None
    windows = gw.getWindowsWithTitle("PPPoker v")
    if not windows:
        print("PPPoker window not found")
        return None
    w = windows[0]
    print(f"Found window: {w.title} at ({w.left}, {w.top}) {w.width}x{w.height}")
    return (w.left, w.top, w.width, w.height)


def capture_window(rect):
    """Screenshot the PPPoker window."""
    img = pyautogui.screenshot(region=rect)
    return cv2.cvtColor(np.array(img), cv2.COLOR_RGB2BGR)


def find_buttons_by_color(screen):
    """Find FOLD and ALL-IN buttons by their distinctive colors.
    
    FOLD button: Red/dark red background (#8B2020 area)
    ALL-IN button: Gold/yellow background (#C5A23C area)
    """
    hsv = cv2.cvtColor(screen, cv2.COLOR_BGR2HSV)
    h, w = screen.shape[:2]
    
    # Only search bottom 30% of screen (buttons are always at bottom)
    bottom_region = int(h * 0.7)
    hsv_bottom = hsv[bottom_region:, :, :]
    screen_bottom = screen[bottom_region:, :, :]
    
    results = {}
    
    # FOLD button: Red hue (0-10 and 170-180), high saturation
    mask_red1 = cv2.inRange(hsv_bottom, (0, 60, 60), (15, 255, 255))
    mask_red2 = cv2.inRange(hsv_bottom, (160, 60, 60), (180, 255, 255))
    mask_red = mask_red1 | mask_red2
    
    # ALL-IN button: Yellow/gold hue (15-35), high saturation
    mask_gold = cv2.inRange(hsv_bottom, (15, 60, 80), (40, 255, 255))
    
    for name, mask, color_desc in [("fold", mask_red, "red"), ("allin", mask_gold, "gold")]:
        # Find contours
        contours, _ = cv2.findContours(mask, cv2.RETR_EXTERNAL, cv2.CHAIN_APPROX_SIMPLE)
        
        # Filter: button should be a reasonable size (> 5000px area)
        btn_contours = []
        for c in contours:
            area = cv2.contourArea(c)
            if area > 3000:
                btn_contours.append((area, c))
        
        if not btn_contours:
            print(f"  {name}: No {color_desc} button region found")
            continue
        
        # Take the largest contour
        btn_contours.sort(key=lambda x: x[0], reverse=True)
        area, best = btn_contours[0]
        x, y, bw, bh = cv2.boundingRect(best)
        
        # Add small padding
        pad = 5
        x1 = max(0, x - pad)
        y1 = max(0, y - pad)
        x2 = min(screen_bottom.shape[1], x + bw + pad)
        y2 = min(screen_bottom.shape[0], y + bh + pad)
        
        btn_img = screen_bottom[y1:y2, x1:x2]
        
        # Save template
        save_path = ASSETS_DIR / f"btn_{name}.png"
        cv2.imwrite(str(save_path), btn_img)
        
        # Coordinates relative to full window
        center_x = x + bw // 2
        center_y = bottom_region + y + bh // 2
        
        results[name] = {
            "path": save_path,
            "center": (center_x, center_y),
            "size": (bw, bh),
            "area": area,
        }
        print(f"  {name}: saved {save_path} ({x2-x1}x{y2-y1}px) center=({center_x},{center_y})")
    
    return results


def main():
    print("=== Button Template Capture ===\n")
    
    rect = None
    try:
        rect = find_pppoker_window()
    except Exception as e:
        print(f"Window search error: {e}")
    
    if not rect:
        # Try from saved screenshot
        ss_path = Path(__file__).parent / "data" / "screenshot.png"
        if ss_path.exists():
            print(f"Using saved screenshot: {ss_path}")
            screen = cv2.imread(str(ss_path))
        else:
            print("No window and no screenshot found")
            return
    else:
        import time
        # Bring window to front
        windows = gw.getWindowsWithTitle("PPPoker v")
        if windows:
            windows[0].activate()
            time.sleep(0.5)
        screen = capture_window(rect)
    
    # Save reference
    ref_path = ASSETS_DIR / "screenshot_ref.png"
    cv2.imwrite(str(ref_path), screen)
    print(f"Reference saved: {ref_path}")
    print(f"Screen size: {screen.shape[1]}x{screen.shape[0]}")
    print()
    
    results = find_buttons_by_color(screen)
    
    if results:
        print(f"\n=== Found {len(results)} button(s) ===")
        
        # Update config
        import json
        config_path = Path(__file__).parent / "data" / "pc_config.json"
        config = {}
        if config_path.exists():
            with open(config_path) as f:
                config = json.load(f)
        
        if "buttons" not in config:
            config["buttons"] = {}
        
        for name, info in results.items():
            cx, cy = info["center"]
            if name not in config["buttons"]:
                config["buttons"][name] = {}
            config["buttons"][name]["x"] = cx
            config["buttons"][name]["y"] = cy
            config["buttons"][name]["image"] = f"btn_{name}.png"
            print(f"  {name}: center=({cx}, {cy})")
        
        config["use_image_recognition"] = True
        config["image_confidence"] = 0.85
        
        with open(config_path, "w") as f:
            json.dump(config, f, indent=2)
        print(f"\nConfig saved: {config_path}")
    else:
        print("\nNo buttons found! Make sure FOLD/ALL-IN buttons are visible on screen.")


if __name__ == "__main__":
    main()
