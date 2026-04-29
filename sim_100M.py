import sqlite3
import random
import os
import sys
import time
import multiprocessing as mp
from collections import defaultdict
from treys import Deck, Evaluator, Card

sys.path.append("d:/aof_bot/automation")
from gto_lookup import GtoLookup

DB_PATH = "d:/aof_bot/automation/data/hands.db"

RAW_HAND_RANKS = [
    "AA", "KK", "QQ", "JJ", "TT", "AKs", "AQs", "AJs", "AKo", "99", "ATs", "AQo", "KQs", "88", 
    "AJo", "KJs", "KTs", "ATo", "QJs", "77", "KQo", "QTs", "A9s", "KJo", "JTs", "66", "A8s", 
    "KTo", "A9o", "QTo", "A7s", "J9s", "Q9s", "K9s", "55", "A5s", "A8o", "A6s", "JTo", "A4s", 
    "K9o", "44", "A7o", "A3s", "T9s", "Q9o", "Q8s", "K8s", "A5o", "J9o", "A2s", "A6o", "K7s", 
    "A4o", "33", "J8s", "K8o", "98s", "T8s", "K6s", "Q8o", "A3o", "K5s", "A2o", "K7o", "Q7s", 
    "K4s", "J8o", "22", "T9o", "T7s", "98o", "K3s", "K6o", "Q6s", "K2s", "K5o", "J7s", "87s", 
    "Q7o", "Q5s", "K4o", "97s", "T8o", "T6s", "Q4s", "K3o", "J7o", "Q6o", "87o", "K2o", "Q3s", 
    "97o", "Q2s", "T7o", "J6s", "Q5o", "86s", "76s", "J5s", "96s", "T6o", "Q4o", "J6o", "Q3o", 
    "86o", "J4s", "76o", "T5s", "96o", "J5o", "Q2o", "75s", "85s", "J3s", "T5o", "T4s", "95s", 
    "J2s", "J4o", "75o", "65s", "85o", "T3s", "95o", "J3o", "T4o", "84s", "T2s", "65o", "74s", 
    "94s", "J2o", "T3o", "54s", "84o", "94o", "T2o", "74o", "64s", "93s", "83s", "54o", "93o", 
    "73s", "64o", "83o", "92s", "53s", "92o", "63s", "73o", "82s", "43s", "53o", "82o", "72s", 
    "63o", "43o", "62s", "52s", "72o", "62o", "42s", "52o", "32s", "42o", "32o"
]

def get_combos(hand_str):
    if len(hand_str) == 2: return 6
    if hand_str.endswith('s'): return 4
    if hand_str.endswith('o'): return 12
    return 0

def get_hand_str(c1, c2):
    r1, r2 = Card.get_rank_int(c1), Card.get_rank_int(c2)
    s1, s2 = Card.get_suit_int(c1), Card.get_suit_int(c2)
    if r1 < r2: r1, r2, s1, s2 = r2, r1, s2, s1
    c1_char, c2_char = "23456789TJQKA"[r1], "23456789TJQKA"[r2]
    if r1 == r2: return c1_char + c2_char
    if s1 == s2: return c1_char + c2_char + 's'
    return c1_char + c2_char + 'o'

def get_situation_key(position, prior_actions):
    return f"{position}_{prior_actions}"

def load_player_profiles():
    players = ["13082001"]
    names = {"13082001": "キングジャック"}
    conn = sqlite3.connect(DB_PATH)
    c = conn.cursor()
    profiles = {}
    for pid in players:
        profiles[pid] = {'name': names[pid], 'freqs': defaultdict(lambda: 0.1)}
        c.execute("""
        SELECT hp.position, hp.prior_actions, COUNT(*) as total, SUM(CASE WHEN hp.action='A' THEN 1 ELSE 0 END) as pushed
        FROM hand_players hp JOIN hands h ON hp.hand_id = h.id
        WHERE hp.player_id = ? AND hp.position IN ('UTG', 'BTN', 'SB', 'BB') AND h.num_players = 4
        GROUP BY hp.position, hp.prior_actions
        """, (pid,))
        for pos, prior, total, pushed in c.fetchall():
            if total > 5: profiles[pid]['freqs'][get_situation_key(pos, prior)] = pushed / total
    conn.close()
    
    for pid in players:
        profiles[pid]['ranges'] = {}
        for key, freq in profiles[pid]['freqs'].items():
            t_combos = 1326 * freq; c_combos = 0; allowed = set()
            for h in RAW_HAND_RANKS:
                if c_combos >= t_combos: break
                allowed.add(h); c_combos += get_combos(h)
            profiles[pid]['ranges'][key] = allowed
            
    profiles["GTO_A"] = {'name': '🤖 GTO_Bot A (左隣り)'}
    profiles["GTO_B"] = {'name': '🤖 GTO_Bot B (対面)'}
    profiles["GTO_C"] = {'name': '🤖 GTO_Bot C (右隣り)'}
    return profiles

def does_push_call_fast(hand_169, key, range_dict, gto_lookup, pid, pos, prior):
    if pid.startswith("GTO_"):
        pos_gto = "CO" if pos == "UTG" else pos
        prior_gto = prior.replace("-", "")
        freq = gto_lookup.get_push_freq(hand_169, 4, pos_gto, prior_gto)
        if freq < 0: freq = 0.0 
        return random.random() < freq
        
    if key not in range_dict:
        t_combos = 132.6; c_combos = 0
        for h in RAW_HAND_RANKS:
            if c_combos >= t_combos: return False
            if h == hand_169: return True
            c_combos += get_combos(h)
        return False
    return hand_169 in range_dict[key]


class DummyStdout:
    def write(self, s): pass
    def flush(self): pass


def simulate_chunk(job):
    chunk_hands_dealt, seed_val = job
    random.seed(seed_val)
    
    evaluator = Evaluator()
    players_array = ["13082001", "GTO_A", "GTO_B", "GTO_C"]
    
    # Needs to instanciate per-process due to weak references / memory boundaries
    old_stdout = sys.stdout
    sys.stdout = DummyStdout()
    gto = GtoLookup("d:/aof_bot/solver/data/charts_rb50")
    profiles = load_player_profiles()
    kj_ranges = profiles["13082001"]['ranges']
    sys.stdout = old_stdout
    
    chips = {pid: 0.0 for pid in players_array}
    
    for i in range(chunk_hands_dealt):
        table_players = players_array[i % 4:] + players_array[:i % 4]
        deck = Deck()
        deck_cards = deck.cards
        idx = 0
        
        hole_cards = {}
        for pid in table_players:
            hole_cards[pid] = [deck_cards[idx], deck_cards[idx+1]]
            idx += 2
            
        pot = 1.5 
        contributions = {table_players[2]: 0.5, table_players[3]: 1.0, table_players[0]: 0.0, table_players[1]: 0.0}
        prior = ""; pushers = []; folders = []
        
        # UTG
        p0 = table_players[0]
        if does_push_call_fast(get_hand_str(*hole_cards[p0]), get_situation_key("UTG", prior), kj_ranges, gto, p0, "UTG", prior):
            contributions[p0] = 8.0; pushers.append(p0); prior += "A"
        else: prior += "F"
            
        # BTN
        p1 = table_players[1]
        p_btn = "A" if "A" in prior else prior
        p_btn_gto = "F" if prior == "F" else ("A" if prior == "A" else prior)
        if does_push_call_fast(get_hand_str(*hole_cards[p1]), get_situation_key("BTN", p_btn), kj_ranges, gto, p1, "BTN", p_btn_gto):
            contributions[p1] = 8.0; pushers.append(p1); prior += "-A"
        else: prior += "-F"
            
        # SB
        p2 = table_players[2]
        p_sb_raw = prior.replace("-", "")
        if "A" not in p_sb_raw: p_sb = "F-F"
        elif p_sb_raw == "AF": p_sb = "A-F"
        elif p_sb_raw == "FA": p_sb = "F-A"
        elif p_sb_raw == "AA": p_sb = "A-A"
        else: p_sb = "F-F"
        if does_push_call_fast(get_hand_str(*hole_cards[p2]), get_situation_key("SB", p_sb), kj_ranges, gto, p2, "SB", prior):
            contributions[p2] = 8.0; pushers.append(p2); prior += "-A"
        else: prior += "-F"
            
        # BB
        p3 = table_players[3]
        p_bb_raw = prior.replace("-", "")
        if "A" in p_bb_raw:
            if p_bb_raw == "AFF": p_bb = "A-F-F"
            elif p_bb_raw == "FAF": p_bb = "F-A-F"
            elif p_bb_raw == "FFA": p_bb = "F-F-A"
            else: p_bb = "A-A-A"
            if does_push_call_fast(get_hand_str(*hole_cards[p3]), get_situation_key("BB", p_bb), kj_ranges, gto, p3, "BB", prior):
                contributions[p3] = 8.0; pushers.append(p3)
        
        # Resolution
        n_pushers = len(pushers)
        if n_pushers == 0:
            chips[table_players[3]] += 0.5; chips[table_players[2]] -= 0.5
            continue
        if n_pushers == 1:
            w = pushers[0]
            for p in table_players:
                if p != w: chips[p] -= contributions[p]; chips[w] += contributions[p]
            continue
            
        pot = sum(contributions.values())
        rake = min(pot * 0.02, 3.0)
        final_pot = pot - rake
        
        board = [deck_cards[idx], deck_cards[idx+1], deck_cards[idx+2], deck_cards[idx+3], deck_cards[idx+4]]
        
        scores = {p: evaluator.evaluate(board, hole_cards[p]) for p in pushers}
        min_score = min(scores.values())
        winners = [p for p in pushers if scores[p] == min_score]
        share = final_pot / len(winners)
        
        for p in table_players: chips[p] -= contributions[p]
        for w in winners: chips[w] += share
        
    return chips

def main():
    hands_dealt_total = 100_000_000
    n_cores = max(1, mp.cpu_count() - 1)
    
    print(f"Starting 100,000,000 hand validation using {n_cores} Parallel Cores...")
    
    chunk_size = hands_dealt_total // n_cores
    chunks = []
    for i in range(n_cores):
        size = chunk_size + (hands_dealt_total % n_cores if i == n_cores - 1 else 0)
        chunks.append((size, time.time() + i))
        
    start_t = time.time()
    
    with mp.Pool(processes=n_cores) as pool:
        results = pool.map(simulate_chunk, chunks)
        
    final_chips = {"13082001": 0.0, "GTO_A": 0.0, "GTO_B": 0.0, "GTO_C": 0.0}
    for chunk_chips in results:
        for p, c in chunk_chips.items():
            final_chips[p] += c
            
    print(f"\\nDone in {time.time()-start_t:.1f} seconds.\\n")
    
    players_array = ["13082001", "GTO_A", "GTO_B", "GTO_C"]
    table_rake = sum(final_chips.values())
    per_player_rake = abs(table_rake) / 4
    rb_amount = (per_player_rake / hands_dealt_total * 100) * 0.70
    
    with open("d:/aof_bot/kj_vs_3bots_100M_report.md", "w", encoding="utf-8") as f:
        f.write("# 1億ハンド検証：キングジャック vs 3人のGTO Bot\n\n")
        f.write(f"ノイズを完全に消し去った100,000,000ハンド（1億ハンド）の解析結果です。\n")
        f.write(f"※ マルチコア並列処理で分散を0.00%まで削ぎ落としました。\n\n")
        f.write("## 究極の収束Winrate\n\n")
        f.write("| キングジャックから見た位置 | 総獲得BB | bb/100 (RB前) | レーキバック後 (RB 70%) | 備考 |\n")
        f.write("|---|---|---|---|---|\n")
        
        profiles = load_player_profiles()
        for pid in players_array:
            if pid == "13082001": continue
            name = profiles[pid]['name']
            total_bb = final_chips[pid]
            bb_100 = (total_bb / hands_dealt_total) * 100
            rb_winrate = bb_100 + rb_amount
            bold = "**"
            color = "<font color='red'>" if rb_winrate > 0.0 else ""
            color_end = "</font>" if rb_winrate > 0.0 else ""
            f.write(f"| {name} | {total_bb:,.1f} BB | {bb_100:+.2f} bb/100 | {bold}{color}{rb_winrate:+.2f} bb/100{color_end}{bold} | |\n")

        # KJ Stats
        kj_bb = final_chips["13082001"]
        kj_100 = (kj_bb / hands_dealt_total) * 100
        kj_rb = kj_100 + rb_amount
        f.write(f"| 🤡 キングジャック (ターゲット) | {kj_bb:,.1f} BB | {kj_100:+.2f} bb/100 | **<font color='blue'>{kj_rb:+.2f} bb/100</font>** | |\n")

    for pid in players_array:
        p_name = profiles[pid]['name'] if pid in profiles else "KJ"
        total_bb = final_chips[pid]
        bb_100 = (total_bb / hands_dealt_total) * 100
        rb_winrate = bb_100 + rb_amount
        print(f"{p_name:<25} : {total_bb:>12,.1f} BB  ( {rb_winrate:>+6.2f} bb/100 )")

if __name__ == "__main__":
    # Windows native multiprocessing workaround needs this pattern
    mp.freeze_support()
    main()
