"""PPPoker AoF table configuration.

Defines screen regions and template paths for OCR.
Coordinates should be adjusted based on actual PPPoker window layout.
"""

from dataclasses import dataclass, field
from typing import Dict, Tuple, List

# API server URL
API_URL = "http://localhost:8080"

# Default game settings
DEFAULT_STACK_BB = 8.0
DEFAULT_NUM_PLAYERS = 4
RAKE_PCT = 0.02
RAKE_CAP_BB = 3.0


@dataclass
class Region:
    """Screen region defined by (x, y, width, height)."""
    x: int
    y: int
    w: int
    h: int

    @property
    def bbox(self) -> Tuple[int, int, int, int]:
        return (self.x, self.y, self.x + self.w, self.y + self.h)


@dataclass
class SeatConfig:
    """Configuration for a single seat on the table."""
    # Region for the player's cards (2 cards)
    card1: Region
    card2: Region
    # Region for the player's stack/chip count
    stack: Region
    # Region for the player's name
    name: Region
    # Region for action indicator (fold/allin text or visual)
    action: Region


@dataclass
class TableConfig:
    """Configuration for the entire table layout."""
    # Window title pattern for PPPoker
    window_title: str = "PPPoker"

    # Number of seats
    num_seats: int = 4

    # Seat configurations (indexed by position)
    seats: Dict[int, SeatConfig] = field(default_factory=dict)

    # Dealer button region
    dealer_button: Region = field(default_factory=lambda: Region(0, 0, 0, 0))

    # Action buttons
    fold_button: Region = field(default_factory=lambda: Region(0, 0, 0, 0))
    allin_button: Region = field(default_factory=lambda: Region(0, 0, 0, 0))

    # Pot display
    pot_region: Region = field(default_factory=lambda: Region(0, 0, 0, 0))

    # Board cards (5 community cards)
    board_cards: List[Region] = field(default_factory=list)

    # Template image directory
    template_dir: str = "tablemaps"

    # Polling interval in seconds
    poll_interval: float = 0.5

    # Action delay (seconds between detection and action)
    action_delay: float = 0.3


def create_default_config() -> TableConfig:
    """Create a default table configuration.

    NOTE: These coordinates are placeholders.
    They must be calibrated for the actual PPPoker window.
    Use the calibration tool to set correct values.
    """
    config = TableConfig()

    # Placeholder coordinates - must be calibrated
    # Seat 0 (bottom center - hero)
    config.seats[0] = SeatConfig(
        card1=Region(580, 500, 60, 80),
        card2=Region(645, 500, 60, 80),
        stack=Region(580, 585, 130, 25),
        name=Region(580, 560, 130, 25),
        action=Region(560, 480, 170, 25),
    )

    # Seat 1 (left)
    config.seats[1] = SeatConfig(
        card1=Region(200, 300, 50, 65),
        card2=Region(255, 300, 50, 65),
        stack=Region(200, 370, 110, 22),
        name=Region(200, 348, 110, 22),
        action=Region(180, 280, 150, 22),
    )

    # Seat 2 (top center)
    config.seats[2] = SeatConfig(
        card1=Region(580, 120, 50, 65),
        card2=Region(635, 120, 50, 65),
        stack=Region(580, 190, 110, 22),
        name=Region(580, 168, 110, 22),
        action=Region(560, 100, 150, 22),
    )

    # Seat 3 (right)
    config.seats[3] = SeatConfig(
        card1=Region(950, 300, 50, 65),
        card2=Region(1005, 300, 50, 65),
        stack=Region(950, 370, 110, 22),
        name=Region(950, 348, 110, 22),
        action=Region(930, 280, 150, 22),
    )

    # Buttons
    config.fold_button = Region(450, 650, 120, 50)
    config.allin_button = Region(730, 650, 120, 50)

    # Board cards
    config.board_cards = [
        Region(430, 270, 50, 65),
        Region(490, 270, 50, 65),
        Region(550, 270, 50, 65),
        Region(610, 270, 50, 65),
        Region(670, 270, 50, 65),
    ]

    # Pot
    config.pot_region = Region(540, 240, 120, 25)

    # Dealer button
    config.dealer_button = Region(0, 0, 30, 30)

    return config
