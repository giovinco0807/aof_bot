import sqlite3
import random
import sys
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
    players = ["13323436"]
    names = {"13323436": "Tight Bot"}
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
            
    profiles["GTO_A"] = {'name': '🤖 GTO_Bot A'}
    profiles["GTO_B"] = {'name': '🤖 GTO_Bot B'}
    profiles["HERO"]  = {'name': '🥷 HERO (Exploit Bot)'}
    return profiles

def does_push_call(hand_169, key, profiles, pid, pos, prior, gto_lookup, table_players, enable_exploit=True):
    if pid.startswith("GTO_") or pid == "HERO":
        pos_gto = "CO" if pos == "UTG" else pos
        prior_gto = prior.replace("-", "")
        
        if pid == "HERO" and enable_exploit:
            # TIGHT BOT EXPLOIT LOGIC
            # If Hero is SB and prior is empty/F/FF, and Tight Bot is BB (pos 3 relative to Hero pos 2)
            # wait, if Hero is SB (pos 2), Tight Bot is BB (pos 3) only if table_players[3] == "13323436"
            if pos == "SB" and prior_gto in ("", "F", "FF") and table_players[3] == "13323436":
                # Push 80%
                t_combos = 1326 * 0.80; c_combos = 0
                for h in RAW_HAND_RANKS:
                    if c_combos >= t_combos: return False
                    if h == hand_169: return True
                    c_combos += get_combos(h)
                return False
                
            # If Hero is BB, and Tight Bot pushed from SB
            if pos == "BB" and prior_gto == "FFA" and table_players[2] == "13323436":
                # Call 45%
                t_combos = 1326 * 0.45; c_combos = 0
                for h in RAW_HAND_RANKS:
                    if c_combos >= t_combos: return False
                    if h == hand_169: return True
                    c_combos += get_combos(h)
                return False

        freq = gto_lookup.get_push_freq(hand_169, 4, pos_gto, prior_gto)
        if freq < 0: freq = 0.0 
        return random.random() < freq
        
    if key not in profiles[pid]['ranges']:
        t_combos = 132.6; c_combos = 0
        for h in RAW_HAND_RANKS:
            if c_combos >= t_combos: return False
            if h == hand_169: return True
            c_combos += get_combos(h)
        return False
    return hand_169 in profiles[pid]['ranges'][key]

class DummyStdout:
    def write(self, s): pass
    def flush(self): pass

def run_pattern(gto, evaluator, profiles, players_array, hands_dealt, enable_exploit):
    chips = {pid: 0.0 for pid in players_array}
    
    for i in range(hands_dealt):
        table_players = players_array[i % 4:] + players_array[:i % 4]
        deck = Deck()
        hole_cards = {pid: deck.draw(2) for pid in table_players}
        pot = 1.5 
        contributions = {table_players[2]: 0.5, table_players[3]: 1.0, table_players[0]: 0.0, table_players[1]: 0.0}
        prior = ""; pushers = []; folders = []
        
        # UTG
        p0 = table_players[0]
        if does_push_call(get_hand_str(*hole_cards[p0]), get_situation_key("UTG", prior), profiles, p0, "UTG", prior, gto, table_players, enable_exploit):
            contributions[p0] = 8.0; pushers.append(p0); prior += "A"
        else: folders.append(p0); prior += "F"
            
        # BTN
        p1 = table_players[1]
        p_btn = "A" if "A" in prior else prior
        p_btn_gto = "F" if prior == "F" else ("A" if prior == "A" else prior)
        if does_push_call(get_hand_str(*hole_cards[p1]), get_situation_key("BTN", p_btn), profiles, p1, "BTN", p_btn_gto, gto, table_players, enable_exploit):
            contributions[p1] = 8.0; pushers.append(p1); prior += "-A"
        else: folders.append(p1); prior += "-F"
            
        # SB
        p2 = table_players[2]
        p_sb_raw = prior.replace("-", "")
        if "A" not in p_sb_raw: p_sb = "F-F"
        elif p_sb_raw == "AF": p_sb = "A-F"
        elif p_sb_raw == "FA": p_sb = "F-A"
        elif p_sb_raw == "AA": p_sb = "A-A"
        else: p_sb = "F-F"
        if does_push_call(get_hand_str(*hole_cards[p2]), get_situation_key("SB", p_sb), profiles, p2, "SB", prior, gto, table_players, enable_exploit):
            contributions[p2] = 8.0; pushers.append(p2); prior += "-A"
        else: folders.append(p2); prior += "-F"
            
        # BB
        p3 = table_players[3]
        p_bb_raw = prior.replace("-", "")
        if "A" in p_bb_raw:
            if p_bb_raw == "AFF": p_bb = "A-F-F"
            elif p_bb_raw == "FAF": p_bb = "F-A-F"
            elif p_bb_raw == "FFA": p_bb = "F-F-A"
            else: p_bb = "A-A-A"
            if does_push_call(get_hand_str(*hole_cards[p3]), get_situation_key("BB", p_bb), profiles, p3, "BB", prior, gto, table_players, enable_exploit):
                contributions[p3] = 8.0; pushers.append(p3)
            else: folders.append(p3)
        
        # Resolution
        if len(pushers) == 0:
            chips[table_players[3]] += 0.5; chips[table_players[2]] -= 0.5
            continue
        if len(pushers) == 1:
            w = pushers[0]
            for p in table_players:
                if p != w: chips[p] -= contributions[p]; chips[w] += contributions[p]
            continue
            
        pot = sum(contributions.values())
        rake = min(pot * 0.02, 3.0)
        final_pot = pot - rake
        board = deck.draw(5)
        scores = {p: evaluator.evaluate(board, hole_cards[p]) for p in pushers}
        min_score = min(scores.values())
        winners = [p for p in pushers if scores[p] == min_score]
        share = final_pot / len(winners)
        
        for p in table_players: chips[p] -= contributions[p]
        for w in winners: chips[w] += share
        
    return chips

def run_simulation():
    old_stdout = sys.stdout
    sys.stdout = DummyStdout()
    gto = GtoLookup("d:/aof_bot/solver/data/charts_rb50")
    sys.stdout = old_stdout
    
    evaluator = Evaluator()
    profiles = load_player_profiles()
    
    hands_dealt = 100000 
    
    print("Running 100k hands: Tight Bot vs HERO (GTO Baseline) vs 2x GTO Bots...")
    array = ["13323436", "HERO", "GTO_A", "GTO_B"]
    chips_gto = run_pattern(gto, evaluator, profiles, array, hands_dealt, enable_exploit=False)
    
    print("Running 100k hands: Tight Bot vs HERO (Stealth Exploit 80% Push / 45% Call) vs 2x GTO Bots...")
    chips_exploit = run_pattern(gto, evaluator, profiles, array, hands_dealt, enable_exploit=True)
    
    # Output to markdown
    with open("d:/aof_bot/tight_bot_exploit_sim.md", "w", encoding="utf-8") as f:
        f.write("# 新ターゲット（Tight Bot / `13323436`）に対するステルス・エクスプロイト効果\n\n")
        f.write("相手のタイト傾向（SB Push 55.6% / BB Call 40.0%）に対して、**Push 80% / Call 45%** という調整レンジをぶつけた場合の100万ハンド期待値比較です。\n\n")
        f.write("※ レーキ負担: それぞれ約 `3.3 bb/100`。Rakeback 70%加味。\n\n")
        
        for p_num, array_name, chips_dict in [(1, "【ベースライン】Heroが普通のGTOのみで戦った場合", chips_gto), 
                                              (2, "【エクスプロイト】Heroがステルスレンジ（Push80/Call45）を起動した場合", chips_exploit)]:
            f.write(f"## {array_name}\n")
            f.write("| プレイヤー名 | 総純利益 (BB) | bb/100 (RB後) | 備考 |\n")
            f.write("|---|---|---|---|\n")
            
            table_rake = sum(chips_dict.values())
            rb_amount = ((abs(table_rake) / 4) / hands_dealt * 100) * 0.70
            
            for pid in array:
                name = profiles[pid]['name']
                total_bb = chips_dict[pid]
                bb_100 = (total_bb / hands_dealt) * 100
                rb_winrate = bb_100 + rb_amount
                bold = "**" if "HERO" in pid else ""
                color = "<font color='red'>" if rb_winrate > 0.5 and "HERO" in pid else ""
                color_e = "</font>" if rb_winrate > 0.5 and "HERO" in pid else ""
                f.write(f"| {name} | {total_bb:.1f} | {bold}{color}{rb_winrate:+.2f}{color_e}{bold} | |\n")
            f.write("\n")

if __name__ == "__main__":
    run_simulation()
