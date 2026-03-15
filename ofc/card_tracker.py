"""Card tracking for OFC overlay: known cards, remaining deck, discards."""

from typing import Dict, List, Set, Tuple


# Card decoding (same as ofc_capture.py)
RANK_MAP = {2:"2",3:"3",4:"4",5:"5",6:"6",7:"7",8:"8",9:"9",
            10:"T",11:"J",12:"Q",13:"K",14:"A"}
SUIT_MAP = {1:"d", 2:"c", 3:"h", 4:"s"}

# Joker raw values (PPPoker encodes 2 jokers with non-standard suit/rank)
JOKER_VALUES = {1295, 1552}  # 0x050F, 0x0610
JOKER_LABEL = "JK"  # Display label for jokers

RANKS_DISPLAY = ["A","K","Q","J","T","9","8","7","6","5","4","3","2"]
SUITS_ORDER = ["s", "h", "d", "c"]

# Full 52-card deck (jokers tracked separately)
FULL_DECK: Set[str] = set()
for _r in RANK_MAP.values():
    for _s in SUIT_MAP.values():
        FULL_DECK.add(_r + _s)


def decode_card(raw: int) -> str:
    """Decode PPPoker card integer: card = (suit << 8) | rank."""
    if raw <= 0:
        return ""
    if raw in JOKER_VALUES:
        return JOKER_LABEL
    rank_val = raw & 0xFF
    suit_val = (raw >> 8) & 0xFF
    r = RANK_MAP.get(rank_val, "")
    s = SUIT_MAP.get(suit_val, "")
    if not r or not s:
        return ""
    return r + s


def is_known_card(raw: int) -> bool:
    """Check if raw card value is a real card or joker (not hidden/zero)."""
    if raw <= 0:
        return False
    if raw in JOKER_VALUES:
        return True
    rank = raw & 0xFF
    suit = (raw >> 8) & 0xFF
    return 2 <= rank <= 14 and 1 <= suit <= 4


# Keep old name for backward compat
is_real_card = is_known_card


class CardTracker:
    """Tracks known cards and computes remaining deck for OFC overlay."""

    def __init__(self, hero_uid: int = 0):
        self.hero_uid = hero_uid
        self._hero_detected = False
        self.reset()

    def reset(self):
        """Reset for a new hand."""
        self.round_num = 0
        self.hand_num = 0

        # Per-round dealt cards for hero (decoded strings)
        self.hero_dealt: Dict[int, List[str]] = {}  # round -> [cards]

        # Current board state (decoded strings)
        self.hero_board: Dict[str, List[str]] = {"top": [], "mid": [], "bot": []}
        self.opp_board: Dict[str, List[str]] = {"top": [], "mid": [], "bot": []}

        # Cumulative discards (decoded strings)
        self.hero_discard: List[str] = []
        self.opp_discard: List[str] = []  # only known at result

        # All known cards (standard deck cards as "As", "Kh", etc.)
        self._known_cards: Set[str] = set()
        # Joker tracking (how many jokers seen, max 2)
        self.jokers_seen: int = 0

        # Per-round opponent discards (filled at result)
        self.opp_discard_by_round: Dict[int, str] = {}  # round -> card
        self.hero_discard_by_round: Dict[int, str] = {}  # round -> card

    def _add_known(self, card_str: str):
        """Add a card to the known set. Handles jokers separately."""
        if not card_str:
            return
        if card_str == JOKER_LABEL:
            self.jokers_seen = min(self.jokers_seen + 1, 2)
        elif card_str in FULL_DECK:
            self._known_cards.add(card_str)

    def _add_known_list(self, cards: List[str]):
        """Add multiple cards to known set."""
        for c in cards:
            self._add_known(c)

    def on_game_start(self, data: dict):
        """Handle game_start event."""
        self.reset()
        self.hand_num = data.get("hand_num", 0)

    def on_hand_card(self, data: dict):
        """Handle hand_card event (PineHandCardBRC)."""
        uid = data.get("uid", 0)
        cards_raw = data.get("cards_raw", [])
        round_num = data.get("round", 0)
        self.round_num = round_num

        # Auto-detect hero: the player whose cards are real (not raw=0)
        real_cards = [c for c in cards_raw if is_known_card(c)]
        if real_cards and not self._hero_detected and self.hero_uid == 0:
            self.hero_uid = uid
            self._hero_detected = True

        is_hero = (uid == self.hero_uid) if self.hero_uid else len(real_cards) > 0

        # Also count jokers in dealt cards
        known_cards = [c for c in cards_raw if is_known_card(c)]
        if is_hero and known_cards:
            decoded = [decode_card(c) for c in known_cards]
            self.hero_dealt[round_num] = decoded
            self._add_known_list(decoded)

    def on_action(self, data: dict):
        """Handle action event (PineActionBRC)."""
        uid = data.get("uid", 0)
        top_raw = data.get("top_raw", [])
        mid_raw = data.get("mid_raw", [])
        bot_raw = data.get("bot_raw", [])
        discard_raw = data.get("discard_raw", [])

        is_hero = (uid == self.hero_uid) if self.hero_uid else False

        # Decode board cards (filter hidden zeros)
        top = [decode_card(c) for c in top_raw if is_known_card(c)]
        mid = [decode_card(c) for c in mid_raw if is_known_card(c)]
        bot = [decode_card(c) for c in bot_raw if is_known_card(c)]

        if is_hero:
            self.hero_board = {"top": top, "mid": mid, "bot": bot}
            # Hero discards are visible (cumulative)
            self.hero_discard = [decode_card(c) for c in discard_raw if is_known_card(c)]
            self._add_known_list(self.hero_discard)
            # Build per-round discard
            for i, c in enumerate(self.hero_discard):
                if c:
                    self.hero_discard_by_round[i + 2] = c  # R2, R3, R4, R5
        else:
            self.opp_board = {"top": top, "mid": mid, "bot": bot}
            # Opponent discards are hidden during play ([0,0,...])
            # but opponent board cards ARE visible in real-time

        # Add all visible board cards to known set
        self._add_known_list(top + mid + bot)

    def on_result(self, data: dict):
        """Handle result event (PineResultBRC) - reveals all cards."""
        players = data.get("players", [])

        for p in players:
            uid = p.get("uid", 0)
            top_raw = p.get("top_raw", [])
            mid_raw = p.get("mid_raw", [])
            bot_raw = p.get("bot_raw", [])
            discard_raw = p.get("discard_raw", [])

            top = [decode_card(c) for c in top_raw if is_known_card(c)]
            mid = [decode_card(c) for c in mid_raw if is_known_card(c)]
            bot = [decode_card(c) for c in bot_raw if is_known_card(c)]
            discards = [decode_card(c) for c in discard_raw if is_known_card(c)]

            is_hero = (uid == self.hero_uid) if self.hero_uid else False

            if is_hero:
                self.hero_board = {"top": top, "mid": mid, "bot": bot}
                self.hero_discard = discards
                for i, c in enumerate(discards):
                    if c:
                        self.hero_discard_by_round[i + 2] = c
            else:
                self.opp_board = {"top": top, "mid": mid, "bot": bot}
                self.opp_discard = discards
                for i, c in enumerate(discards):
                    if c:
                        self.opp_discard_by_round[i + 2] = c

            # All cards now known
            self._add_known_list(top + mid + bot + discards)

    def remaining_deck(self) -> Dict[str, List[str]]:
        """Return remaining cards grouped by suit, each sorted by rank."""
        remaining = FULL_DECK - self._known_cards
        by_suit: Dict[str, List[str]] = {s: [] for s in SUITS_ORDER}
        for card in remaining:
            if len(card) == 2:
                suit = card[1]
                if suit in by_suit:
                    by_suit[suit].append(card)
        # Sort each suit by rank order (A K Q J T 9 ... 2)
        rank_order = {r: i for i, r in enumerate(RANKS_DISPLAY)}
        for suit in by_suit:
            by_suit[suit].sort(key=lambda c: rank_order.get(c[0], 99))
        return by_suit

    def remaining_count(self) -> int:
        """Number of unseen cards (52 standard + 2 jokers = 54 max)."""
        standard_remaining = 52 - len(self._known_cards & FULL_DECK)
        joker_remaining = 2 - self.jokers_seen
        return standard_remaining + joker_remaining

    def known_count(self) -> int:
        """Number of seen cards."""
        return len(self._known_cards & FULL_DECK) + self.jokers_seen

    def is_remaining(self, card_str: str) -> bool:
        """Check if a specific card is still in the deck."""
        if card_str == JOKER_LABEL:
            return self.jokers_seen < 2
        return card_str in FULL_DECK and card_str not in self._known_cards

    def export_hand(self) -> dict:
        """Export current hand state as a structured dict for data logging."""
        return {
            "hand_num": self.hand_num,
            "hero_uid": self.hero_uid,
            "hero_board": {
                "top": self.hero_board["top"][:],
                "mid": self.hero_board["mid"][:],
                "bot": self.hero_board["bot"][:],
            },
            "opp_board": {
                "top": self.opp_board["top"][:],
                "mid": self.opp_board["mid"][:],
                "bot": self.opp_board["bot"][:],
            },
            "hero_discard": self.hero_discard[:],
            "opp_discard": self.opp_discard[:],
            "hero_discard_by_round": dict(self.hero_discard_by_round),
            "opp_discard_by_round": dict(self.opp_discard_by_round),
            "hero_dealt": {str(k): v[:] for k, v in self.hero_dealt.items()},
            "known_cards": sorted(self._known_cards),
            "jokers_seen": self.jokers_seen,
            "remaining_count": self.remaining_count(),
        }
