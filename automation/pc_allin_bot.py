"""PC auto-play bot for PPPoker.

Uses SetForegroundWindow + SetCursorPos + mouse_event.
Clicks ALL-IN or FOLD every N seconds with proper delays.

Usage:
    python pc_allin_bot.py           # ALL-IN every 4s
    python pc_allin_bot.py --fold    # FOLD every 4s
    python pc_allin_bot.py --interval 5  # Custom interval
"""

import ctypes
import ctypes.wintypes as wt
import time
import sys
import random
from datetime import datetime

WINDOW_TITLE = "PPPoker v"

# Button positions RELATIVE to window top-left (from screenshot calibration)
BUTTONS = {
    "allin": {"x": 801, "y": 929},
    "fold":  {"x": 628, "y": 929},
}


def find_hwnd():
    """Find PPPoker window handle."""
    result = [None]
    @ctypes.WINFUNCTYPE(ctypes.c_bool, ctypes.c_void_p, ctypes.c_void_p)
    def cb(hwnd, _):
        buf = ctypes.create_unicode_buffer(256)
        ctypes.windll.user32.GetWindowTextW(hwnd, buf, 256)
        if WINDOW_TITLE.lower() in buf.value.lower():
            result[0] = hwnd
            return False
        return True
    ctypes.windll.user32.EnumWindows(cb, 0)
    return result[0]


def get_window_rect(hwnd):
    """Get window position."""
    rect = wt.RECT()
    ctypes.windll.user32.GetWindowRect(hwnd, ctypes.byref(rect))
    return rect


def click_button(hwnd, btn_x, btn_y):
    """Click a button: bring window to front, move cursor, click with delays."""
    rect = get_window_rect(hwnd)
    sx = rect.left + btn_x
    sy = rect.top + btn_y

    # 1. Bring PPPoker to foreground
    ctypes.windll.user32.SetForegroundWindow(hwnd)
    time.sleep(0.3)

    # 2. Move mouse cursor to button
    ctypes.windll.user32.SetCursorPos(sx, sy)
    time.sleep(0.3 + random.random() * 0.2)

    # 3. Mouse down (hold briefly like a human)
    ctypes.windll.user32.mouse_event(0x0002, 0, 0, 0, 0)
    time.sleep(0.1 + random.random() * 0.05)

    # 4. Mouse up
    ctypes.windll.user32.mouse_event(0x0004, 0, 0, 0, 0)


def main():
    import argparse
    parser = argparse.ArgumentParser()
    parser.add_argument("--fold", action="store_true")
    parser.add_argument("--interval", type=float, default=4.0)
    args = parser.parse_args()

    action = "fold" if args.fold else "allin"
    btn = BUTTONS[action]

    print("=" * 50)
    print(f"  PC Auto {action.upper()} Bot")
    print("=" * 50)

    hwnd = find_hwnd()
    if not hwnd:
        print("  PPPoker window not found!")
        return

    rect = get_window_rect(hwnd)
    print(f"  HWND: {hwnd}")
    print(f"  Window: ({rect.left},{rect.top}) {rect.right-rect.left}x{rect.bottom-rect.top}")
    print(f"  Button: win({btn['x']},{btn['y']}) → screen({rect.left+btn['x']},{rect.top+btn['y']})")
    print(f"  Interval: {args.interval}s")
    print("  Press Ctrl+C to stop\n")

    count = 0
    while True:
        try:
            hwnd = find_hwnd()
            if not hwnd:
                print("\r  Window lost...", end="")
                time.sleep(2)
                continue

            click_button(hwnd, btn["x"], btn["y"])
            count += 1

            ts = datetime.now().strftime("%H:%M:%S")
            rect = get_window_rect(hwnd)
            print(f"\r  [{ts}] #{count:4d} {action.upper()} at ({rect.left+btn['x']},{rect.top+btn['y']})", end="")
            sys.stdout.flush()

            time.sleep(args.interval)

        except KeyboardInterrupt:
            print(f"\n\n  Done: {count} clicks.")
            break


if __name__ == "__main__":
    main()
