"""Build AoF GTO chart JSON files from user-provided range notation.

Range notation examples:
  "22+, A2s+, A2o+, K2s+"   -> all these hands at 100%
  "Q4o:0.999"               -> Q4o at 99.9%
  "T3s:0.111"               -> T3s at 11.1%

Usage:
  python build_charts.py
  -> Writes aof_2p_8bb.json, aof_3p_8bb.json, aof_4p_8bb.json
"""

import json
import re
from pathlib import Path

OUTPUT_DIR = Path("d:/aof_bot/solver/data/charts_rb50")

RANKS = "23456789TJQKA"
RANK_ORDER = "AKQJT98765432"  # High to low

# hand_index mapping: same as the solver
# Pairs: 22=0, 33=1, ..., AA=12
# Suited: index = high*13 + low - high*(high+1)/2 ... 
# Actually let's build it from the solver's scheme:
# hand_index for pairs: rank_index (0=22, 1=33, ..., 12=AA)
# hand_index for suited: 13 + ...
# Let me reconstruct from the existing JSON

def rank_idx(r: str) -> int:
    """Rank char to index: 2=0, 3=1, ..., A=12"""
    return RANKS.index(r)

def hand_to_index(hand: str) -> int:
    """Convert hand name to solver hand_index.
    
    Pairs: AA=12, KK=11, ..., 22=0
    Suited: high_rank * 13 + low_rank (with offset)
    Offsuit: similar with different offset
    
    From the solver code:
      Pairs: rank (0-12)
      Suited: 13 + (high * (high-1)/2 + low) 
      Offsuit: 13 + 78 + (high * (high-1)/2 + low)
    
    Actually from existing data: AA=12, AKs=90, AKo=168
    Let me reverse-engineer the formula:
      Pairs: rank_idx (2=0,...,A=12) 
      Suited: 13 + high*(high-1)//2 + low  where high=rank_idx(higher), low=rank_idx(lower)
      Offsuit: 91 + high*(high-1)//2 + low
    
    Check: AKs -> high=12(A), low=11(K) -> 13 + 12*11/2 + 11 = 13 + 66 + 11 = 90 ✓
    Check: AKo -> 91 + 66 + 11 = 168 ✓
    Check: A2s -> high=12, low=0 -> 13 + 66 + 0 = 79 ✓
    """
    if len(hand) == 2:
        # Pair
        return rank_idx(hand[0])
    
    r1, r2, suit = hand[0], hand[1], hand[2]
    high = max(rank_idx(r1), rank_idx(r2))
    low = min(rank_idx(r1), rank_idx(r2))
    
    if suit == 's':
        return 13 + high * (high - 1) // 2 + low
    else:  # 'o'
        return 91 + high * (high - 1) // 2 + low


def all_hand_names():
    """Generate all 169 canonical hand names."""
    hands = []
    # Pairs
    for r in RANKS:
        hands.append(r + r)
    # Suited
    for i in range(len(RANKS)):
        for j in range(i):
            hands.append(RANKS[i] + RANKS[j] + 's')
    # Offsuit
    for i in range(len(RANKS)):
        for j in range(i):
            hands.append(RANKS[i] + RANKS[j] + 'o')
    return hands


def parse_range(range_str: str) -> dict:
    """Parse a range string like '22+, A2s+, K3o+, Q4o:0.999' into {hand: freq}.
    
    Returns dict mapping hand name -> allin frequency (0.0 to 1.0).
    """
    result = {}
    
    # Split by comma
    parts = [p.strip() for p in range_str.split(',')]
    
    for part in parts:
        if not part:
            continue
        
        # Check for frequency suffix like ":0.999"
        freq = 1.0
        if ':' in part:
            part, freq_str = part.rsplit(':', 1)
            freq = float(freq_str)
        
        # Check for '+' suffix (range)
        is_plus = part.endswith('+')
        if is_plus:
            part = part[:-1]
        
        if len(part) == 2 and part[0] == part[1]:
            # Pair like "22" or "22+"
            r = rank_idx(part[0])
            if is_plus:
                for ri in range(r, 13):
                    result[RANKS[ri] + RANKS[ri]] = freq
            else:
                result[part] = freq
        
        elif len(part) == 3 and part[2] in ('s', 'o'):
            # Suited/offsuit like "A2s" or "K3o"
            high_r = part[0]
            low_r = part[1]
            suit = part[2]
            
            high_idx = rank_idx(high_r)
            low_idx = rank_idx(low_r)
            
            if is_plus:
                # e.g. "A2s+" means A2s, A3s, A4s, ..., A(high-1)s (keeping high fixed)
                # e.g. "K3o+" means K3o, K4o, ..., KQo
                for li in range(low_idx, high_idx):
                    hand = RANKS[high_idx] + RANKS[li] + suit
                    result[hand] = freq
            else:
                result[part] = freq
        
        else:
            print(f"WARNING: Could not parse range part: '{part}'")
    
    return result


def build_chart_entries(range_dict: dict) -> list:
    """Convert a {hand: freq} dict into sorted chart entries."""
    entries = []
    for hand, freq in sorted(range_dict.items(), key=lambda x: -x[1]):
        if freq > 0.0001:  # Skip near-zero entries
            entries.append({
                "hand": hand,
                "hand_index": hand_to_index(hand),
                "allin_freq": freq
            })
    return entries


def build_json(num_players: int, charts_data: list) -> dict:
    """Build the full JSON structure.
    
    charts_data: list of (position, prior_actions, description, range_str)
    """
    charts = []
    for pos, prior, desc, range_str in charts_data:
        range_dict = parse_range(range_str)
        entries = build_chart_entries(range_dict)
        charts.append({
            "position": pos,
            "prior_actions": prior,
            "description": desc,
            "entries": entries
        })
    
    return {
        "num_players": num_players,
        "stack_bb": 8.0,
        "structure": "SB=0.5 BB=1 Ante=0",
        "charts": charts
    }


# ──────────────────────────────────────────────────────────────────
# User-provided ranges
# ──────────────────────────────────────────────────────────────────

RANGES_2P = [
    ("SB", "", "SB opens (first to act)",
     "22+, A2s+, A2o+, K2s+, K2o+, Q2s+, Q5o+, Q4o:0.999, J2s+, J7o+, T4s+, T3s:0.111, T7o+, 95s+, 97o+, 84s+, 87o, 86o:0.004, 74s+, 76o, 64s+, 63s:0.015, 53s+, 43s:0.999"),
    ("BB", "A", "BB faces SB push",
     "22+, A2s+, A2o+, K2s+, K3o+, K2o:0.211, Q4s+, Q8o+, J7s+, J9o+, T8s+, T9o:0.870, 98s:0.277"),
]

# Placeholder for 3p and 4p - user will provide later
RANGES_3P = [
    # BTN opens (first to act)
    ("BTN", "", "BTN opens (first to act)",
     "22+, A2s+, A2o+, K5s+, K4s:0.990, K9o+, Q8s+, Q7s:0.003, QTo+, J8s+, J7s:0.547, JTo, T7s+, T9o:0.602, 97s+, 86s+, 76s, 65s:0.972"),
    # SB faces BTN push
    ("SB", "A", "SB faces BTN push",
     "33+, A2s+, A6o+, A5o:0.996, K9s+, KTo+, QTs+"),
    # BB faces BTN push + SB push
    ("BB", "AA", "BB faces BTN+SB push",
     "44+, A8s+, A7s:0.979, ATo+, KTs+, K9s:0.005, KJo+, QTs+, Q9s:0.999, QJo:0.002, J9s+, T9s, 98s:0.920"),
    # BB faces BTN push, SB fold
    ("BB", "AF", "BB faces BTN push after SB fold",
     "22+, A2s+, A2o+, K7s+, KTo+, K9o:0.074, QTs+, Q9s:0.999, QJo, QTo:0.678, JTs"),
    # SB opens after BTN fold
    ("SB", "F", "SB opens after BTN fold",
     "22+, A2s+, A2o+, K2s+, K2o+, Q2s+, Q5o+, J3s+, J2s:0.996, J8o+, J7o:0.862, T4s+, T3s:0.055, T7o+, 95s+, 97o+, 84s+, 87o, 74s+, 76o, 64s+, 63s:0.001, 53s+, 43s:0.366"),
    # BB faces SB push after BTN fold
    ("BB", "FA", "BB faces SB push after BTN fold",
     "22+, A2s+, A2o+, K2s+, K3o+, Q5s+, Q4s:0.001, Q8o+, J8s+, J7s:0.305, J9o+, T8s+"),
]
RANGES_4P = [
    # CO opens (first to act)
    ("CO", "", "CO opens (first to act)",
     "22+, A2s+, A4o+, A3o:0.934, K9s+, K8s:0.977, K7s:0.020, KTo+, Q9s+, Q8s:0.014, QJo, QTo:0.720, J8s+, JTo, T8s+, 98s, 87s:0.999"),
    # BTN faces CO push
    ("BTN", "A", "BTN faces CO push",
     "55+, 44:0.955, A7s+, A9o+, KJs+, KQo:0.993"),
    # SB faces CO+BTN push
    ("SB", "AA", "SB faces CO+BTN push",
     "66+, ATs+, AJo+, KJs+, KTs:0.003, QJs:0.845, JTs:0.009"),
    # BB faces CO+BTN+SB push
    ("BB", "AAA", "BB faces CO+BTN+SB push",
     "77+, 66:0.964, AQs+, AJs:0.995, A9s:0.001, AKo, AQo:0.378, KQs:0.997, KJs:0.998, KTs:0.962, K8s:0.001, KQo:0.002, QJs:0.845, QTs:0.337, Q9s:0.001, JTs:0.898, J9s:0.001, T9s:0.854, 98s:0.003, 97s:0.001, 87s:0.128, 86s:0.002, 85s:0.001, 76s:0.008, 75s:0.001, 65s:0.208, 64s:0.001"),
    # BB faces CO+BTN push, SB fold
    ("BB", "AAF", "BB faces CO+BTN push, SB fold",
     "55+, ATs+, AJo+, KTs+, KQo, QTs+, JTs, J9s:0.001, T9s:0.027"),
    # SB faces CO push, BTN fold
    ("SB", "AF", "SB faces CO push, BTN fold",
     "33+, A4s+, A8o+, KTs+, KQo, KJo:0.981, QJs"),
    # BB faces CO push + SB push, BTN fold
    ("BB", "AFA", "BB faces CO+SB push, BTN fold",
     "55+, 44:0.986, A9s+, ATo+, KTs+, KQo, QTs+, Q9s:0.013, JTs, T9s:0.930"),
    # BB faces CO push, BTN+SB fold
    ("BB", "AFF", "BB faces CO push, BTN+SB fold",
     "22+, A2s+, A5o+, A4o:0.410, K9s+, KTo+, QTs+, QJo:0.055, JTs:0.955"),
    # BTN opens after CO fold
    ("BTN", "F", "BTN opens after CO fold",
     "22+, A2s+, A2o+, K5s+, K4s:0.513, KTo+, K9o:0.852, Q8s+, Q7s:0.002, QTo+, J8s+, J7s:0.981, JTo, T7s+, T9o:0.387, 97s+, 86s+, 76s, 65s:0.016"),
    # SB faces BTN push after CO fold
    ("SB", "FA", "SB faces BTN push after CO fold",
     "33+, A2s+, A7o+, A6o:0.837, A5o:0.002, KTs+, K9s:0.991, KTo+, QTs+"),
    # BB faces BTN+SB push after CO fold
    ("BB", "FAA", "BB faces BTN+SB push after CO fold",
     "44+, 33:0.001, A8s+, A7s:0.003, ATo+, A9o:0.001, KTs+, KQo, KJo:0.999, QTs+, Q9s:0.194, JTs, J9s:0.999, T9s:0.978"),
    # BB faces BTN push, SB fold, after CO fold
    ("BB", "FAF", "BB faces BTN push after CO+SB fold",
     "22+, A2s+, A2o+, K8s+, K7s:0.017, KTo+, QTs+, Q9s:0.550, QJo, JTs"),
    # SB opens after CO+BTN fold
    ("SB", "FF", "SB opens after CO+BTN fold",
     "22+, A2s+, A2o+, K2s+, K2o+, Q2s+, Q5o+, Q4o:0.001, J2s+, J8o+, J7o:0.220, T4s+, T3s:0.982, T8o+, T7o:0.999, 95s+, 97o+, 85s+, 84s:0.006, 87o, 75s+, 74s:0.999, 76o:0.923, 64s+, 53s+, 43s:0.003"),
    # BB faces SB push after CO+BTN fold
    ("BB", "FFA", "BB faces SB push after CO+BTN fold",
     "22+, A2s+, A2o+, K2s+, K4o+, K3o:0.032, Q6s+, Q5s:0.488, Q8o+, J8s+, J9o+, T8s+"),
]


def main():
    OUTPUT_DIR.mkdir(parents=True, exist_ok=True)
    
    # Build 2-player chart
    if RANGES_2P:
        data_2p = build_json(2, RANGES_2P)
        out_path = OUTPUT_DIR / "aof_2p_8bb.json"
        with open(out_path, 'w') as f:
            json.dump(data_2p, f, indent=2)
        
        # Count hands per position
        for chart in data_2p["charts"]:
            pos = chart["position"]
            n = len(chart["entries"])
            total_freq = sum(e["allin_freq"] for e in chart["entries"])
            print(f"  2P {pos}: {n} hands, total weighted combos ~ {total_freq:.1f}")
        print(f"  Saved to {out_path}")
    
    # Build 3-player chart
    if RANGES_3P:
        data_3p = build_json(3, RANGES_3P)
        out_path = OUTPUT_DIR / "aof_3p_8bb.json"
        with open(out_path, 'w') as f:
            json.dump(data_3p, f, indent=2)
        print(f"  Saved to {out_path}")
    
    # Build 4-player chart
    if RANGES_4P:
        data_4p = build_json(4, RANGES_4P)
        out_path = OUTPUT_DIR / "aof_4p_8bb.json"
        with open(out_path, 'w') as f:
            json.dump(data_4p, f, indent=2)
        print(f"  Saved to {out_path}")
    
    print("\nDone! Charts generated.")
    
    # Verification: print the range in a readable format
    if RANGES_2P:
        print("\n=== 2P Verification ===")
        data_2p = build_json(2, RANGES_2P)
        for chart in data_2p["charts"]:
            pos = chart["position"]
            entries = chart["entries"]
            print(f"\n{pos} ({len(entries)} hands):")
            for e in entries:
                freq_str = f"{e['allin_freq']*100:.1f}%"
                if e['allin_freq'] >= 0.999:
                    freq_str = "100%"
                print(f"  {e['hand']:4s} idx={e['hand_index']:3d} freq={freq_str}")


if __name__ == "__main__":
    main()
