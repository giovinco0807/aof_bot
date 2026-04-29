import sqlite3
import random
import sys
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

def run_hu_simulation(gto, evaluator, hands_dealt, exploit_mode):
    players = ["HERO", "13082001"]
    chips = {pid: 0.0 for pid in players}
    
    kj_sb_range = build_percentile_range(0.843)
    kj_bb_range = build_percentile_range(0.457)
    hero_bb_46_range = build_percentile_range(0.460)
    
    for i in range(hands_dealt):
        table_players = players[i % 2:] + players[:i % 2]
        sb_player = table_players[0]
        bb_player = table_players[1]
        
        deck = Deck()
        hole_cards = {pid: deck.draw(2) for pid in table_players}
        
        contributions = {sb_player: 0.5, bb_player: 1.0}
        pushers = []
        
        # SB Action
        sb_hand = get_hand_str(*hole_cards[sb_player])
        sb_push = False
        if sb_player == "13082001":
            sb_push = sb_hand in kj_sb_range
        else: # HERO
            freq = gto.get_push_freq(sb_hand, 2, "SB", "")
            if freq < 0: freq = 0.0
            sb_push = random.random() < freq

        if sb_push:
            contributions[sb_player] = 8.0
            pushers.append(sb_player)
        else:
            chips[sb_player] -= 0.5
            chips[bb_player] += 0.5
            continue
            
        # BB Action
        bb_hand = get_hand_str(*hole_cards[bb_player])
        bb_call = False
        
        if bb_player == "13082001":
             bb_call = bb_hand in kj_bb_range
        else: # HERO
            freq = gto.get_push_freq(bb_hand, 2, "BB", "A")
            if freq < 0: freq = 0.0
            
            if exploit_mode == "GTO":
                bb_call = random.random() < freq
            elif exploit_mode == "EXPLOIT_PURE_CALL":
                # GTOが0%より高い（1%でもコールする可能性がある）ハンドなら、100%コールする
                bb_call = freq > 0.0
            elif exploit_mode == "EXPLOIT_CALL_46":
                bb_call = bb_hand in hero_bb_46_range

        if bb_call:
            contributions[bb_player] = 8.0
            pushers.append(bb_player)
        else:
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
    hands_dealt = 10000000  # 1000万ハンド
    
    print("Running 10M Hands: Pure GTO Baseline...")
    chips_gto = run_hu_simulation(gto, evaluator, hands_dealt, "GTO")
    
    print("Running 10M Hands: Exploit (GTO Mixed -> 100% Pure Call)...")
    chips_exploit = run_hu_simulation(gto, evaluator, hands_dealt, "EXPLOIT_PURE_CALL")

    print("Running 10M Hands: Exploit (Top 46% Call)...")
    chips_exploit_46 = run_hu_simulation(gto, evaluator, hands_dealt, "EXPLOIT_CALL_46")
    
    with open("d:/aof_bot/hu_kj_46pc_10M_sim.md", "w", encoding="utf-8") as f:
        f.write("# 対キングジャック：BB「コール頻度を46%に拡張した」戦術シミュレーション（1000万ハンド版）\n\n")
        f.write(f"キングジャック（SB Push: 84.3%）に対して、BBにいるHeroが「GTO（平均42.5%）」、「混合100%（約45%）」、そして「上位46%ハンドでの強制コール」を行った場合の利益を1000万ハンドで比較しました。\n\n")
        f.write("※ 各パターン1000万ハンド実施。レーキ負担加味、Rakeback 70%加味。\n\n")
        
        for mode, name, chips in [
            ("GTO", "【ベースライン】Heroが純粋なGTO（頻度乱数あり: 平均42.5%）でプレイした場合", chips_gto),
            ("EXPLOIT", "【検証1】Heroが『GTO混合を100%に強化（約45%）』した場合", chips_exploit),
            ("EXPLOIT_46", "【検証2】Heroが『コール頻度をさらにトップ46%まで広げた』場合", chips_exploit_46)
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
                color = "<font color='red'>" if rb_winrate > 0 else "<font color='blue'>"
                color_e = "</font>"
                f.write(f"| {pname} | {total_bb:.1f} | {bold}{color}{rb_winrate:+.2f}{color_e}{bold} |\n")
            f.write("\n")

if __name__ == "__main__":
    main()
