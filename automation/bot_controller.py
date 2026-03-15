"""Bot controller: decides actions and communicates with the solver API."""

import time
import requests
from typing import Optional
from config import API_URL, DEFAULT_NUM_PLAYERS, DEFAULT_STACK_BB
from table_reader import TableState


class BotController:
    """Main bot logic: reads table state and decides actions."""

    def __init__(self, api_url: str = API_URL, use_exploit: bool = True):
        self.api_url = api_url
        self.use_exploit = use_exploit

    def decide(self, state: TableState) -> Optional[str]:
        """Decide whether to fold or go all-in.

        Returns 'fold', 'allin', or None if not our turn.
        """
        if not state.is_our_turn:
            return None

        hero = state.hero
        if not hero or not hero.card1 or not hero.card2:
            return None  # Can't read cards

        hand = hero.card1 + hero.card2
        num_players = state.num_active_players
        stack = hero.stack_bb or DEFAULT_STACK_BB
        prior_actions = state.prior_actions

        if self.use_exploit:
            return self._exploit_decision(hand, num_players, stack, prior_actions, state)
        else:
            return self._gto_decision(hand, num_players, stack, prior_actions)

    def _gto_decision(self, hand: str, num_players: int, stack: float, prior_actions: str) -> str:
        """Get GTO decision from the solver API."""
        try:
            resp = requests.get(f"{self.api_url}/gto", params={
                "hand": hand,
                "num_players": num_players,
                "stack": stack,
                "prior_actions": prior_actions,
            }, timeout=2)

            if resp.status_code == 200:
                data = resp.json()
                action = data.get("action", "Fold")
                prob = data.get("allin_probability", 0)
                print(f"[GTO] {hand} | {num_players}p {stack:.1f}BB | prior={prior_actions} | "
                      f"allin={prob:.1%} -> {action}")
                return "allin" if action == "AllIn" else "fold"
        except requests.RequestException as e:
            print(f"[API Error] {e}")

        # Fallback: fold
        return "fold"

    def _exploit_decision(self, hand: str, num_players: int, stack: float,
                          prior_actions: str, state: TableState) -> str:
        """Get exploitative decision from the solver API."""
        opponent_ids = ",".join(state.opponent_ids)

        try:
            resp = requests.get(f"{self.api_url}/exploit", params={
                "hand": hand,
                "num_players": num_players,
                "stack": stack,
                "prior_actions": prior_actions,
                "player_ids": opponent_ids,
            }, timeout=2)

            if resp.status_code == 200:
                data = resp.json()
                action = data.get("action", "Fold")
                gto = data.get("gto_allin", 0)
                exploit = data.get("exploit_allin", 0)
                blended = data.get("blended_allin", 0)
                conf = data.get("confidence", 0)
                print(f"[EXPLOIT] {hand} | {num_players}p {stack:.1f}BB | "
                      f"gto={gto:.1%} exploit={exploit:.1%} blend={blended:.1%} "
                      f"conf={conf:.2f} -> {action}")
                return "allin" if action == "AllIn" else "fold"
        except requests.RequestException as e:
            print(f"[API Error] {e}, falling back to GTO")

        return self._gto_decision(hand, num_players, stack, prior_actions)

    def record_hand(self, state: TableState, actions: str):
        """Record a completed hand to the database via API."""
        try:
            record = {
                "timestamp": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
                "num_players": len(state.players),
                "stack_bb": state.hero.stack_bb or DEFAULT_STACK_BB if state.hero else DEFAULT_STACK_BB,
                "player_ids": [p.name for p in state.players],
                "actions": actions,
                "showdown_cards": [
                    (p.card1 or "") + (p.card2 or "") if p.card1 else ""
                    for p in state.players
                ],
                "pot_bb": state.pot_bb or 0,
            }

            resp = requests.post(f"{self.api_url}/record_hand", json=record, timeout=2)
            if resp.status_code == 200:
                print(f"[DB] Hand recorded: {actions}")
            else:
                print(f"[DB Error] {resp.status_code}: {resp.text}")
        except requests.RequestException as e:
            print(f"[DB Error] {e}")
