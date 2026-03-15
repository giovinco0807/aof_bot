"""ADB-based auto-input for PPPoker AoF on Android.

Sends tap events to the Android device via ADB to press Fold/AllIn buttons.
Button coordinates are configurable and can be calibrated via screenshot.

Anti-detection measures:
  - sendevent (raw touch events) instead of 'input tap'
  - Log-normal delay distribution (matches human reaction times)
  - Variable touch duration (50-200ms)
  - 2D Gaussian jitter (not uniform rectangle)
  - Occasional "hesitation" patterns

Usage:
    from adb_input import AdbController
    adb = AdbController()
    adb.tap_fold()
    adb.tap_allin()
"""

import subprocess
import random
import time
import json
import math
from pathlib import Path

# Default ADB path (scrcpy bundled)
DEFAULT_ADB = r"C:\Users\Owner\Desktop\scrcpy-win64-v3.3.3\adb.exe"

# Config file for button coordinates
CONFIG_PATH = Path(__file__).parent / "data" / "adb_config.json"

# Default button coordinates for 1080x2400 (PPPoker AoF layout)
# These need to be calibrated per device - use calibrate() to set them
DEFAULT_CONFIG = {
    "screen_width": 1080,
    "screen_height": 2400,
    "buttons": {
        "fold": {"x": 270, "y": 1750, "label": "FOLD"},
        "allin": {"x": 810, "y": 1750, "label": "ALL IN"},
        "leave": {"x": 80, "y": 120, "label": "LEAVE (back/X button)"},
        "leave_confirm": {"x": 540, "y": 1300, "label": "LEAVE confirm dialog"},
    },
    # 2D Gaussian jitter (sigma in pixels)
    "tap_jitter_sigma": 12,
    # Log-normal delay parameters (matches human reaction time distribution)
    # median ~1.2s, 90th percentile ~2.5s, occasional 3-5s "thinks"
    "delay_mu": 0.2,       # ln(median) ≈ 0.2 → median ~1.2s
    "delay_sigma": 0.5,    # spread
    "delay_floor": 0.4,    # minimum delay (seconds)
    "delay_cap": 6.0,      # maximum delay (seconds)
    # Touch hold duration range (ms) - humans hold 80-250ms
    "touch_duration_min": 60,
    "touch_duration_max": 220,
    # Probability of "hesitation" (extra 1-3s pause, like re-reading cards)
    "hesitation_prob": 0.15,
    # Use sendevent (raw touch) instead of 'input tap'
    "use_sendevent": True,
    # Touch device path (auto-detected if empty)
    "touch_device": "",
}


class AdbController:
    def __init__(self, adb_path: str = DEFAULT_ADB, device_serial: str = None):
        self.adb_path = adb_path
        self.device_serial = device_serial
        self.config = self._load_config()
        self.enabled = False  # Safety: must be explicitly enabled

    def _load_config(self) -> dict:
        if CONFIG_PATH.exists():
            with open(CONFIG_PATH, "r") as f:
                return json.load(f)
        return DEFAULT_CONFIG.copy()

    def _save_config(self):
        CONFIG_PATH.parent.mkdir(parents=True, exist_ok=True)
        with open(CONFIG_PATH, "w") as f:
            json.dump(self.config, f, indent=2)

    def _adb_cmd(self, *args) -> subprocess.CompletedProcess:
        cmd = [self.adb_path]
        if self.device_serial:
            cmd += ["-s", self.device_serial]
        cmd += list(args)
        return subprocess.run(cmd, capture_output=True, text=True, timeout=10)

    def _adb_shell(self, shell_cmd: str) -> str:
        result = self._adb_cmd("shell", shell_cmd)
        return result.stdout.strip()

    def check_connection(self) -> bool:
        """Verify ADB connection to device."""
        try:
            result = self._adb_cmd("devices")
            lines = result.stdout.strip().split("\n")
            for line in lines[1:]:
                if "\tdevice" in line:
                    serial = line.split("\t")[0]
                    if self.device_serial is None or serial == self.device_serial:
                        if self.device_serial is None:
                            self.device_serial = serial
                        return True
            return False
        except Exception:
            return False

    def screenshot(self, save_path: str = None) -> str:
        """Take a screenshot and pull it to PC."""
        remote_path = "/sdcard/aof_screenshot.png"
        self._adb_shell(f"screencap -p {remote_path}")
        local_path = save_path or str(Path(__file__).parent / "data" / "screenshot.png")
        Path(local_path).parent.mkdir(parents=True, exist_ok=True)
        self._adb_cmd("pull", remote_path, local_path)
        self._adb_shell(f"rm {remote_path}")
        return local_path

    def _detect_touch_device(self) -> str:
        """Auto-detect the touch input device path."""
        cached = self.config.get("touch_device", "")
        if cached:
            return cached
        # Find touchscreen device from /proc/bus/input/devices
        try:
            out = self._adb_shell("getevent -pl 2>/dev/null | grep -B5 ABS_MT_POSITION")
            for line in out.split("\n"):
                line = line.strip()
                if line.startswith("add device"):
                    dev = line.split(":")[0].replace("add device", "").strip()
                    if dev:
                        self.config["touch_device"] = dev
                        return dev
        except Exception:
            pass
        return "/dev/input/event1"  # common default

    def _sendevent_tap(self, x: int, y: int, duration_ms: int = 120):
        """Send raw touch events via sendevent (harder to detect than 'input tap')."""
        dev = self._detect_touch_device()
        # ABS_MT values: need to scale to device touch range
        # Most Android devices use 0-32767 or match screen resolution
        # We'll use the screen resolution directly (works on most devices)
        sw = self.config.get("screen_width", 1080)
        sh = self.config.get("screen_height", 2400)

        # Clamp coordinates
        x = max(0, min(x, sw - 1))
        y = max(0, min(y, sh - 1))

        # Build sendevent sequence for a single finger tap
        # EV_ABS=3, EV_SYN=0, EV_KEY=1
        # ABS_MT_TRACKING_ID=57, ABS_MT_POSITION_X=53, ABS_MT_POSITION_Y=54
        # ABS_MT_TOUCH_MAJOR=48, ABS_MT_PRESSURE=58
        # BTN_TOUCH=330
        tracking_id = random.randint(100, 60000)
        pressure = random.randint(40, 70)
        touch_major = random.randint(5, 12)

        down_cmds = (
            f"sendevent {dev} 3 57 {tracking_id};"
            f"sendevent {dev} 3 53 {x};"
            f"sendevent {dev} 3 54 {y};"
            f"sendevent {dev} 3 48 {touch_major};"
            f"sendevent {dev} 3 58 {pressure};"
            f"sendevent {dev} 1 330 1;"
            f"sendevent {dev} 0 0 0"
        )

        up_cmds = (
            f"sendevent {dev} 3 57 -1;"
            f"sendevent {dev} 1 330 0;"
            f"sendevent {dev} 0 0 0"
        )

        self._adb_shell(down_cmds)
        time.sleep(duration_ms / 1000.0)
        self._adb_shell(up_cmds)

    def tap(self, x: int, y: int, jitter: bool = True):
        """Send a tap event to the device with human-like characteristics."""
        if jitter:
            sigma = self.config.get("tap_jitter_sigma", 12)
            # 2D Gaussian jitter (more natural than uniform rectangle)
            x += int(random.gauss(0, sigma))
            y += int(random.gauss(0, sigma))

        if self.config.get("use_sendevent", True):
            lo = self.config.get("touch_duration_min", 60)
            hi = self.config.get("touch_duration_max", 220)
            duration = random.randint(lo, hi)
            self._sendevent_tap(x, y, duration)
        else:
            self._adb_shell(f"input tap {x} {y}")

    def tap_fold(self, delay: bool = True):
        """Tap the Fold button."""
        if not self.enabled:
            print("  [ADB] Auto-input disabled (use --auto-play to enable)")
            return False
        if delay:
            self._human_delay()
        btn = self.config["buttons"]["fold"]
        self.tap(btn["x"], btn["y"])
        print(f"  [ADB] Tapped FOLD at ({btn['x']}, {btn['y']})")
        return True

    def tap_allin(self, delay: bool = True):
        """Tap the AllIn button."""
        if not self.enabled:
            print("  [ADB] Auto-input disabled (use --auto-play to enable)")
            return False
        if delay:
            self._human_delay()
        btn = self.config["buttons"]["allin"]
        self.tap(btn["x"], btn["y"])
        print(f"  [ADB] Tapped ALL IN at ({btn['x']}, {btn['y']})")
        return True

    def tap_leave(self, delay: bool = True):
        """Tap the Leave/Back button, then confirm dialog."""
        if not self.enabled:
            print("  [ADB] Auto-input disabled")
            return False
        if delay:
            self._human_delay()
        btn = self.config["buttons"]["leave"]
        self.tap(btn["x"], btn["y"])
        print(f"  [ADB] Tapped LEAVE at ({btn['x']}, {btn['y']})")
        # Wait for confirm dialog
        time.sleep(1.0)
        confirm = self.config["buttons"]["leave_confirm"]
        self.tap(confirm["x"], confirm["y"])
        print(f"  [ADB] Tapped LEAVE CONFIRM at ({confirm['x']}, {confirm['y']})")
        return True

    def _human_delay(self):
        """Log-normal delay to match human reaction time distribution.

        Human reaction times follow a log-normal distribution:
        - Most actions: 0.8-2.0s (reading cards, deciding)
        - Occasional slow: 3-5s (harder decisions, distractions)
        - Never instant: floor at 0.4s
        """
        mu = self.config.get("delay_mu", 0.2)
        sigma = self.config.get("delay_sigma", 0.5)
        floor = self.config.get("delay_floor", 0.4)
        cap = self.config.get("delay_cap", 6.0)

        delay = random.lognormvariate(mu, sigma)
        delay = max(floor, min(delay, cap))

        # Occasional hesitation (like re-reading cards or getting distracted)
        if random.random() < self.config.get("hesitation_prob", 0.15):
            delay += random.uniform(1.0, 3.0)
            delay = min(delay, cap)

        time.sleep(delay)

    def set_button(self, name: str, x: int, y: int):
        """Set button coordinates."""
        if name in self.config["buttons"]:
            self.config["buttons"][name]["x"] = x
            self.config["buttons"][name]["y"] = y
            self._save_config()
            print(f"  [ADB] {name} button set to ({x}, {y})")
        else:
            print(f"  [ADB] Unknown button: {name}")

    def calibrate(self):
        """Interactive calibration: take screenshot and let user set coordinates."""
        print("=== ADB Button Calibration ===")
        print("Taking screenshot...")
        path = self.screenshot()
        print(f"Screenshot saved to: {path}")
        print("Open the screenshot and find the button coordinates.")
        print()

        for btn_name in ("fold", "allin"):
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

    def test_tap(self, button: str = "fold"):
        """Test a single tap without delay."""
        if button == "fold":
            btn = self.config["buttons"]["fold"]
        elif button == "allin":
            btn = self.config["buttons"]["allin"]
        else:
            print(f"Unknown button: {button}")
            return
        print(f"Tapping {button} at ({btn['x']}, {btn['y']})...")
        self.tap(btn["x"], btn["y"], jitter=False)
        print("Done.")


# CLI for calibration and testing
if __name__ == "__main__":
    import argparse
    parser = argparse.ArgumentParser(description="ADB auto-input for PPPoker")
    parser.add_argument("--calibrate", action="store_true", help="Calibrate button positions")
    parser.add_argument("--screenshot", action="store_true", help="Take a screenshot")
    parser.add_argument("--test", choices=["fold", "allin"], help="Test tap a button")
    parser.add_argument("--adb", default=DEFAULT_ADB, help="Path to adb executable")
    args = parser.parse_args()

    ctrl = AdbController(adb_path=args.adb)
    if not ctrl.check_connection():
        print("ERROR: No ADB device connected")
        exit(1)
    print(f"Connected to device: {ctrl.device_serial}")

    if args.screenshot:
        path = ctrl.screenshot()
        print(f"Screenshot: {path}")
    elif args.calibrate:
        ctrl.calibrate()
    elif args.test:
        ctrl.test_tap(args.test)
    else:
        parser.print_help()
