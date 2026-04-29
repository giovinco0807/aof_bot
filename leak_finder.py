import sqlite3
import collections

DB_PATH = r"d:\aof_bot\automation\data\hands.db"

def identify_leaks():
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    
    # 1. Get players with >= 2000 hands
    rows = conn.execute("""
        SELECT player_id, COUNT(*) as total_hands, SUM(profit_bb) as profit_bb,
               SUM(CASE WHEN action IN ('A','raise','call') THEN 1 ELSE 0 END) * 100.0 / COUNT(*) AS vpip
        FROM hand_players
        WHERE player_id IS NOT NULL AND player_id != 0
        GROUP BY player_id
        HAVING total_hands >= 2000
        ORDER BY total_hands DESC
    """).fetchall()
    
    candidates = []
    
    for r in rows:
        pid = r['player_id']
        hands = r['total_hands']
        profit = r['profit_bb']
        vpip = r['vpip']
        
        # Calculate SB push% (Heads Up only)
        sb_stats = conn.execute("""
            SELECT 
                COUNT(*) as sb_opps,
                SUM(CASE WHEN hp.action IN ('A','raise') THEN 1 ELSE 0 END) as sb_pushes
            FROM hand_players hp
            JOIN hands h ON hp.hand_id = h.id
            WHERE hp.player_id = ? AND hp.position = 'SB' AND h.num_players = 2
        """, (pid,)).fetchone()
        
        sb_push_pct = (sb_stats['sb_pushes'] / sb_stats['sb_opps'] * 100) if sb_stats['sb_opps'] > 0 else 0
        
        # Calculate BB call% (Heads Up only)
        # If BB has an action ('F' or 'A'/'call'), it means SB pushed.
        bb_stats = conn.execute("""
            SELECT 
                COUNT(CASE WHEN hp.action IN ('F','A','call') THEN 1 END) as bb_opps,
                SUM(CASE WHEN hp.action IN ('A','call') THEN 1 ELSE 0 END) as bb_calls
            FROM hand_players hp
            JOIN hands h ON hp.hand_id = h.id
            WHERE hp.player_id = ? AND hp.position = 'BB' AND h.num_players = 2
        """, (pid,)).fetchone()
        
        bb_call_pct = (bb_stats['bb_calls'] / bb_stats['bb_opps'] * 100) if bb_stats['bb_opps'] > 0 else 0
        
        candidates.append({
            'pid': pid,
            'hands': hands,
            'profit': profit,
            'vpip': vpip,
            'sb_push': sb_push_pct,
            'bb_call': bb_call_pct,
            'sb_opps': sb_stats['sb_opps'],
            'bb_opps': bb_stats['bb_opps']
        })
        
    conn.close()
    
    print(f"{'Player ID':<12} | {'Hands':<6} | {'Profit':<8} | {'VPIP':<5} | {'SB Push%':<8} | {'BB Call%':<8} | {'Classification'}")
    print("-" * 88)
    
    for c in candidates:
        leaks = []
        if c['sb_push'] > 71:
            leaks.append("OVER-PUSH SB")
        elif c['sb_push'] < 60 and c['sb_opps'] > 100:
            leaks.append("UNDER-PUSH SB")
            
        if c['bb_call'] > 60:
            leaks.append("OVER-CALL BB")
        elif c['bb_call'] < 48 and c['bb_opps'] > 100:
            leaks.append("OVER-FOLD BB")
            
        leak_str = ", ".join(leaks) if leaks else "---"
        prof_str = f"{c['profit']:.1f}"
        
        print(f"{c['pid']:<12} | {c['hands']:<6} | {prof_str:<8} | {c['vpip']:<4.1f}% | {c['sb_push']:<7.1f}% | {c['bb_call']:<7.1f}% | {leak_str}")

if __name__ == '__main__':
    identify_leaks()
