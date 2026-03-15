"""GTO chart lookup for AoF decisions.

Loads pre-computed GTO charts from JSON and provides push/fold decisions
based on hand, position, and prior actions.

Usage:
    from gto_lookup import GtoLookup
    gto = GtoLookup("d:/aof_bot/solver/data/charts_rb50")
    decision = gto.should_push(hand="AKs", num_players=4, position="UTG", prior_actions="")
"""

import json
from pathlib import Path
from typing import Optional

# Map card integers (PPPoker format) to standard notation
# PPPoker: suit*13 + rank, where suit 0=spade,1=heart,2=diamond,3=club
# rank: 0=2, 1=3, ..., 8=T, 9=J, 10=Q, 11=K, 12=A
RANKS = "23456789TJQKA"
SUITS = "shdc"


def card_int_to_str(card_int: int) -> str:
    """Convert PPPoker card integer to string like 'As', 'Kh'."""
    suit = card_int // 13
    rank = card_int % 13
    return RANKS[rank] + SUITS[suit]


def cards_to_hand_name(card1: str, card2: str) -> str:
    """Convert two card strings (e.g. 'As', 'Kh') to hand name (e.g. 'AKo').
    
    Returns canonical hand name: pairs like 'AA', suited like 'AKs', offsuit like 'AKo'.
    """
    r1, s1 = card1[0], card1[1]
    r2, s2 = card2[0], card2[1]
    
    rank_order = "AKQJT98765432"
    idx1 = rank_order.index(r1)
    idx2 = rank_order.index(r2)
    
    # Sort so higher rank comes first
    if idx1 > idx2:
        r1, r2 = r2, r1
        s1, s2 = s2, s1
    
    if r1 == r2:
        return r1 + r2  # Pair
    elif s1 == s2:
        return r1 + r2 + "s"  # Suited
    else:
        return r1 + r2 + "o"  # Offsuit


class GtoLookup:
    """Lookup push/fold decisions from pre-computed GTO charts."""
    
    def __init__(self, charts_dir: str = "d:/aof_bot/solver/data/charts_rb50"):
        self.charts_dir = Path(charts_dir)
        self.charts = {}  # {num_players: [chart_nodes]}
        self._load_charts()
    
    def _load_charts(self):
        """Load all available chart files."""
        for np in [2, 3, 4]:
            path = self.charts_dir / f"aof_{np}p_8bb.json"
            if path.exists():
                with open(path) as f:
                    data = json.load(f)
                    self.charts[np] = data.get("charts", [])
                print(f"  [GTO] Loaded {np}p chart: {len(self.charts[np])} situations")
    
    def _find_chart(self, num_players: int, position: str, prior_actions: str) -> Optional[dict]:
        """Find the matching chart node for the given situation."""
        charts = self.charts.get(num_players, [])
        
        for chart in charts:
            chart_pos = chart.get("position", "")
            chart_prior = chart.get("prior_actions", "")
            
            if chart_pos.upper() == position.upper() and chart_prior == prior_actions:
                return chart
        
        # Fallback: try matching just position
        for chart in charts:
            if chart.get("position", "").upper() == position.upper():
                desc = chart.get("description", "")
                # Try to match prior actions from description
                if prior_actions == "" and "first" in desc.lower():
                    return chart
                if prior_actions == "F" and "fold" in desc.lower():
                    return chart
        
        return None
    
    def get_push_freq(self, hand_name: str, num_players: int, position: str, prior_actions: str = "") -> float:
        """Get the GTO push frequency for a given hand and situation.
        
        Args:
            hand_name: e.g. "AKs", "77", "T9o"
            num_players: 2, 3, or 4
            position: "UTG", "BTN", "SB", "BB"
            prior_actions: e.g. "" (first to act), "F" (one fold), "A" (one push), "FF" etc.
        
        Returns:
            Push frequency (0.0 to 1.0). Returns -1 if situation not found.
        """
        chart = self._find_chart(num_players, position, prior_actions)
        if chart is None:
            print(f"  [GTO] WARNING: No chart found for {num_players}p {position} prior={prior_actions}")
            return -1
        
        entries = chart.get("entries", [])
        for entry in entries:
            if entry.get("hand", "") == hand_name:
                return entry.get("allin_freq", 0.0)
        
        return 0.0  # Hand not in chart = never push
    
    def should_push(self, hand_name: str, num_players: int, position: str, 
                    prior_actions: str = "", threshold: float = 0.5) -> bool:
        """Determine if we should push (True) or fold (False).
        
        Uses threshold of 0.5 by default (push if GTO frequency > 50%).
        """
        freq = self.get_push_freq(hand_name, num_players, position, prior_actions)
        if freq < 0:
            print(f"  [GTO] Situation not found, defaulting to FOLD")
            return False
        
        decision = freq >= threshold
        action = "PUSH" if decision else "FOLD"
        print(f"  [GTO] {hand_name} @ {num_players}p {position} (prior={prior_actions}): "
              f"freq={freq*100:.1f}% → {action}")
        return decision


if __name__ == "__main__":
    gto = GtoLookup()
    
    # Test some hands
    test_hands = [
        ("AA",  4, "UTG", ""),
        ("AKs", 4, "UTG", ""),
        ("72o", 4, "UTG", ""),
        ("QTs", 4, "BTN", "F"),
        ("55",  2, "SB",  ""),
    ]
    
    print("\n=== GTO Lookup Test ===")
    for hand, np, pos, prior in test_hands:
        gto.should_push(hand, np, pos, prior)
