"""Precompute 169×169 preflop all-in equity table for AoF exploit calculations.

For each pair of hand types (e.g. AKs vs QQ), compute the equity (win probability)
when both go all-in preflop and 5 community cards are dealt.

Output: JSON file with equity[hand_a][hand_b] = win probability of hand_a.
"""

import random
import json
import time
import sys
from itertools import combinations

# ─── Card representation (same as sim_aof.py) ───
RANKS = "23456789TJQKA"
SUITS = "shdc"
RANK_VAL = {r: i for i, r in enumerate(RANKS)}

def card(r, s):
    return (RANK_VAL[r], s)

def card_str(c):
    return RANKS[c[0]] + c[1]

ALL_CARDS = [(r, s) for r in range(13) for s in SUITS]

# ─── Hand evaluator (compact 7-card) ───
def evaluate_hand_7(cards):
    ranks = sorted([c[0] for c in cards], reverse=True)
    suits = [c[1] for c in cards]
    
    # Count ranks
    rc = {}
    for r in ranks:
        rc[r] = rc.get(r, 0) + 1
    
    # Group by count
    groups = sorted(rc.items(), key=lambda x: (x[1], x[0]), reverse=True)
    
    # Flush check
    sc = {}
    for s in suits:
        sc[s] = sc.get(s, 0) + 1
    flush_suit = None
    for s, cnt in sc.items():
        if cnt >= 5:
            flush_suit = s
            break
    
    flush_ranks = None
    if flush_suit:
        flush_ranks = sorted([c[0] for c in cards if c[1] == flush_suit], reverse=True)[:5]
    
    # Straight check
    unique = sorted(set(ranks), reverse=True)
    if 12 in unique:  # Ace can be low
        unique_low = unique + [-1]  # A as low (value -1 for wheel)
    else:
        unique_low = unique
    
    def find_straight(vals):
        for i in range(len(vals) - 4):
            if vals[i] - vals[i+4] == 4:
                return vals[i]
        return None
    
    straight_high = find_straight(unique_low)
    
    # Straight flush check
    if flush_suit and flush_ranks:
        fr = sorted(set(c[0] for c in cards if c[1] == flush_suit), reverse=True)
        if 12 in fr:
            fr_low = fr + [-1]
        else:
            fr_low = fr
        sf_high = find_straight(fr_low)
        if sf_high is not None:
            return (8, sf_high)
    
    # Four of a kind
    if groups[0][1] == 4:
        kicker = max(r for r in ranks if r != groups[0][0])
        return (7, groups[0][0], kicker)
    
    # Full house
    if groups[0][1] == 3 and groups[1][1] >= 2:
        return (6, groups[0][0], groups[1][0])
    
    # Flush
    if flush_ranks:
        return (5,) + tuple(flush_ranks)
    
    # Straight
    if straight_high is not None:
        return (4, straight_high)
    
    # Three of a kind
    if groups[0][1] == 3:
        kickers = sorted([r for r in ranks if r != groups[0][0]], reverse=True)[:2]
        return (3, groups[0][0]) + tuple(kickers)
    
    # Two pair
    if groups[0][1] == 2 and groups[1][1] == 2:
        p1 = max(groups[0][0], groups[1][0])
        p2 = min(groups[0][0], groups[1][0])
        kicker = max(r for r in ranks if r != p1 and r != p2)
        return (2, p1, p2, kicker)
    
    # One pair
    if groups[0][1] == 2:
        kickers = sorted([r for r in ranks if r != groups[0][0]], reverse=True)[:3]
        return (1, groups[0][0]) + tuple(kickers)
    
    # High card
    return (0,) + tuple(ranks[:5])


# ─── 169 hand types ───
ALL_169 = []
for i in range(13):
    ALL_169.append(RANKS[i] * 2)  # pairs
for i in range(13):
    for j in range(i + 1, 13):
        ALL_169.append(RANKS[j] + RANKS[i] + "s")  # suited (higher rank first)
        ALL_169.append(RANKS[j] + RANKS[i] + "o")  # offsuit

def hand_to_cards(hand_type):
    """Convert hand type to a specific card combo (avoiding conflicts)."""
    if len(hand_type) == 2:  # pair
        r = RANK_VAL[hand_type[0]]
        return [(r, 's'), (r, 'h')]
    else:
        r1 = RANK_VAL[hand_type[0]]
        r2 = RANK_VAL[hand_type[1]]
        if hand_type[2] == 's':
            return [(r1, 's'), (r2, 's')]
        else:
            return [(r1, 's'), (r2, 'h')]

def hand_to_cards_avoiding(hand_type, blocked_cards):
    """Convert hand type to cards that don't conflict with blocked_cards."""
    blocked = set((c[0], c[1]) for c in blocked_cards)
    r1 = RANK_VAL[hand_type[0]]
    
    if len(hand_type) == 2:  # pair
        r = r1
        available_suits = [s for s in SUITS if (r, s) not in blocked]
        if len(available_suits) < 2:
            return None
        return [(r, available_suits[0]), (r, available_suits[1])]
    else:
        r2 = RANK_VAL[hand_type[1]]
        if hand_type[2] == 's':
            for s in SUITS:
                if (r1, s) not in blocked and (r2, s) not in blocked:
                    return [(r1, s), (r2, s)]
            return None
        else:
            for s1 in SUITS:
                for s2 in SUITS:
                    if s1 != s2 and (r1, s1) not in blocked and (r2, s2) not in blocked:
                        return [(r1, s1), (r2, s2)]
            return None


def compute_equity(hand_a_type, hand_b_type, n_samples=5000):
    """Compute equity of hand_a vs hand_b via Monte Carlo."""
    cards_a = hand_to_cards(hand_a_type)
    cards_b = hand_to_cards_avoiding(hand_b_type, cards_a)
    
    if cards_b is None:
        return 0.5  # Can't deal non-conflicting cards (shouldn't happen for different types)
    
    used = set((c[0], c[1]) for c in cards_a + cards_b)
    deck = [(r, s) for r in range(13) for s in SUITS if (r, s) not in used]
    
    wins_a = 0
    wins_b = 0
    ties = 0
    
    for _ in range(n_samples):
        board = random.sample(deck, 5)
        score_a = evaluate_hand_7(cards_a + board)
        score_b = evaluate_hand_7(cards_b + board)
        if score_a > score_b:
            wins_a += 1
        elif score_b > score_a:
            wins_b += 1
        else:
            ties += 1
    
    total = wins_a + wins_b + ties
    return (wins_a + ties * 0.5) / total


def main():
    n_samples = int(sys.argv[1]) if len(sys.argv) > 1 else 3000
    total_pairs = len(ALL_169) * len(ALL_169)
    
    print(f"Computing {len(ALL_169)}×{len(ALL_169)} = {total_pairs:,} equity pairs")
    print(f"Samples per pair: {n_samples}")
    print(f"Estimated time: ~{total_pairs * n_samples * 2 * 15e-6 / 60:.0f} minutes")
    print()
    
    random.seed(42)
    equity_table = {}
    t0 = time.time()
    done = 0
    
    for i, ha in enumerate(ALL_169):
        equity_table[ha] = {}
        for j, hb in enumerate(ALL_169):
            if ha == hb:
                equity_table[ha][hb] = 0.5
            elif hb in equity_table and ha in equity_table[hb]:
                # Use symmetry: equity(A vs B) = 1 - equity(B vs A)
                equity_table[ha][hb] = 1.0 - equity_table[hb][ha]
            else:
                equity_table[ha][hb] = compute_equity(ha, hb, n_samples)
            
            done += 1
            if done % 1000 == 0:
                el = time.time() - t0
                eta = el / done * (total_pairs - done)
                print(f"\r  {done:,}/{total_pairs:,} ({done/total_pairs*100:.1f}%) "
                      f"elapsed={el:.0f}s ETA={eta:.0f}s", end="", flush=True)
    
    elapsed = time.time() - t0
    print(f"\n\nDone in {elapsed:.1f}s ({elapsed/60:.1f} min)")
    
    # Save
    out_path = "d:/aof_bot/solver/data/equity_table_169.json"
    with open(out_path, "w") as f:
        json.dump(equity_table, f, indent=1)
    print(f"Saved to {out_path}")
    
    # Quick sanity check
    print("\nSanity checks:")
    print(f"  AA vs KK: {equity_table['AA']['KK']:.3f}")
    print(f"  AKs vs QQ: {equity_table['AKs']['QQ']:.3f}")
    print(f"  72o vs AA: {equity_table['72o']['AA']:.3f}")
    print(f"  AKs vs AKo: {equity_table['AKs']['AKo']:.3f}")


if __name__ == "__main__":
    main()
