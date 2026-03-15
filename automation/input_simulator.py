"""Mouse/keyboard input simulation for PPPoker."""

import time
import random
import pyautogui
from config import Region


# Safety: prevent runaway clicks
pyautogui.FAILSAFE = True
pyautogui.PAUSE = 0.1


class InputSimulator:
    """Simulates mouse clicks on PPPoker buttons."""

    def __init__(self, humanize: bool = True):
        """
        Args:
            humanize: Add random delays and slight position offsets
                     to mimic human behavior.
        """
        self.humanize = humanize

    def click_button(self, region: Region):
        """Click the center of a button region.

        Args:
            region: Screen region of the button to click.
        """
        # Calculate center with optional random offset
        cx = region.x + region.w // 2
        cy = region.y + region.h // 2

        if self.humanize:
            # Random offset within 30% of button size
            offset_x = random.randint(-region.w // 5, region.w // 5)
            offset_y = random.randint(-region.h // 5, region.h // 5)
            cx += offset_x
            cy += offset_y

            # Random pre-click delay (100-500ms)
            time.sleep(random.uniform(0.1, 0.5))

        pyautogui.click(cx, cy)

        if self.humanize:
            # Random post-click delay (50-200ms)
            time.sleep(random.uniform(0.05, 0.2))

    def click_fold(self, fold_region: Region):
        """Click the fold button."""
        print("[ACTION] Clicking FOLD")
        self.click_button(fold_region)

    def click_allin(self, allin_region: Region):
        """Click the all-in button."""
        print("[ACTION] Clicking ALL-IN")
        self.click_button(allin_region)
