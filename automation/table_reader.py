"""Reads the full table state from PPPoker screen."""

from dataclasses import dataclass
from typing import Optional, List
from config import TableConfig, Region
from ocr import ScreenCapture, CardRecognizer, TextRecognizer, ActionDetector


@dataclass
class PlayerState:
    """State of a single player at the table."""
    seat: int
    name: str
    stack_bb: Optional[float]
    card1: Optional[str]  # e.g. "Ac"
    card2: Optional[str]  # e.g. "Kd"
    action: Optional[str]  # 'F' (fold), 'A' (allin), or None (not acted yet)
    is_hero: bool


@dataclass
class TableState:
    """Complete table state for one moment in time."""
    players: List[PlayerState]
    is_our_turn: bool
    hero_seat: int
    num_active_players: int
    pot_bb: Optional[float]
    board_cards: List[str]
    dealer_seat: Optional[int]

    @property
    def hero(self) -> Optional[PlayerState]:
        """Get hero's state."""
        for p in self.players:
            if p.is_hero:
                return p
        return None

    @property
    def hero_hand(self) -> Optional[str]:
        """Get hero's hand as a 4-char string, e.g. 'AcKd'."""
        h = self.hero
        if h and h.card1 and h.card2:
            return h.card1 + h.card2
        return None

    @property
    def prior_actions(self) -> str:
        """Get prior actions string (before hero's turn)."""
        result = []
        for p in self.players:
            if p.is_hero:
                break
            if p.action:
                result.append(p.action)
        return "".join(result)

    @property
    def opponent_ids(self) -> List[str]:
        """Get non-hero player IDs."""
        return [p.name for p in self.players if not p.is_hero and p.name]


class TableReader:
    """Reads table state from the screen using OCR."""

    def __init__(self, config: TableConfig):
        self.config = config
        self.capture = ScreenCapture()
        self.card_recognizer = CardRecognizer(config.template_dir)
        self.text_recognizer = TextRecognizer()
        self.action_detector = ActionDetector()
        self.hero_seat = 0  # Default: hero sits at seat 0 (bottom)

    def read_table(self) -> TableState:
        """Read the complete table state from the current screen."""
        players = []

        for seat_idx in range(self.config.num_seats):
            if seat_idx not in self.config.seats:
                continue

            seat = self.config.seats[seat_idx]
            is_hero = seat_idx == self.hero_seat

            # Read player name
            name_img = self.capture.capture_region(seat.name)
            name = self.text_recognizer.read_text(name_img)

            # Read stack
            stack_img = self.capture.capture_region(seat.stack)
            stack = self.text_recognizer.read_number(stack_img)

            # Read cards (only visible for hero, or at showdown)
            card1_img = self.capture.capture_region(seat.card1)
            card1 = self.card_recognizer.recognize_card(card1_img)

            card2_img = self.capture.capture_region(seat.card2)
            card2 = self.card_recognizer.recognize_card(card2_img)

            # Detect action
            action_img = self.capture.capture_region(seat.action)
            action = self.action_detector.detect_player_action(action_img)

            players.append(PlayerState(
                seat=seat_idx,
                name=name,
                stack_bb=stack,
                card1=card1,
                card2=card2,
                action=action,
                is_hero=is_hero,
            ))

        # Check if it's our turn
        allin_img = self.capture.capture_region(self.config.allin_button)
        is_our_turn = self.action_detector.is_button_visible(allin_img, "allin")

        # Read pot
        pot_img = self.capture.capture_region(self.config.pot_region)
        pot = self.text_recognizer.read_number(pot_img)

        # Read board cards (for showdown display)
        board = []
        for card_region in self.config.board_cards:
            card_img = self.capture.capture_region(card_region)
            card = self.card_recognizer.recognize_card(card_img)
            if card:
                board.append(card)

        num_active = sum(1 for p in players if p.action != 'F')

        return TableState(
            players=players,
            is_our_turn=is_our_turn,
            hero_seat=self.hero_seat,
            num_active_players=num_active,
            pot_bb=pot,
            board_cards=board,
            dealer_seat=None,
        )
