import sqlite3

def query_bots():
    conn = sqlite3.connect("d:/aof_bot/automation/data/hands.db")
    c = conn.cursor()
    
    bot_ids = ('13323436', '13337673', '13407577')
    placeholders = ','.join('?' * len(bot_ids))
    
    # 全プレイスタイルのVPIPも見てみる
    c.execute(f"""
    SELECT 
        COUNT(*) as total_hands,
        SUM(CASE WHEN action='A' THEN 1 ELSE 0 END) as vpip
    FROM hand_players hp
    WHERE hp.player_id IN ({placeholders})
    """, bot_ids)
    total_hands, vpip = c.fetchone()
    vpip_freq = (vpip / total_hands * 100) if total_hands else 0
    
    # 4-Max + 2-Max SB Push
    c.execute(f"""
    SELECT 
        COUNT(*) as total_hands,
        SUM(CASE WHEN action='A' THEN 1 ELSE 0 END) as pushes
    FROM hand_players hp JOIN hands h ON hp.hand_id = h.id
    WHERE hp.player_id IN ({placeholders}) AND hp.position = 'SB' AND hp.prior_actions = ''
    """, bot_ids)
    
    sb_total, sb_push = c.fetchone()
    sb_freq = (sb_push / sb_total * 100) if sb_total else 0
    
    # 4-Max + 2-Max BB Call (facing SB push)
    c.execute(f"""
    SELECT 
        COUNT(*) as total_hands,
        SUM(CASE WHEN action='A' THEN 1 ELSE 0 END) as calls
    FROM hand_players hp JOIN hands h ON hp.hand_id = h.id
    WHERE hp.player_id IN ({placeholders}) AND hp.position = 'BB' AND hp.prior_actions = 'A'
    """, bot_ids)
    
    bb_total, bb_call = c.fetchone()
    bb_freq = (bb_call / bb_total * 100) if bb_total else 0
    
    print(f"--- Tight Bots Aggregate Ops (13323436, 13337673, 13407577) ---")
    print(f"Total Hands Played: {total_hands}")
    print(f"Overall VPIP: {vpip_freq:.1f}%\n")
    print(f"Total SB Push Opportunities: {sb_total}")
    print(f"SB Push Rate: {sb_freq:.1f}%\n")
    print(f"Total BB Call Opportunities: {bb_total}")
    print(f"BB Call Rate: {bb_freq:.1f}%\n")
    
    # Check individual hand counts to ensure they are actually active
    c.execute(f"""
    SELECT hp.player_id, COUNT(*)
    FROM hand_players hp
    WHERE hp.player_id IN ({placeholders})
    GROUP BY hp.player_id
    """, bot_ids)
    print("Individual Datapoints:")
    for row in c.fetchall():
        print(f"  ID {row[0]}: {row[1]} hands")
    
    conn.close()

if __name__ == "__main__":
    query_bots()
