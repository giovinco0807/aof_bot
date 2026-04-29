import sqlite3
import json
import sys

sys.path.insert(0, "d:/aof_bot/automation")
from gto_lookup import cards_to_hand_name, GtoLookup

db_path = "d:/aof_bot/automation/data/hands.db"
conn = sqlite3.connect(db_path)
conn.row_factory = sqlite3.Row

# Load Exploit range to see what it *should* do
# kj_mixed_pure_call = set("AA,KK,QQ,JJ,TT,AKs,AQs,AJs,AKo,...".split(","))
kj_mixed_pure_call = set("AA,KK,QQ,JJ,TT,AKs,AQs,AJs,AKo,99,ATs,AQo,KQs,88,AJo,KJs,KTs,ATo,QJs,77,KQo,QTs,A9s,KJo,JTs,66,A8s,KTo,A9o,QTo,A7s,J9s,Q9s,K9s,55,A5s,A8o,A6s,JTo,A4s,K9o,44,A7o,A3s,T9s,Q9o,Q8s,K8s,A5o,J9o,A2s,A6o,K7s,A4o,33,J8s,K8o,98s,T8s,K6s,Q8o,A3o,K5s,A2o,K7o,Q7s,K4s,J8o,22,T9o,T7s,98o,K3s,K6o,Q6s,K2s,K5o,J7s,87s,Q7o,Q5s,K4o,97s,T8o,T6s,Q4s,K3o,J7o,Q6o,87o,K2o,Q3s,97o,Q2s,T7o,J6s,Q5o,86s,76s,J5s,96s,T6o,Q4o,J6o,Q3o,86o,J4s,76o,T5s,96o,J5o,Q2o,75s,85s,J3s,T5o,T4s,95s,J2s,J4o,75o,65s,85o,T3s,95o,J3o,T4o,84s,T2s,65o,74s,94s,J2o,T3o,54s,84o,94o,T2o,74o,64s,93s,83s,54o,93o,73s,64o,83o,92s,53s,92o,63s,73o,82s,43s,53o,82o,72s,63o,43o,62s,52s,72o,62o,42s,52o,32s,42o,32o".split(","))

gto = GtoLookup("d:/aof_bot/solver/data/charts_rb50")

query = """
SELECT h.id, h.timestamp, h.num_players, h.actions, h.cards, h.player_ids, hp.action as hero_action, hp.cards as hero_cards, hp.position, hp.prior_actions
FROM hands h
JOIN hand_players hp ON h.id = hp.hand_id
WHERE h.id IN (
    SELECT id FROM hands ORDER BY id DESC LIMIT 500
)
AND h.player_ids LIKE '%13082001%'
ORDER BY h.id DESC
"""

rows = conn.execute(query).fetchall()

print(f"Total hands with KJ in last 500 hands: {len(rows)}")

KJ_UID = "13082001"
found = 0

for row in rows:
    pids = row['player_ids'].split(',')
    if KJ_UID not in pids: continue
    
    if not row['hero_cards'] or len(row['hero_cards']) < 4: continue
    hero_hand_name = cards_to_hand_name(row['hero_cards'][0:2], row['hero_cards'][2:4])
    
    hero_pos = row['position']
    prior = row['prior_actions']
    hero_action = row['hero_action']
    np = row['num_players']
    
    # BvB conditions facing a push
    is_facing_push = (hero_pos == "BB" and prior in ("A", "FA", "FFA"))
    is_sb_push = (hero_pos == "SB" and prior in ("", "F", "FF"))
    
    if is_sb_push:
        gto_freq = gto.get_push_freq(hero_hand_name, np, hero_pos, prior)
        
        if hero_action == "F" and gto_freq > 0.0:
            print(f"--- ERROR FOUND? (HERO FOLDED SB PUSH HAND) ---")
            print(f"Hand {row['id']} [{row['timestamp']}] | {np}-max")
            print(f"Hero POS: {hero_pos}, PRIOR: {prior}, HAND: {hero_hand_name} ({row['hero_cards']})")
            print(f"Hero folded. GTO Freq: {gto_freq*100:.1f}%")
            print(f"PIDs: {row['player_ids']}")
            print(f"Actions: {row['actions']}")
            found += 1
            if found >= 10: break

conn.close()
if found == 0:
    print("No erroneous folds found in SB Push!")
