"""Records hand histories by monitoring the table state transitions."""

import time
from typing import Optional, List
from table_reader import TableState, PlayerState
from bot_controller import BotController


class HandRecorder:
    """Monitors table state and records completed hands."""

    def __init__(self, bot: BotController):
        self.bot = bot
        self.previous_state: Optional[TableState] = None
        self.current_actions: str = ""
        self.hand_active: bool = False

    def update(self, state: TableState):
        """Update with a new table state snapshot.

        Detects hand transitions and records completed hands.
        """
        if self.previous_state is None:
            self.previous_state = state
            return

        # Detect new hand start (e.g., hero gets new cards)
        if self._is_new_hand(state):
            # Record previous hand if it was active
            if self.hand_active and self.current_actions:
                self.bot.record_hand(self.previous_state, self.current_actions)

            # Start new hand
            self.current_actions = ""
            self.hand_active = True

        # Track actions
        if self.hand_active:
            self._track_actions(state)

        # Detect showdown (board cards visible)
        if self.hand_active and len(state.board_cards) >= 5:
            # Wait a moment for showdown cards to display
            pass

        self.previous_state = state

    def _is_new_hand(self, state: TableState) -> bool:
        """Detect if a new hand has started."""
        prev = self.previous_state
        if not prev:
            return True

        # New hand if hero's cards changed
        hero = state.hero
        prev_hero = prev.hero
        if hero and prev_hero:
            if (hero.card1 != prev_hero.card1 or hero.card2 != prev_hero.card2):
                if hero.card1 and hero.card2:
                    return True

        return False

    def _track_actions(self, state: TableState):
        """Track new actions in the current hand."""
        new_actions = []
        for p in state.players:
            if p.action and len(self.current_actions) <= p.seat:
                new_actions.append(p.action)

        if new_actions:
            self.current_actions = "".join(
                p.action or "?" for p in state.players if p.action is not None
            )
