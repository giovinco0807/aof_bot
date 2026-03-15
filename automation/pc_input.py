"""PC-based auto-input for PPPoker AoF on Windows.

Uses PostMessage to send clicks to the PPPoker window without moving the mouse.
Button coordinates are calibrated from screenshot analysis.

Anti-detection measures:
  - Log-normal delay distribution (matches human reaction times)
  - 2D Gaussian jitter around button center
  - Variable mouse movement speed
  - Occasional "hesitation" patterns

Usage:
    from pc_input import PcController
    pc = PcController()
    pc.tap_fold()
    pc.tap_allin()
"""

import random
import time
import json
import math
from pathlib import Path

try:
    import pyautogui
    pyautogui.FAILSAFE = True  # Move mouse to corner to abort
    pyautogui.PAUSE = 0.05
except ImportError:
    pyautogui = None

try:
    import pygetwindow as gw
except ImportError:
    gw = None

# Config file for button coordinates
CONFIG_PATH = Path(__file__).parent / "data" / "pc_config.json"

# Assets directory for button images
ASSETS_DIR = Path(__file__).parent / "assets"

DEFAULT_CONFIG = {
    "window_title": "PPPoker v",
    "buttons": {
        "fold":          {"x": 628, "y": 929, "label": "FOLD",    "image": "btn_fold.png"},
        "allin":         {"x": 801, "y": 929, "label": "ALL IN",  "image": "btn_allin.png"},
        "leave":         {"x": 0, "y": 0, "label": "LEAVE",   "image": "btn_leave.png"},
        "leave_confirm": {"x": 0, "y": 0, "label": "CONFIRM", "image": "btn_confirm.png"},
    },
    # 2D Gaussian jitter (sigma in pixels)
    "tap_jitter_sigma": 4,
    # Log-normal delay parameters (matches human reaction time)
    "delay_mu": 0.2,
    "delay_sigma": 0.5,
    "delay_floor": 0.4,
    "delay_cap": 6.0,
    # Probability of "hesitation"
    "hesitation_prob": 0.15,
    # Use image recognition to find buttons (vs fixed coordinates)
    "use_image_recognition": True,
    "image_confidence": 0.85,
}


class PcController:
    """Drop-in replacement for AdbController that controls PPPoker on Windows."""

    def __init__(self):
        self.config = self._load_config()
        self.enabled = False  # Safety: must be explicitly enabled
        self._window = None

    def _load_config(self) -> dict:
        if CONFIG_PATH.exists():
            with open(CONFIG_PATH, "r") as f:
                saved = json.load(f)
                # Merge with defaults (in case new keys were added)
                merged = DEFAULT_CONFIG.copy()
                merged.update(saved)
                if "buttons" in saved:
                    merged["buttons"] = DEFAULT_CONFIG["buttons"].copy()
                    merged["buttons"].update(saved["buttons"])
                return merged
        return DEFAULT_CONFIG.copy()

    def _save_config(self):
        CONFIG_PATH.parent.mkdir(parents=True, exist_ok=True)
        with open(CONFIG_PATH, "w") as f:
            json.dump(self.config, f, indent=2)

    def check_connection(self) -> bool:
        """Check if PPPoker window exists and pyautogui is available."""
        if pyautogui is None:
            print("  [PC] ERROR: pyautogui not installed. Run: pip install pyautogui")
            return False
        # Try to find the PPPoker window
        title = self.config.get("window_title", "PPPoker")
        if gw:
            windows = gw.getWindowsWithTitle(title)
            # Filter out hidden/minimized or tiny dummy windows
            valid_windows = [w for w in windows if w.width > 300 and w.height > 300 and w.left >= -10000]
            if valid_windows:
                self._window = valid_windows[0]
                print(f"  [PC] Found window: '{self._window.title}' at ({self._window.left}, {self._window.top}) "
                      f"size={self._window.width}x{self._window.height}")
                return True
            else:
                print(f"  [PC] WARNING: No window with title containing '{title}' found")
                print(f"  [PC] Will still work if coordinates are set manually")
                return True  # Still allow manual coordinate mode
        # Without pygetwindow, just check pyautogui works
        print(f"  [PC] pygetwindow not available, using absolute coordinates")
        return True

    def _find_button_by_image(self, btn_name: str):
        """Find button using real-time color detection within PPPoker window.
        
        FOLD = red/dark-red button
        ALL-IN = gold/yellow button
        
        Scans bottom 20% of the window for colored rectangular regions.
        Falls back to template matching if color detection fails.
        """
        try:
            import cv2
            import numpy as np

            win_rect = self._get_window_rect()
            if not win_rect:
                print("  [PC] PPPoker window not found")
                return None
            wx, wy, ww, wh = win_rect

            # Screenshot the bottom 50% of the window
            bottom_h = int(wh * 0.5)
            bottom_y = wh - bottom_h
            region = (wx, wy + bottom_y, ww, bottom_h)
            screen_img = pyautogui.screenshot(region=region)
            screen = cv2.cvtColor(np.array(screen_img), cv2.COLOR_RGB2BGR)
            hsv = cv2.cvtColor(screen, cv2.COLOR_BGR2HSV)

            sh, sw = screen.shape[:2]

            if btn_name == "fold":
                # Red button: H=0-15 or 160-180, moderate S and V
                mask1 = cv2.inRange(hsv, (0, 30, 40), (15, 255, 200))
                mask2 = cv2.inRange(hsv, (160, 30, 40), (180, 255, 200))
                mask = mask1 | mask2
            elif btn_name == "allin":
                # Gold/yellow button: H=15-35
                mask = cv2.inRange(hsv, (15, 30, 40), (40, 255, 220))
            else:
                print(f"  [PC] Unknown button: {btn_name}")
                return None

            # Morphological cleanup
            kernel = cv2.getStructuringElement(cv2.MORPH_RECT, (5, 5))
            mask = cv2.morphologyEx(mask, cv2.MORPH_CLOSE, kernel)
            mask = cv2.morphologyEx(mask, cv2.MORPH_OPEN, kernel)

            # Find contours
            contours, _ = cv2.findContours(mask, cv2.RETR_EXTERNAL, cv2.CHAIN_APPROX_SIMPLE)

            # Filter: button should be rectangular and reasonable size
            candidates = []
            for c in contours:
                area = cv2.contourArea(c)
                if area < 800:  # Too small
                    continue
                x, y, bw, bh = cv2.boundingRect(c)
                aspect = bw / max(bh, 1)
                # Buttons are wider than tall (aspect > 1.5)
                if aspect < 1.2 or aspect > 8:
                    continue
                # Button should be in bottom half of the bottom strip
                if y < sh * 0.3:
                    continue
                fill = area / (bw * bh)
                if fill < 0.4:  # Not rectangle-like
                    continue
                candidates.append((area, x, y, bw, bh))

            if not candidates:
                # Save debug images to understand why detection failed
                debug_path = ASSETS_DIR / f"debug_{btn_name}_screen.png"
                mask_path = ASSETS_DIR / f"debug_{btn_name}_mask.png"
                cv2.imwrite(str(debug_path), screen)
                cv2.imwrite(str(mask_path), mask)
                n_contours = len(contours)
                mask_pixels = cv2.countNonZero(mask)
                print(f"  [PC] {btn_name}: no candidates (contours={n_contours}, mask_px={mask_pixels})")
                print(f"  [PC] Debug saved: {debug_path}")
                # Fall back to template matching
                return self._find_button_by_template(btn_name)

            # Take the largest candidate
            candidates.sort(key=lambda c: c[0], reverse=True)
            area, bx, by, bw, bh = candidates[0]

            # Center, converted to screen coordinates
            cx = wx + bx + bw // 2
            cy = wy + bottom_y + by + bh // 2
            print(f"  [PC] {btn_name}: color-detected at ({cx},{cy}) "
                  f"size={bw}x{bh} area={area}")
            return (cx, cy)

        except ImportError:
            print("  [PC] OpenCV not installed. pip install opencv-python")
            return None
        except Exception as e:
            print(f"  [PC] Button detection failed for {btn_name}: {e}")
            return None

    def _find_button_by_template(self, btn_name: str):
        """Fallback: find button using template matching."""
        btn = self.config["buttons"].get(btn_name, {})
        img_name = btn.get("image", "")
        img_path = ASSETS_DIR / img_name
        if not img_path.exists():
            return None

        try:
            import cv2
            import numpy as np

            win_rect = self._get_window_rect()
            if not win_rect:
                return None
            wx, wy, ww, wh = win_rect

            screen_img = pyautogui.screenshot(region=(wx, wy, ww, wh))
            screen = cv2.cvtColor(np.array(screen_img), cv2.COLOR_RGB2BGR)
            template = cv2.imread(str(img_path))
            if template is None:
                return None

            th, tw = template.shape[:2]
            # Scale template to match screen if needed
            scale = wh / 2400  # mobile screenshot was 2400px tall
            if abs(scale - 1.0) > 0.1:
                new_w = max(1, int(tw * scale))
                new_h = max(1, int(th * scale))
                template = cv2.resize(template, (new_w, new_h))
                th, tw = template.shape[:2]

            result = cv2.matchTemplate(screen, template, cv2.TM_CCOEFF_NORMED)
            min_val, max_val, min_loc, max_loc = cv2.minMaxLoc(result)

            conf = self.config.get("image_confidence", 0.7)
            if max_val < conf:
                return None

            cx = wx + max_loc[0] + tw // 2
            cy = wy + max_loc[1] + th // 2
            print(f"  [PC] {btn_name}: template-matched at ({cx},{cy}) conf={max_val:.3f}")
            return (cx, cy)

        except Exception:
            return None

    def _get_window_rect(self):
        """Get PPPoker window rectangle (x, y, w, h) on screen."""
        if gw:
            try:
                title = self.config.get("window_title", "PPPoker")
                windows = gw.getWindowsWithTitle(title)
                # Filter out minimized/dummy windows
                valid_windows = [w for w in windows if w.width > 300 and w.height > 300 and w.left >= -10000]
                if valid_windows:
                    w = valid_windows[0]
                    return (w.left, w.top, w.width, w.height)
            except Exception:
                pass
        return None

    def resize_window(self, width=1064, height=970):
        """Force the PPPoker window to a specific size so default coordinates match."""
        if gw:
            try:
                title = self.config.get("window_title", "PPPoker")
                windows = gw.getWindowsWithTitle(title)
                valid_windows = [w for w in windows if w.width > 300 and w.height > 300 and w.left >= -10000]
                if valid_windows:
                    w = valid_windows[0]
                    # Only move/resize if it doesn't already match
                    if w.width != width or w.height != height or w.left != 0 or w.top != 0:
                        w.moveTo(0, 0)
                        w.resizeTo(width, height)
                        print(f"  [PC] Resized '{title}' to {width}x{height} at (0,0)")
                    return True
            except Exception as e:
                print(f"  [PC] Failed to resize window: {e}")
        return False

    def capture_button_templates(self):
        """Capture button template images from the current PPPoker screen.
        
        Take a screenshot, then let user click on each button region.
        Or auto-detect from known default positions and crop.
        """
        win_rect = self._get_window_rect()
        if not win_rect:
            print("PPPoker window not found")
            return
        wx, wy, ww, wh = win_rect

        import cv2
        import numpy as np

        screen_img = pyautogui.screenshot(region=(wx, wy, ww, wh))
        screen = cv2.cvtColor(np.array(screen_img), cv2.COLOR_RGB2BGR)

        # Save full screenshot for reference
        ASSETS_DIR.mkdir(parents=True, exist_ok=True)
        ref_path = ASSETS_DIR / "screenshot_ref.png"
        cv2.imwrite(str(ref_path), screen)
        print(f"Reference screenshot: {ref_path}")

        for btn_name, btn_cfg in self.config["buttons"].items():
            bx, by = btn_cfg.get("x", 0), btn_cfg.get("y", 0)
            if bx == 0 and by == 0:
                print(f"  {btn_name}: no coordinates, skipping")
                continue

            # Crop region around the button (80x40 for standard buttons)
            crop_w, crop_h = 100, 50
            x1 = max(0, bx - crop_w // 2)
            y1 = max(0, by - crop_h // 2)
            x2 = min(ww, bx + crop_w // 2)
            y2 = min(wh, by + crop_h // 2)

            btn_img = screen[y1:y2, x1:x2]
            if btn_img.size == 0:
                print(f"  {btn_name}: crop empty at ({bx}, {by})")
                continue

            save_path = ASSETS_DIR / btn_cfg.get("image", f"btn_{btn_name}.png")
            cv2.imwrite(str(save_path), btn_img)
            print(f"  {btn_name}: saved {save_path} ({x2-x1}x{y2-y1})")

    def _get_button_pos(self, btn_name: str, table_index: int = 0) -> tuple:
        """Get button position (image recognition or fixed coordinates)."""
        # Try image recognition first if enabled
        if self.config.get("use_image_recognition", True):
            pos = self._find_button_by_image(btn_name) # image rec doesn't support multi-table cleanly yet
            if pos:
                return pos
            print(f"  [PC] {btn_name}: image recognition failed, NOT clicking")
            return None

        # Fixed coordinates mode
        # Try to look up table-specific button first (e.g. fold_0 or fold_1)
        btn_key = f"{btn_name}_{table_index}"
        btn = self.config["buttons"].get(btn_key)
        if not btn:
            # Fallback to general button name
            btn = self.config["buttons"].get(btn_name, {})
            
        x, y = btn.get("x", 0), btn.get("y", 0)
        if x == 0 and y == 0:
            print(f"  [PC] WARNING: Button '{btn_key}' (or '{btn_name}') has no coordinates set! Use --calibrate")
            return None

        # Make coordinates relative to window
        win_rect = self._get_window_rect()
        if win_rect:
            x += win_rect[0]
            y += win_rect[1]

        return (x, y)

    def tap(self, x: int, y: int, jitter: bool = True):
        """Click at screen position using SetForegroundWindow + SetCursorPos + mouse_event."""
        try:
            import ctypes
            import ctypes.wintypes as wt

            if jitter:
                sigma = self.config.get("tap_jitter_sigma", 4)
                x += int(random.gauss(0, sigma))
                y += int(random.gauss(0, sigma))

            # Find window handle and bring to front
            hwnd = self._find_hwnd()
            if hwnd:
                ctypes.windll.user32.SetForegroundWindow(hwnd)
                time.sleep(0.3)

            # Move cursor to position
            ctypes.windll.user32.SetCursorPos(x, y)
            time.sleep(0.3 + random.random() * 0.2)

            # Mouse down (hold briefly), then up
            ctypes.windll.user32.mouse_event(0x0002, 0, 0, 0, 0)  # LEFTDOWN
            time.sleep(0.10 + random.random() * 0.05)
            ctypes.windll.user32.mouse_event(0x0004, 0, 0, 0, 0)  # LEFTUP
        except Exception as e:
            print(f"  [PC] Error in tap() at ({x}, {y}): {e}")

    def _find_hwnd(self):
        """Find the PPPoker window handle (HWND)."""
        import ctypes
        title = self.config.get("window_title", "PPPoker")
        result = [None]
        @ctypes.WINFUNCTYPE(ctypes.c_bool, ctypes.c_void_p, ctypes.c_void_p)
        def enum_cb(hwnd, lparam):
            buf = ctypes.create_unicode_buffer(256)
            ctypes.windll.user32.GetWindowTextW(hwnd, buf, 256)
            if title.lower() in buf.value.lower():
                result[0] = hwnd
                return False
            return True
        ctypes.windll.user32.EnumWindows(enum_cb, 0)
        return result[0]

    def _verified_button_pos(self, btn_name: str, table_index: int = 0) -> tuple:
        """Double-verify button position: detect twice with a gap, confirm match."""
        print(f"  [PC DEBUG] _verified_button_pos called for {btn_name} on table {table_index}")
        pos1 = None
        for _ in range(5):  # Try for up to 1 second
            pos1 = self._get_button_pos(btn_name, table_index)
            if pos1 is not None:
                break
            time.sleep(0.2)
            
        if pos1 is None:
            print(f"  [PC] {btn_name}: initial detection failed (button not found after retries)")
            return None

        # Wait and re-detect to verify it's stable and not a misclick
        time.sleep(0.2)
        pos2 = self._get_button_pos(btn_name, table_index)
        if pos2 is None:
            print(f"  [PC] {btn_name}: second detection failed (button disappeared?)")
            return None

        # Check positions match within tolerance (15px)
        dx = abs(pos1[0] - pos2[0])
        dy = abs(pos1[1] - pos2[1])
        if dx > 15 or dy > 15:
            print(f"  [PC] {btn_name}: positions differ! ({pos1} vs {pos2}) — ABORT")
            return None

        # Use average of both detections for accuracy
        cx = (pos1[0] + pos2[0]) // 2
        cy = (pos1[1] + pos2[1]) // 2
        print(f"  [PC] {btn_name}: verified at ({cx},{cy}) (drift: {dx},{dy}px)")
        return (cx, cy)

    def tap_fold(self, delay: bool = True, table_index: int = 0) -> bool:
        """Click the Fold button on the specified table."""
        try:
            if not self.enabled:
                print("  [PC] Auto-input disabled (use --auto-play to enable)")
                return False
            if delay:
                self._human_delay()
            pos = self._verified_button_pos("fold", table_index)
            if pos is None:
                return False
            self.tap(pos[0], pos[1])
            print(f"  [PC] Clicked FOLD at ({pos[0]}, {pos[1]}) on table {table_index}")
            return True
        except Exception as e:
            print(f"  [PC] Error in tap_fold: {e}")
            import traceback
            traceback.print_exc()
            return False

    def tap_allin(self, delay: bool = True, table_index: int = 0) -> bool:
        """Click the AllIn button on the specified table."""
        try:
            if not self.enabled:
                print("  [PC] Auto-input disabled (use --auto-play to enable)")
                return False
            if delay:
                self._human_delay()
            pos = self._verified_button_pos("allin", table_index)
            if pos is None:
                return False
            self.tap(pos[0], pos[1])
            print(f"  [PC] Clicked ALL IN at ({pos[0]}, {pos[1]}) on table {table_index}")
            return True
        except Exception as e:
            print(f"  [PC] Error in tap_allin: {e}")
            import traceback
            traceback.print_exc()
            return False

    def tap_leave(self, delay: bool = True, table_index: int = 0) -> bool:
        """Click the Leave button, then confirm dialog on the specified table."""
        if not self.enabled:
            print("  [PC] Auto-input disabled")
            return False
        if delay:
            self._human_delay()
        pos = self._get_button_pos("leave", table_index)
        if pos is None:
            return False
        self.tap(pos[0], pos[1])
        print(f"  [PC] Clicked LEAVE at ({pos[0]}, {pos[1]}) on table {table_index}")
        time.sleep(1.0)
        confirm_pos = self._get_button_pos("leave_confirm", table_index)
        if confirm_pos:
            self.tap(confirm_pos[0], confirm_pos[1])
            print(f"  [PC] Clicked CONFIRM at ({confirm_pos[0]}, {confirm_pos[1]}) on table {table_index}")
        return True

    def _human_delay(self):
        """Log-normal delay matching human reaction time distribution."""
        mu = self.config.get("delay_mu", 0.2)
        sigma = self.config.get("delay_sigma", 0.5)
        floor = self.config.get("delay_floor", 0.4)
        cap = self.config.get("delay_cap", 6.0)

        delay = random.lognormvariate(mu, sigma)
        delay = max(floor, min(delay, cap))

        if random.random() < self.config.get("hesitation_prob", 0.15):
            delay += random.uniform(1.0, 3.0)
            delay = min(delay, cap)

        time.sleep(delay)

    def set_button(self, name: str, x: int, y: int):
        """Set button coordinates (relative to PPPoker window)."""
        if name in self.config["buttons"]:
            self.config["buttons"][name]["x"] = x
            self.config["buttons"][name]["y"] = y
            self._save_config()
            print(f"  [PC] {name} button set to ({x}, {y})")
        else:
            print(f"  [PC] Unknown button: {name}")

    def screenshot(self, save_path: str = None) -> str:
        """Take a screenshot of the PPPoker window."""
        local_path = save_path or str(Path(__file__).parent / "data" / "screenshot_pc.png")
        Path(local_path).parent.mkdir(parents=True, exist_ok=True)

        if self._window and gw:
            try:
                windows = gw.getWindowsWithTitle(self.config.get("window_title", "PPPoker"))
                if windows:
                    w = windows[0]
                    w.activate()
                    time.sleep(0.3)
                    region = (w.left, w.top, w.width, w.height)
                    img = pyautogui.screenshot(region=region)
                    img.save(local_path)
                    print(f"  [PC] Screenshot saved: {local_path}")
                    return local_path
            except Exception as e:
                print(f"  [PC] Window screenshot failed: {e}")

        # Fallback: full screen
        img = pyautogui.screenshot()
        img.save(local_path)
        print(f"  [PC] Full screenshot saved: {local_path}")
        return local_path

    def calibrate(self):
        """Interactive calibration: screenshot + manual coordinate input."""
        print("=== PC Button Calibration ===")
        print("Taking screenshot of PPPoker window...")
        path = self.screenshot()
        print(f"Screenshot saved to: {path}")
        print("Open the screenshot and note the button coordinates")
        print("(coordinates should be RELATIVE to the PPPoker window top-left corner)")
        print()

        for btn_name in ("fold", "allin", "leave", "leave_confirm"):
            label = self.config["buttons"][btn_name]["label"]
            current_x = self.config["buttons"][btn_name]["x"]
            current_y = self.config["buttons"][btn_name]["y"]
            print(f"{label} button (current: {current_x}, {current_y})")
            coords = input(f"  Enter new coordinates (x,y) or press Enter to keep: ").strip()
            if coords:
                parts = coords.replace(" ", "").split(",")
                if len(parts) == 2:
                    x, y = int(parts[0]), int(parts[1])
                    self.set_button(btn_name, x, y)

        self._save_config()
        print("Calibration complete. Config saved.")


# CLI for calibration and testing
if __name__ == "__main__":
    import argparse
    parser = argparse.ArgumentParser(description="PC auto-input for PPPoker")
    parser.add_argument("--calibrate", action="store_true", help="Calibrate button positions")
    parser.add_argument("--screenshot", action="store_true", help="Take a screenshot")
    parser.add_argument("--capture-buttons", action="store_true", help="Capture button template images from current screen")
    parser.add_argument("--test", choices=["fold", "allin"], help="Test click a button")
    args = parser.parse_args()

    ctrl = PcController()
    if not ctrl.check_connection():
        print("ERROR: Cannot initialize PC controller")
        exit(1)

    if args.screenshot:
        path = ctrl.screenshot()
        print(f"Screenshot: {path}")
    elif getattr(args, 'capture_buttons', False):
        ctrl.capture_button_templates()
    elif args.calibrate:
        ctrl.calibrate()
    elif args.test:
        ctrl.enabled = True
        if args.test == "fold":
            ctrl.tap_fold(delay=False)
        else:
            ctrl.tap_allin(delay=False)
    else:
        parser.print_help()
