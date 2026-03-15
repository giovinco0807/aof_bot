"""Analyze observed player data vs GTO — by player count + situation.

Matches each observed action to exact (num_players, position, prior_actions) GTO chart.
Uses hand_players table for accurate physical seat mapping.
"""

import sqlite3
import json
from pathlib import Path
from collections import defaultdict

DB_PATH = Path("d:/aof_bot/automation/data/hands.db")
CHARTS_DIR = Path("d:/aof_bot/solver/data/charts_rb50")

# Suspected bots (near 100% VPIP) — exclude from analysis
BLACKLIST = {"13226424", "13393284"}

POS_BY_NP = {
    2: ["SB", "BB"],
    3: ["BTN", "SB", "BB"],
    4: ["CO", "BTN", "SB", "BB"],
}


def load_all_gto():
    """Load all GTO charts. Key = (np, position, prior)."""
    # Total combos across all 169 hand types = 1326
    # Pairs: 13 * 6 = 78, Suited: 78 * 4 = 312, Offsuit: 78 * 12 = 936
    TOTAL_COMBOS = 1326

    gto = {}
    for np in [2, 3, 4]:
        path = CHARTS_DIR / f"aof_{np}p_8bb.json"
        if not path.exists():
            continue
        with open(path) as f:
            data = json.load(f)
        for c in data["charts"]:
            key = (np, c["position"], c["prior_actions"])
            push_combos = 0
            for e in c["entries"]:
                combos = 6 if len(e["hand"]) == 2 else (4 if e["hand"].endswith("s") else 12)
                push_combos += e["allin_freq"] * combos
            gto[key] = {
                "push_rate": push_combos / TOTAL_COMBOS,
                "push_hands": len(c["entries"]),
                "desc": c.get("description", ""),
            }
    return gto


def analyze():
    conn = sqlite3.connect(str(DB_PATH))

    # Load all hands (including dealer_seat=-1)
    hand_rows = conn.execute("""
        SELECT h.id, h.num_players, h.dealer_seat
        FROM hands h
        WHERE h.num_players IN (2, 3, 4)
    """).fetchall()
    print(f"Total hands: {len(hand_rows)}")

    # Count by np
    np_counts = defaultdict(int)
    for _, np, _ in hand_rows:
        np_counts[np] += 1
    for np, cnt in sorted(np_counts.items()):
        print(f"  {np}P: {cnt} hands")

    gto = load_all_gto()
    print(f"\nGTO charts loaded: {len(gto)}")
    for k in sorted(gto.keys()):
        np, pos, prior = k
        print(f"  {np}P {pos:3s} prior='{prior:<5s}' → GTO push {gto[k]['push_rate']*100:5.1f}%  ({gto[k]['desc']})")

    # Load hand_players grouped by hand_id
    hp_rows = conn.execute("""
        SELECT hp.hand_id, hp.player_id, hp.seat_id, hp.action
        FROM hand_players hp
        JOIN hands h ON h.id = hp.hand_id
        WHERE h.num_players IN (2, 3, 4)
        ORDER BY hp.hand_id, hp.seat_id
    """).fetchall()
    conn.close()

    # Group by hand_id
    hands_map = defaultdict(list)
    for hid, pid, sid, act in hp_rows:
        hands_map[hid].append((pid.strip() if pid else "", sid, act.strip().upper() if act else "?"))

    # player -> (np, pos, prior) -> {seen, pushed}
    ps = defaultdict(lambda: defaultdict(lambda: {"seen": 0, "pushed": 0}))
    pt = defaultdict(lambda: {"seen": 0, "pushed": 0})

    for hid, np, dealer_seat in hand_rows:
        positions = POS_BY_NP.get(np)
        if not positions or hid not in hands_map:
            continue

        players = hands_map[hid]
        if len(players) != np:
            continue

        # Sort by physical seat_id
        players_sorted = sorted(players, key=lambda x: x[1])
        seat_ids = [p[1] for p in players_sorted]
        acts = [p[2] for p in players_sorted]

        # Determine dealer index(es) and weight(s)
        dealer_options = []  # [(dealer_idx, weight), ...]

        if dealer_seat >= 0:
            # Known dealer
            for idx, sid in enumerate(seat_ids):
                if sid == dealer_seat:
                    dealer_options = [(idx, 1.0)]
                    break
        elif np == 2:
            # Infer from 2P action pattern
            a0, a1 = acts[0], acts[1]
            if a0 in ('A', 'F') and a1 == '?':
                dealer_options = [(0, 1.0)]  # sorted_idx 0 = SB
            elif a1 in ('A', 'F') and a0 == '?':
                dealer_options = [(1, 1.0)]  # sorted_idx 1 = SB
            elif a0 == 'A' and a1 == 'F':
                dealer_options = [(0, 1.0)]  # SB pushed, BB folded
            elif a0 == 'F' and a1 == 'A':
                dealer_options = [(1, 1.0)]  # SB pushed, BB folded
            elif a0 == 'A' and a1 == 'A':
                dealer_options = [(0, 0.5), (1, 0.5)]  # ambiguous
            # else: skip

        if not dealer_options:
            continue

        for dealer_idx, weight in dealer_options:
            # dealer_idx = BB's index in sorted player list (PPPoker convention)
            # Map positions relative to BB:
            #   4P: CO(BB-3), BTN(BB-2), SB(BB-1), BB(BB)
            #   3P: BTN(BB-2), SB(BB-1), BB(BB)
            #   2P: SB(BB-1), BB(BB)
            bb_idx = dealer_idx
            pos_to_idx = {}
            for i, pos in enumerate(positions):
                # positions[-1] is always BB → offset = -(np-1) + i from BB
                offset = -(np - 1) + i
                pos_to_idx[pos] = (bb_idx + offset) % np

            prior = ""
            for pos in positions:
                idx = pos_to_idx[pos]
                pid, _, act = players_sorted[idx]

                if not pid or act == "?":
                    prior += "?"
                    continue

                if pid in BLACKLIST:
                    prior += "A" if act == "A" else "F"
                    continue

                pushed = act == "A"
                sit_key = (np, pos, prior)

                if sit_key in gto:
                    ps[pid][sit_key]["seen"] += weight
                    if pushed:
                        ps[pid][sit_key]["pushed"] += weight
                    pt[pid]["seen"] += weight
                    if pushed:
                        pt[pid]["pushed"] += weight

                prior += "A" if pushed else "F"

    # ── Results ──
    sorted_players = sorted(pt.items(), key=lambda x: x[1]["seen"], reverse=True)

    for pid, total in sorted_players:
        if total["seen"] < 50:
            continue

        vpip = total["pushed"] / total["seen"] * 100

        print(f"\n{'='*90}")
        print(f"Player {pid}  |  {total['seen']} situations  |  Overall VPIP: {vpip:.1f}%")
        print(f"{'='*90}")
        print(f"  {'NP':>2} {'Situation':<22} {'Seen':>5} {'Push':>5} {'Rate':>6} {'GTO':>6} {'Diff':>7}  {'Tendency'}")
        print(f"  {'-'*80}")

        weighted_dev = 0.0
        weight = 0

        for sit_key in sorted(gto.keys()):
            s = ps[pid].get(sit_key)
            if not s or s["seen"] == 0:
                continue

            np, pos, prior = sit_key
            rate = s["pushed"] / s["seen"] * 100
            gto_rate = gto[sit_key]["push_rate"] * 100
            diff = rate - gto_rate

            if abs(diff) < 3:     tend = "≈GTO"
            elif diff > 15:       tend = "⚠ VERY LOOSE"
            elif diff > 5:        tend = "↑ Loose"
            elif diff < -15:      tend = "⚠ VERY TIGHT"
            elif diff < -5:       tend = "↓ Tight"
            else:                 tend = "~"

            label = f"{pos} prior='{prior}'"
            print(f"  {np}P {label:<22} {s['seen']:>5} {s['pushed']:>5} {rate:>5.1f}% {gto_rate:>5.1f}% {diff:>+6.1f}%  {tend}")

            weighted_dev += diff * s["seen"]
            weight += s["seen"]

        if weight > 0:
            avg = weighted_dev / weight
            tend = "LOOSE" if avg > 2 else "TIGHT" if avg < -2 else "≈GTO"
            print(f"  {'-'*80}")
            print(f"     {'WEIGHTED AVG':<22} {weight:>5} {'':>5} {'':>6} {'':>6} {avg:>+6.1f}%  {tend}")

    print(f"\n{'='*90}")


if __name__ == "__main__":
    analyze()
