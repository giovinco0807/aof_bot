import sqlite3
import random
import sys
from collections import defaultdict
from treys import Deck, Evaluator, Card

sys.path.append("d:/aof_bot/automation")
from gto_lookup import GtoLookup

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

def build_percentile_range(percent_threshold: float) -> set:
    allowed = set()
    target_combos = 1326 * percent_threshold
    current_combos = 0
    for h in RAW_HAND_RANKS:
        if current_combos >= target_combos:
            break
        allowed.add(h)
        current_combos += get_combos(h)
    return allowed

class DummyStdout:
    def write(self, s): pass
    def flush(self): pass

def does_hu_push_call(hand_169, pid, pos, prior, gto, exploit_mode):
    # King Jack HU Exact Stats (from leak_finder)
    # SB Push = 84.3%, BB Call = 45.7%
    if pid == "13082001":
        if pos == "SB":
            return hand_169 in build_percentile_range(0.843)
        if pos == "BB":
            return hand_169 in build_percentile_range(0.457)
            
    if pid == "HERO":
        if exploit_mode == "GTO":
            # Pure GTO
            freq = gto.get_push_freq(hand_169, 2, pos, prior)
            if freq < 0: freq = 0.0
            return random.random() < freq
            
        elif exploit_mode == "EXPLOIT_SAFE":
            # Push 100% from SB
            if pos == "SB": return True
            # Call 52.3% from BB
            if pos == "BB": return hand_169 in build_percentile_range(0.523)
            
        elif exploit_mode == "EXPLOIT_MAX":
            # Push 100% from SB
            if pos == "SB": return True
            # Call 65% from BB vs 84.3% Push
            if pos == "BB": return hand_169 in build_percentile_range(0.65)
            
    return False

def run_hu_simulation(gto, evaluator, hands_dealt, exploit_mode, pattern_name):
    players = ["HERO", "13082001"]
    chips = {pid: 0.0 for pid in players}
    
    for i in range(hands_dealt):
        # Rotate SB and BB. SB acts first in HU preflop.
        table_players = players[i % 2:] + players[:i % 2]
        # table_players[0] is SB (BTN)
        # table_players[1] is BB
        sb_player = table_players[0]
        bb_player = table_players[1]
        
        deck = Deck()
        hole_cards = {pid: deck.draw(2) for pid in table_players}
        
        # Blinds
        contributions = {sb_player: 0.5, bb_player: 1.0}
        pushers = []
        
        # SB Action
        sb_hand = get_hand_str(*hole_cards[sb_player])
        if does_hu_push_call(sb_hand, sb_player, "SB", "", gto, exploit_mode):
            contributions[sb_player] = 8.0
            pushers.append(sb_player)
            prior = "A"
        else:
            # SB Folds
            chips[sb_player] -= 0.5
            chips[bb_player] += 0.5
            continue
            
        # BB Action
        bb_hand = get_hand_str(*hole_cards[bb_player])
        if does_hu_push_call(bb_hand, bb_player, "BB", "A", gto, exploit_mode):
            contributions[bb_player] = 8.0
            pushers.append(bb_player)
        else:
            # BB Folds
            chips[bb_player] -= 1.0
            chips[sb_player] += 1.0
            continue
            
        # Showdown
        pot = sum(contributions.values())
        rake = min(pot * 0.02, 3.0)
        final_pot = pot - rake
        
        board = deck.draw(5)
        scores = {p: evaluator.evaluate(board, hole_cards[p]) for p in pushers}
        min_score = min(scores.values())
        winners = [p for p in pushers if scores[p] == min_score]
        share = final_pot / len(winners)
        
        for p in pushers: chips[p] -= contributions[p]
        for w in winners: chips[w] += share
        
    return chips

def main():
    old_stdout = sys.stdout
    sys.stdout = DummyStdout()
    gto = GtoLookup("d:/aof_bot/solver/data/charts_rb50")
    sys.stdout = old_stdout
    
    evaluator = Evaluator()
    hands_dealt = 1000000  # 1 Million hands per scenario
    
    print("Running HUD Sim: GTO Baseline...")
    chips_gto = run_hu_simulation(gto, evaluator, hands_dealt, "GTO", "GTO Baseline")
    
    print("Running HUD Sim: Safe Exploit (Push 100% / Call 52.3%)...")
    chips_safe = run_hu_simulation(gto, evaluator, hands_dealt, "EXPLOIT_SAFE", "Safe Exploit")
    
    print("Running HUD Sim: Max Exploit (Push 100% / Call 65%)...")
    chips_max = run_hu_simulation(gto, evaluator, hands_dealt, "EXPLOIT_MAX", "Max Exploit")
    
    with open("d:/aof_bot/hu_kj_sim.md", "w", encoding="utf-8") as f:
        f.write("# 完全ヘッズアップ（1対1）での対キングジャック戦シミュレーション\n\n")
        f.write("キングジャックの超詳細なHeads-Up用スタッツ（SB Push 84.3%、BB Call 45.7%）をデータベースから抽出し、**完全な1対1（他ボットのノイズなし）**で対戦した場合の利益をシミュレートしました。\n\n")
        f.write("※ 各パターン100万ハンド実施。レーキ負担: 約 `3.3 bb/100`、Rakeback 70%加味。\n\n")
        
        for mode, name, chips in [
            ("GTO", "【ベースライン】Heroが純粋なGTOをプレイした場合", chips_gto),
            ("EXPLOIT_SAFE", "【旧エクスプロイト】Heroが安全め（52.3%）にコール＋AnyTwo(100%)でPushした場合", chips_safe),
            ("EXPLOIT_MAX", "【最大搾取】Heroが限界（65%）まで広くコール＋AnyTwo(100%)でPushした場合", chips_max)
        ]:
            f.write(f"## {name}\n")
            f.write("| プレイヤー名 | 総純利益 (BB) | bb/100 (RB後) |\n")
            f.write("|---|---|---|\n")
            
            table_rake = sum(chips.values())
            per_player_rake = abs(table_rake) / 2
            rb_amount = (per_player_rake / hands_dealt * 100) * 0.70
            
            for pid in ["HERO", "13082001"]:
                pname = "🥷 HERO (Bot)" if pid == "HERO" else "🤡 King Jack"
                total_bb = chips[pid]
                bb_100 = (total_bb / hands_dealt) * 100
                rb_winrate = bb_100 + rb_amount
                bold = "**" if pid == "HERO" else ""
                color = "<font color='red'>" if rb_winrate > 0 else ""
                color_e = "</font>" if rb_winrate > 0 else ""
                f.write(f"| {pname} | {total_bb:.1f} | {bold}{color}{rb_winrate:+.2f}{color_e}{bold} |\n")
            f.write("\n")

if __name__ == "__main__":
    main()
