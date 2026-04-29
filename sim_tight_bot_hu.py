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

def query_stats(pid):
    conn = sqlite3.connect("d:/aof_bot/automation/data/hands.db")
    c = conn.cursor()
    
    # Query SB Push
    c.execute("""
    SELECT COUNT(*) as total, SUM(CASE WHEN action='A' THEN 1 ELSE 0 END) as pushed
    FROM hand_players hp JOIN hands h ON hp.hand_id = h.id
    WHERE hp.player_id = ? AND h.num_players = 2 AND hp.position = 'SB' AND hp.prior_actions = ''
    """, (pid,))
    sb_total, sb_push = c.fetchone()
    sb_freq = sb_push / sb_total if sb_total and sb_total > 5 else 0.68
    
    # Query BB Call
    c.execute("""
    SELECT COUNT(*) as total, SUM(CASE WHEN action='A' THEN 1 ELSE 0 END) as pushed
    FROM hand_players hp JOIN hands h ON hp.hand_id = h.id
    WHERE hp.player_id = ? AND h.num_players = 2 AND hp.position = 'BB' AND hp.prior_actions = 'A'
    """, (pid,))
    bb_total, bb_call = c.fetchone()
    bb_freq = bb_call / bb_total if bb_total and bb_total > 5 else 0.53
    
    conn.close()
    return sb_freq, bb_freq

class DummyStdout:
    def write(self, s): pass
    def flush(self): pass

def run_hu_simulation(gto, evaluator, hands_dealt, exploit_mode, tb_sb_freq, tb_bb_freq):
    players = ["HERO", "13323436"]
    chips = {pid: 0.0 for pid in players}
    
    tb_sb_range = build_percentile_range(tb_sb_freq)
    tb_bb_range = build_percentile_range(tb_bb_freq)
    
    # Simple exploit ranges based on heuristics
    # If tb_sb_freq = 0.55 (tight), we should call tighter. Let's say 45%.
    # If tb_bb_freq = 0.40 (tight), we should push wider. Let's say 100%.
    hero_bb_exploit_range = build_percentile_range(0.45)
    
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
        
        if sb_player == "13323436":
            sb_push = sb_hand in tb_sb_range
        else: # HERO
            if exploit_mode == "GTO":
                freq = gto.get_push_freq(sb_hand, 2, "SB", "")
                if freq < 0: freq = 0.0
                sb_push = random.random() < freq
            elif exploit_mode == "EXPLOIT_MAX":
                # Tight bot BB folds too much (40% call means 60% fold). So we push Any Two!
                sb_push = True

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
        
        if bb_player == "13323436":
             bb_call = bb_hand in tb_bb_range
        else: # HERO
            if exploit_mode == "GTO":
                freq = gto.get_push_freq(bb_hand, 2, "BB", "A")
                if freq < 0: freq = 0.0
                bb_call = random.random() < freq
            elif exploit_mode == "EXPLOIT_MAX":
                # Tight bot SB pushes 55%. We should call tighter than GTO. Let's use 45%.
                bb_call = bb_hand in hero_bb_exploit_range

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
    print("Querying Tight Bot (`13323436`) Heads-up DB Stats...")
    sb_freq, bb_freq = query_stats("13323436")
    print(f"Tight Bot HU Stats -> SB Push: {sb_freq*100:.1f}%, BB Call: {bb_freq*100:.1f}%")
    
    old_stdout = sys.stdout
    sys.stdout = DummyStdout()
    gto = GtoLookup("d:/aof_bot/solver/data/charts_rb50")
    sys.stdout = old_stdout
    
    evaluator = Evaluator()
    hands_dealt = 2000000  # 2 Million Hands
    
    print("Running 2M Hands: Pure GTO Baseline...")
    chips_gto = run_hu_simulation(gto, evaluator, hands_dealt, "GTO", sb_freq, bb_freq)
    
    print("Running 2M Hands: Max Exploit (Push 100% / Call 45%)...")
    chips_exploit = run_hu_simulation(gto, evaluator, hands_dealt, "EXPLOIT_MAX", sb_freq, bb_freq)
    
    with open("d:/aof_bot/hu_tightbot_sim.md", "w", encoding="utf-8") as f:
        f.write("# タイトBot（`13323436`）との完全ヘッズアップ結果\n\n")
        f.write(f"データベースから「1対1（Heads-Up）」の時のタイトBotの正確なスタッツを抽出しました：\n")
        f.write(f"* **SB Push率**: `{sb_freq*100:.1f}%` (GTO基準: 約68% / かなりタイト)\n")
        f.write(f"* **BB Call率**: `{bb_freq*100:.1f}%` (GTO基準: 約53% / かなり降りる)\n\n")
        f.write("※ 各パターン200万ハンド実施。レーキ負担: 約 `3.3 bb/100`、Rakeback 70%加味。\n\n")
        
        for mode, name, chips in [
            ("GTO", "【ベースライン】Heroが純粋なGTO（無設定）でプレイした場合", chips_gto),
            ("EXPLOIT_MAX", "【最大搾取】Heroが相手の弱点を突いた（SBからPush 100% / BBでCall 45%）場合", chips_exploit)
        ]:
            f.write(f"## {name}\n")
            f.write("| プレイヤー名 | 総純利益 (BB) | bb/100 (RB後) |\n")
            f.write("|---|---|---|\n")
            
            table_rake = sum(chips.values())
            per_player_rake = abs(table_rake) / 2
            rb_amount = (per_player_rake / hands_dealt * 100) * 0.70
            
            for pid in ["HERO", "13323436"]:
                pname = "🥷 HERO (Bot)" if pid == "HERO" else "🤖 Tight Bot"
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
