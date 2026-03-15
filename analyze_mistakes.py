"""Analyze per-player, per-position GTO mistakes and save to JSON."""
import sqlite3, json
from pathlib import Path
from collections import defaultdict
from datetime import datetime

CHARTS_DIR = Path("d:/aof_bot/solver/data/charts_rb50")
DB_PATH = "d:/aof_bot/automation/data/hands.db"
OUTPUT_PATH = Path("d:/aof_bot/automation/data/player_mistakes.json")
RANK_ORDER = "23456789TJQKA"
BLACKLIST = {"13226424", "13393284"}

POS_ORDER = {2: ["SB", "BB"], 3: ["BTN", "SB", "BB"], 4: ["CO", "BTN", "SB", "BB"]}


def cards_to_hand_type(cards_str):
    if len(cards_str) != 4:
        return None
    r1, s1, r2, s2 = cards_str[0], cards_str[1], cards_str[2], cards_str[3]
    i1 = RANK_ORDER.index(r1) if r1 in RANK_ORDER else -1
    i2 = RANK_ORDER.index(r2) if r2 in RANK_ORDER else -1
    if i1 < 0 or i2 < 0:
        return None
    if i1 < i2:
        r1, r2, s1, s2 = r2, r1, s2, s1
    if r1 == r2:
        return f"{r1}{r2}"
    return f"{r1}{r2}s" if s1 == s2 else f"{r1}{r2}o"


def load_gto():
    gto = {}
    for np in [2, 3, 4]:
        path = CHARTS_DIR / f"aof_{np}p_8bb.json"
        if not path.exists():
            continue
        with open(path) as f:
            data = json.load(f)
        for c in data["charts"]:
            key = f"{np}P_{c['position']}_{c['prior_actions']}"
            gto[key] = {e["hand"]: e["allin_freq"] for e in c["entries"]}
    return gto


def analyze_and_save():
    gto = load_gto()
    conn = sqlite3.connect(DB_PATH, timeout=5)

    # Load all hand_players with position
    all_hp = conn.execute("""
        SELECT hp.hand_id, hp.player_id, hp.position, hp.action, hp.cards, h.num_players
        FROM hand_players hp
        JOIN hands h ON h.id = hp.hand_id
        WHERE hp.position != ''
        ORDER BY hp.hand_id
    """).fetchall()
    conn.close()

    # Group by hand for prior computation
    hands = defaultdict(list)
    for hid, pid, pos, act, cards, np in all_hp:
        hands[hid].append({"pid": pid, "pos": pos, "act": act, "cards": cards, "np": np})

    # Per player -> per situation -> mistakes
    results = {}

    for hid, players in hands.items():
        np = players[0]["np"]
        positions = POS_ORDER.get(np, [])
        if not positions:
            continue

        pos_map = {p["pos"]: p for p in players}
        for p in players:
            if p["pid"] in BLACKLIST or not p["cards"]:
                continue

            ht = cards_to_hand_type(p["cards"])
            if not ht:
                continue

            # Compute prior
            prior = ""
            for pos in positions:
                if pos == p["pos"]:
                    break
                other = pos_map.get(pos)
                if other:
                    prior += "A" if other["act"] == "A" else "F"

            sit_key = f"{np}P_{p['pos']}_{prior}"
            if sit_key not in gto:
                continue

            gto_freq = gto[sit_key].get(ht, 0.0)
            pushed = p["act"] == "A"
            pid = p["pid"]

            if pid not in results:
                results[pid] = {"total_showdowns": 0, "total_mistakes": 0, "situations": {}}

            results[pid]["total_showdowns"] += 1

            if sit_key not in results[pid]["situations"]:
                results[pid]["situations"][sit_key] = {
                    "showdowns": 0, "mistakes": 0, "mistake_hands": [], "correct_hands": []
                }

            sit = results[pid]["situations"][sit_key]
            sit["showdowns"] += 1

            is_mistake = (pushed and gto_freq < 0.5) or (not pushed and gto_freq >= 0.8)

            if is_mistake:
                sit["mistakes"] += 1
                results[pid]["total_mistakes"] += 1
                sit["mistake_hands"].append({
                    "hand": ht, "raw": p["cards"],
                    "action": "PUSH" if pushed else "FOLD",
                    "gto_freq": round(gto_freq, 2),
                })
            else:
                sit["correct_hands"].append({
                    "hand": ht, "action": "PUSH" if pushed else "FOLD",
                    "gto_freq": round(gto_freq, 2),
                })

    # Add summary
    output = {
        "updated_at": datetime.now().isoformat(),
        "total_players": len(results),
        "players": results,
    }

    OUTPUT_PATH.parent.mkdir(parents=True, exist_ok=True)
    with open(OUTPUT_PATH, "w", encoding="utf-8") as f:
        json.dump(output, f, indent=2, ensure_ascii=False)

    print(f"Saved to {OUTPUT_PATH}")
    print(f"Players: {len(results)}")
    for pid in sorted(results, key=lambda x: results[x]["total_showdowns"], reverse=True):
        r = results[pid]
        rate = r["total_mistakes"] / r["total_showdowns"] * 100 if r["total_showdowns"] > 0 else 0
        print(f"  {pid}: {r['total_showdowns']} showdowns, {r['total_mistakes']} mistakes ({rate:.0f}%)")
        for sit_key in sorted(r["situations"]):
            s = r["situations"][sit_key]
            if s["mistakes"] > 0:
                hands_str = ", ".join(m["hand"] for m in s["mistake_hands"])
                print(f"    {sit_key}: {s['mistakes']}/{s['showdowns']} mistakes → {hands_str}")


if __name__ == "__main__":
    analyze_and_save()
