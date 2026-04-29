import sqlite3
from pathlib import Path

DB_PATH = Path("d:/aof_bot/automation/data/hands.db")

def backfill_hand_players():
    if not DB_PATH.exists():
        print(f"DB not found at {DB_PATH}")
        return

    print("Connecting to database...")
    conn = sqlite3.connect(str(DB_PATH))
    
    # Check if columns exist
    cols = [r[1] for r in conn.execute("PRAGMA table_info(hand_players)").fetchall()]
    if "prior_actions" not in cols or "stack_bb" not in cols:
        print("Columns missing. Making sure they are created...")
        try: conn.execute("ALTER TABLE hand_players ADD COLUMN prior_actions TEXT DEFAULT ''")
        except: pass
        try: conn.execute("ALTER TABLE hand_players ADD COLUMN stack_bb REAL DEFAULT 0")
        except: pass
    
    # Read hands table
    print("Reading hands...")
    hands = conn.execute("SELECT id, num_players, bb_size, player_ids, stacks FROM hands").fetchall()
    
    updates = []
    
    POS_ORDER = {"CO": 1, "BTN": 2, "SB": 3, "BB": 4}
    
    print(f"Processing {len(hands)} hands...")
    for h in hands:
        hand_id, np, bb_size, pids_str, stacks_str = h
        
        pids = [p.strip() for p in pids_str.split(",")] if pids_str else []
        stacks = [float(s) if s else 0.0 for s in stacks_str.split(",")] if stacks_str else []
        
        # Get all players for this hand
        hp_full = conn.execute("SELECT id, player_id, position, action FROM hand_players WHERE hand_id = ?", (hand_id,)).fetchall()
        
        if not hp_full:
            continue
            
        # Sort them by their position to determine action order
        # Fallback to id order if position is empty
        actor_order = sorted(hp_full, key=lambda x: POS_ORDER.get(x[2], 99))
        
        for idx, hp in enumerate(actor_order):
            hp_id, pid, position, action = hp
            
            # Prior actions are the actions of everyone before this player in actor_order
            p_acts = [p[3] for p in actor_order[:idx]]
            prior_actions = "-".join(p_acts)
            
            # Find stack from pids index
            stack_chips = 0.0
            if pid in pids:
                p_idx = pids.index(pid)
                if p_idx < len(stacks):
                    stack_chips = stacks[p_idx]
            
            stack_bb = round(stack_chips / bb_size, 2) if bb_size > 0 else 0.0
            
            updates.append((prior_actions, stack_bb, hp_id))

    print(f"Found {len(updates)} rows to update in hand_players. Executing...")
    
    # Execute mostly in chunks
    chunk_size = 5000
    for i in range(0, len(updates), chunk_size):
        chunk = updates[i:i+chunk_size]
        conn.executemany("UPDATE hand_players SET prior_actions = ?, stack_bb = ? WHERE id = ?", chunk)
        conn.commit()
        print(f"Updated {min(i+chunk_size, len(updates))} / {len(updates)}")
        
    conn.close()
    print("Done!")

if __name__ == "__main__":
    backfill_hand_players()
