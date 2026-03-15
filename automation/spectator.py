"""Spectator mode: watches AoF tables and records hand histories.

Usage:
    python spectator.py                     # Run with default settings
    python spectator.py --debug             # Show debug windows
    python spectator.py --table-region 510,0,460,850  # Manual table region
"""

import argparse
import json
import time
import sys
from pathlib import Path
from dataclasses import dataclass
from typing import Optional, List, Tuple

import cv2
import numpy as np
import mss

from card_recognizer import recognize_card, is_card_present, detect_suit, detect_rank
from hand_db import HandRecord, init_db, save_hand, get_player_stats


SCREENSHOT_DIR = Path("screenshots")
CALIBRATION_FILE = Path("calibration.json")


@dataclass
class TableRegion:
    """Defines the table area within the PPPoker window."""
    x: int
    y: int
    w: int
    h: int

    # Relative positions within the table (as fractions of table w/h)
    # Derived from PPPoker screenshot analysis:
    #   Hero (bottom): cards ~center-bottom
    #   Opponent (top, HU): cards ~center-top
    #   Board: 5 cards in center row

    # Hero cards (bottom player)
    hero_card1: Tuple[float, float, float, float] = (0.38, 0.82, 0.14, 0.09)
    hero_card2: Tuple[float, float, float, float] = (0.50, 0.82, 0.14, 0.09)
    hero_name: Tuple[float, float, float, float] = (0.32, 0.92, 0.36, 0.04)
    hero_stack: Tuple[float, float, float, float] = (0.38, 0.96, 0.24, 0.04)

    # Top opponent (seat across from hero, used in HU/all layouts)
    opp_top_card1: Tuple[float, float, float, float] = (0.42, 0.06, 0.10, 0.08)
    opp_top_card2: Tuple[float, float, float, float] = (0.52, 0.06, 0.10, 0.08)
    opp_top_name: Tuple[float, float, float, float] = (0.35, 0.18, 0.30, 0.04)
    opp_top_stack: Tuple[float, float, float, float] = (0.40, 0.22, 0.20, 0.04)

    # Left opponent (3p/4p)
    opp_left_card1: Tuple[float, float, float, float] = (0.02, 0.40, 0.10, 0.08)
    opp_left_card2: Tuple[float, float, float, float] = (0.12, 0.40, 0.10, 0.08)
    opp_left_name: Tuple[float, float, float, float] = (0.02, 0.50, 0.22, 0.04)
    opp_left_stack: Tuple[float, float, float, float] = (0.05, 0.54, 0.18, 0.04)

    # Right opponent (4p)
    opp_right_card1: Tuple[float, float, float, float] = (0.78, 0.40, 0.10, 0.08)
    opp_right_card2: Tuple[float, float, float, float] = (0.88, 0.40, 0.10, 0.08)
    opp_right_name: Tuple[float, float, float, float] = (0.76, 0.50, 0.22, 0.04)
    opp_right_stack: Tuple[float, float, float, float] = (0.78, 0.54, 0.18, 0.04)

    # Board cards (5 cards in center)
    board_card1: Tuple[float, float, float, float] = (0.16, 0.40, 0.10, 0.10)
    board_card5: Tuple[float, float, float, float] = (0.72, 0.40, 0.10, 0.10)

    # Dealer button region
    dealer_region: Tuple[float, float, float, float] = (0.0, 0.0, 1.0, 1.0)

    # Win amount text region (shows "+XX.X" near winner)
    win_text: Tuple[float, float, float, float] = (0.30, 0.75, 0.40, 0.06)

    # Blinds text region
    blinds_text: Tuple[float, float, float, float] = (0.15, 0.50, 0.70, 0.05)

    def abs_region(self, rel: Tuple[float, float, float, float]) -> Tuple[int, int, int, int]:
        """Convert relative (fraction) region to absolute pixel coordinates."""
        rx, ry, rw, rh = rel
        return (
            self.x + int(rx * self.w),
            self.y + int(ry * self.h),
            int(rw * self.w),
            int(rh * self.h),
        )

    def crop(self, img: np.ndarray, rel: Tuple[float, float, float, float]) -> np.ndarray:
        """Crop a relative region from the full screen image."""
        ax, ay, aw, ah = self.abs_region(rel)
        return img[ay:ay + ah, ax:ax + aw]


@dataclass
class ShowdownState:
    """Captured state at showdown."""
    player_cards: List[Optional[str]]  # Per seat: "AhKd" or None
    player_names: List[str]
    player_stacks: List[float]
    board: List[str]                   # 5 board cards
    win_amount: Optional[float]
    dealer_seat: int
    num_players: int
    bb_size: float


class SpectatorLoop:
    """Main spectator loop: watches table and records showdowns."""

    def __init__(self, table_region: TableRegion, debug: bool = False):
        self.table = table_region
        self.debug = debug
        self.sct = mss.mss()
        self.prev_board_hash = None
        self.hands_recorded = 0
        self._ocr_reader = None

    def capture_screen(self) -> np.ndarray:
        """Capture the full primary monitor."""
        monitor = self.sct.monitors[1]
        img = self.sct.grab(monitor)
        return np.array(img)[:, :, :3]  # BGRA -> BGR

    def capture_table(self) -> np.ndarray:
        """Capture just the table region."""
        monitor = {
            "left": self.table.x,
            "top": self.table.y,
            "width": self.table.w,
            "height": self.table.h,
        }
        img = self.sct.grab(monitor)
        return np.array(img)[:, :, :3]

    def detect_showdown(self, screen: np.ndarray) -> bool:
        """Detect if a showdown is happening (board cards visible)."""
        # Check if board card regions have actual cards
        board_cards_present = 0
        for i in range(5):
            # Interpolate board card positions between card1 and card5
            frac = i / 4.0
            bx1, by1, bw1, bh1 = self.table.board_card1
            bx5, by5, bw5, bh5 = self.table.board_card5
            rel = (
                bx1 + (bx5 - bx1) * frac,
                by1 + (by5 - by1) * frac,
                bw1 + (bw5 - bw1) * frac,
                bh1 + (bh5 - bh1) * frac,
            )
            card_img = self.table.crop(screen, rel)
            if is_card_present(card_img):
                board_cards_present += 1

        return board_cards_present >= 4  # At least 4 of 5 board cards visible

    def compute_board_hash(self, screen: np.ndarray) -> int:
        """Compute a hash of the board card region to detect changes."""
        # Crop the entire board area
        bx1, by1 = self.table.board_card1[:2]
        bx5, by5 = self.table.board_card5[:2]
        bw5, bh5 = self.table.board_card5[2:]
        board_area = self.table.crop(screen, (
            bx1, by1, bx5 - bx1 + bw5, bh5
        ))
        # Downsample and hash
        small = cv2.resize(board_area, (32, 8))
        return hash(small.tobytes())

    def read_board_cards(self, screen: np.ndarray) -> List[str]:
        """Read all 5 board cards."""
        cards = []
        for i in range(5):
            frac = i / 4.0
            bx1, by1, bw1, bh1 = self.table.board_card1
            bx5, by5, bw5, bh5 = self.table.board_card5
            rel = (
                bx1 + (bx5 - bx1) * frac,
                by1 + (by5 - by1) * frac,
                bw1 + (bw5 - bw1) * frac,
                bh1 + (bh5 - bh1) * frac,
            )
            card_img = self.table.crop(screen, rel)
            card = recognize_card(card_img)
            if card:
                cards.append(card)

            if self.debug:
                label = card or "?"
                cv2.imshow(f"Board {i + 1}: {label}", card_img)
        return cards

    def read_player_cards(self, screen: np.ndarray,
                          card1_rel: tuple, card2_rel: tuple) -> Optional[str]:
        """Read a player's two hole cards."""
        img1 = self.table.crop(screen, card1_rel)
        img2 = self.table.crop(screen, card2_rel)

        if not is_card_present(img1) or not is_card_present(img2):
            return None

        c1 = recognize_card(img1)
        c2 = recognize_card(img2)

        if self.debug:
            cv2.imshow(f"Card1: {c1}", img1)
            cv2.imshow(f"Card2: {c2}", img2)

        if c1 and c2:
            return c1 + c2
        return None

    def read_text_region(self, screen: np.ndarray, rel: tuple) -> str:
        """Read text from a region using OCR."""
        img = self.table.crop(screen, rel)
        if img.size == 0:
            return ""

        gray = cv2.cvtColor(img, cv2.COLOR_BGR2GRAY)

        try:
            import pytesseract
            text = pytesseract.image_to_string(gray, config="--psm 7").strip()
            return text
        except ImportError:
            pass

        try:
            if self._ocr_reader is None:
                import easyocr
                self._ocr_reader = easyocr.Reader(["en"], gpu=False, verbose=False)
            results = self._ocr_reader.readtext(gray, detail=0)
            return " ".join(results).strip()
        except ImportError:
            pass

        return ""

    def read_number(self, screen: np.ndarray, rel: tuple) -> Optional[float]:
        """Read a number from a region."""
        text = self.read_text_region(screen, rel)
        if not text:
            return None
        # Clean up
        cleaned = text.replace(",", "").replace(" ", "").replace("+", "")
        try:
            return float(cleaned)
        except ValueError:
            return None

    def detect_dealer_seat(self, screen: np.ndarray) -> int:
        """Detect which seat has the dealer button.

        Looks for the 'D' marker near each player position.
        """
        # For now, check brightness of dealer button indicator near each seat
        # The D button is a small circle with 'D' text
        # In the screenshots it appears near the player's cards

        # Simplified: check for 'D' text near each player
        # TODO: improve with template matching for the D button
        return 0  # Default

    def read_showdown(self, screen: np.ndarray) -> Optional[ShowdownState]:
        """Read all data at showdown."""
        board = self.read_board_cards(screen)
        if len(board) < 4:
            return None

        # Read hero cards
        hero_cards = self.read_player_cards(
            screen, self.table.hero_card1, self.table.hero_card2)

        # Read opponent cards (top)
        opp_top_cards = self.read_player_cards(
            screen, self.table.opp_top_card1, self.table.opp_top_card2)

        # Read names
        hero_name = self.read_text_region(screen, self.table.hero_name)
        opp_top_name = self.read_text_region(screen, self.table.opp_top_name)

        # Read stacks
        hero_stack = self.read_number(screen, self.table.hero_stack) or 0
        opp_top_stack = self.read_number(screen, self.table.opp_top_stack) or 0

        # Win amount
        win_amount = self.read_number(screen, self.table.win_text)

        # Determine actions from what we can see:
        # In AoF showdown, if cards are visible = all-in, if not = folded
        actions = []
        player_cards = []
        player_names = []
        player_stacks = []

        # Seat 0: hero (bottom)
        player_names.append(hero_name)
        player_stacks.append(hero_stack)
        if hero_cards:
            actions.append("A")
            player_cards.append(hero_cards)
        else:
            actions.append("F")
            player_cards.append(None)

        # Seat 1: top opponent
        player_names.append(opp_top_name)
        player_stacks.append(opp_top_stack)
        if opp_top_cards:
            actions.append("A")
            player_cards.append(opp_top_cards)
        else:
            actions.append("F")
            player_cards.append(None)

        # TODO: read left/right opponents for 3p/4p tables

        # BB size from blinds text
        bb_size = 20.0  # Default, TODO: parse from blinds text

        dealer = self.detect_dealer_seat(screen)

        return ShowdownState(
            player_cards=player_cards,
            player_names=player_names,
            player_stacks=player_stacks,
            board=board,
            win_amount=win_amount,
            dealer_seat=dealer,
            num_players=len(player_names),
            bb_size=bb_size,
        )

    def record_showdown(self, state: ShowdownState):
        """Save showdown data to database."""
        now = time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())

        # Determine winner (player with visible cards + higher stack or win amount)
        winner = -1
        allin_count = sum(1 for c in state.player_cards if c is not None)
        if allin_count == 1:
            # Only one went all-in, they won by fold
            winner = next(i for i, c in enumerate(state.player_cards) if c is not None)

        actions_str = ",".join("A" if c else "F" for c in state.player_cards)
        cards_str = ",".join(c or "" for c in state.player_cards)
        names_str = ",".join(state.player_names)
        stacks_str = ",".join(f"{s:.1f}" for s in state.player_stacks)
        board_str = "".join(state.board)

        record = HandRecord(
            timestamp=now,
            table_id="",
            num_players=state.num_players,
            bb_size=state.bb_size,
            dealer_seat=state.dealer_seat,
            player_ids=names_str,
            stacks=stacks_str,
            actions=actions_str,
            cards=cards_str,
            board=board_str,
            winner_seat=winner,
            pot_chips=state.win_amount or 0,
        )

        hand_id = save_hand(record)
        self.hands_recorded += 1

        # Print summary
        print(f"\n[Hand #{hand_id}] {now}")
        for i, (name, cards, action) in enumerate(
                zip(state.player_names, state.player_cards,
                    ["A" if c else "F" for c in state.player_cards])):
            marker = "(*)" if i == winner else "   "
            print(f"  {marker} {name}: {action} {cards or ''}")
        print(f"  Board: {' '.join(state.board)}")
        if state.win_amount:
            print(f"  Pot: {state.win_amount:.1f}")
        print(f"  Total hands recorded: {self.hands_recorded}")

    def run(self, poll_interval: float = 0.5):
        """Main spectator loop."""
        print("=" * 50)
        print("  AoF Spectator Mode")
        print("=" * 50)
        print(f"  Table region: ({self.table.x}, {self.table.y}) "
              f"{self.table.w}x{self.table.h}")
        print(f"  Poll interval: {poll_interval}s")
        print(f"  Debug: {self.debug}")
        print("=" * 50)
        print("\nWatching for showdowns... Press Ctrl+C to stop.\n")

        init_db()

        try:
            while True:
                screen = self.capture_screen()

                if self.detect_showdown(screen):
                    board_hash = self.compute_board_hash(screen)

                    # Only record if this is a new showdown (different board)
                    if board_hash != self.prev_board_hash:
                        self.prev_board_hash = board_hash

                        # Wait a moment for all cards to be fully displayed
                        time.sleep(0.3)
                        screen = self.capture_screen()  # Re-capture

                        state = self.read_showdown(screen)
                        if state and any(c is not None for c in state.player_cards):
                            self.record_showdown(state)

                            # Save debug screenshot
                            if self.debug:
                                SCREENSHOT_DIR.mkdir(exist_ok=True)
                                ts = time.strftime("%Y%m%d_%H%M%S")
                                cv2.imwrite(
                                    str(SCREENSHOT_DIR / f"showdown_{ts}.png"),
                                    screen)

                if self.debug:
                    key = cv2.waitKey(1) & 0xFF
                    if key == ord('q'):
                        break
                else:
                    time.sleep(poll_interval)

        except KeyboardInterrupt:
            print(f"\nStopped. Total hands recorded: {self.hands_recorded}")

        if self.debug:
            cv2.destroyAllWindows()


def main():
    parser = argparse.ArgumentParser(description="AoF Spectator Mode")
    parser.add_argument("--debug", action="store_true",
                        help="Show debug windows with card crops")
    parser.add_argument("--table-region", type=str, default=None,
                        help="Table region as x,y,w,h (e.g., 510,0,460,850)")
    parser.add_argument("--poll", type=float, default=0.5,
                        help="Poll interval in seconds")
    args = parser.parse_args()

    # Determine table region
    if args.table_region:
        parts = [int(x) for x in args.table_region.split(",")]
        table = TableRegion(x=parts[0], y=parts[1], w=parts[2], h=parts[3])
    elif CALIBRATION_FILE.exists():
        with open(CALIBRATION_FILE) as f:
            cal = json.load(f)
        # Use calibration data to determine table region
        print(f"Loaded calibration from {CALIBRATION_FILE}")
        # Default table region based on PPPoker layout
        table = TableRegion(x=510, y=0, w=460, h=850)
    else:
        # Default: right half of PPPoker window (from screenshot analysis)
        print("No calibration found. Using default table region.")
        print("Run 'python calibrate.py pick' to calibrate, or use --table-region")
        table = TableRegion(x=510, y=0, w=460, h=850)

    spectator = SpectatorLoop(table, debug=args.debug)
    spectator.run(poll_interval=args.poll)


if __name__ == "__main__":
    main()
