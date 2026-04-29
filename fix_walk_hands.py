import sqlite3
import pandas as pd
from pathlib import Path

DB_PATH = Path("d:/aof_bot/automation/data/hands.db")

def fix_walk_hands():
    print("Connecting to database...")
    conn = sqlite3.connect(str(DB_PATH))
    
    # Target hands where SOME position is empty
    # Wait, hand_players might just have position=''
    bad_hands = conn.execute("SELECT DISTINCT hand_id FROM hand_players WHERE position = ''").fetchall()
    
    updates = []
    POS_NAMES = {
        2: ["SB", "BB"],
        3: ["BTN", "SB", "BB"],
        4: ["CO", "BTN", "SB", "BB"],
    }
    
    print(f"Found {len(bad_hands)} hands with missing positions. Fixing...")
    
    for (hand_id,) in bad_hands:
        hp_rows = conn.execute("SELECT id, seat_id, action FROM hand_players WHERE hand_id = ?", (hand_id,)).fetchall()
        np = len(hp_rows)
        if np not in POS_NAMES:
            continue
            
        # To determine who is who, we look at their actions.
        # Since it's a walk hand, exactly ONE player has "F" as their action but they didn't really act?
        # Actually in walk hands, the BB won without acting. The actual action recorded for BB might be 'F' or whatever default.
        # If it's a walk hand, CO folded, BTN folded, SB folded.
        # Wait, if all players but one are in action_order, how do we know the action_order now?
        # In the DB, we don't have action_order anymore! We just have `actions` column from the `hands` table, which is ordered by `seat_ids`.
        # How do we figure out who the BB is?
        # In `hands` table, `dealer_seat` was saved as fallback integer.
        # Wait! The earlier bug fell back to `dealer_seat = hs.dealer_idx`. We can use `dealer_seat`!
        # But wait, `dealer_idx` was given by the server. Is it reliable? Let's check `hands` table.
        h = conn.execute("SELECT dealer_seat FROM hands WHERE id = ?", (hand_id,)).fetchone()
        if not h: continue
        dealer_seat = h[0]
        
        # If dealer_seat is valid, BB is (dealer_seat + 2) in 4P? Wait, dealer_seat might natively mean BTN.
        # In PPPoker, is dealer_idx = BTN? Usually yes.
        # Let's rely on the fact that the ONLY person who WON chips is the BB!
        # Because it's a walk hand, the BB collected the blinds.
        # So the player with `profit_chips > 0` is the BB!
        
        bb_seat_candidate = None
        for r in hp_rows:
            # check profit
            p = conn.execute("SELECT profit_chips FROM hand_players WHERE id = ?", (r[0],)).fetchone()[0]
            if p > 0:
                bb_seat_candidate = r[1]
                break
                
        if bb_seat_candidate is None:
            # Fallback: maybe no rake?
            continue
            
        bb_seat = bb_seat_candidate
        
        # If we know BB seat, we can map positions!
        # In AoF, sequence is CO, BTN, SB, BB relative to BB!
        # BB is the anchor.
        # 4P: CO = (bb_seat - 3) % np, BTN = (bb_seat - 2) % np, SB = (bb_seat - 1) % np
        
        for hp_id, seat_id, action in hp_rows:
            # calculate distance from BB
            dist_to_bb = (bb_seat - seat_id + np) % np
            
            # If distance to BB is 0 -> BB
            # If distance is 1 (seat is right before BB) -> SB
            # If distance is 2 -> BTN
            # If distance is 3 -> CO
            
            if dist_to_bb == 0: pos = "BB"
            elif dist_to_bb == 1: pos = "SB"
            elif dist_to_bb == 2: pos = "BTN"
            elif dist_to_bb == 3: pos = "CO"
            else: pos = ""
            
            updates.append((pos, hp_id))
            
    if updates:
        print(f"Applying {len(updates)} position updates...")
        for i in range(0, len(updates), 5000):
            chunk = updates[i:i+5000]
            conn.executemany("UPDATE hand_players SET position = ? WHERE id = ?", chunk)
            conn.commit()
    
    conn.close()
    
    print("Done fixing positions. Now re-running prior_actions backfill...")
    # Call the original update_past_hands to fix prior_actions and stack_bb based on the corrected positions
    import sys
    sys.path.append("d:/aof_bot")
    try:
        from update_past_hands import backfill_hand_players
        backfill_hand_players()
    except Exception as e:
        print(f"Error calling backfill: {e}")

if __name__ == "__main__":
    fix_walk_hands()
