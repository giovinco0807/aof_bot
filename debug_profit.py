import sqlite3

conn = sqlite3.connect("d:/aof_bot/automation/data/hands.db")

# Check: how many players per hand?
print("=== Players per hand distribution ===")
rows = conn.execute("""
    SELECT num_players_in_hand, COUNT(*) 
    FROM (
        SELECT hand_id, COUNT(*) as num_players_in_hand
        FROM hand_players GROUP BY hand_id
    )
    GROUP BY num_players_in_hand ORDER BY num_players_in_hand
""").fetchall()
for r in rows:
    print(f"  {r[0]} players: {r[1]} hands")

# Check: what num_players does the hands table say vs actual hand_players count
print("\n=== Expected vs Actual player count per hand (mismatches) ===")
rows = conn.execute("""
    SELECT h.id, h.num_players, COUNT(hp.id) as actual_count, h.actions
    FROM hands h
    JOIN hand_players hp ON h.id = hp.hand_id
    GROUP BY h.id
    HAVING h.num_players != COUNT(hp.id)
    LIMIT 15
""").fetchall()
print(f"  Total mismatches: ...")
for r in rows:
    print(f"  hand={r[0]} expected={r[1]} actual={r[2]} actions={r[3]}")

mismatch_count = conn.execute("""
    SELECT COUNT(*) FROM (
        SELECT h.id
        FROM hands h
        JOIN hand_players hp ON h.id = hp.hand_id
        GROUP BY h.id
        HAVING h.num_players != COUNT(hp.id)
    )
""").fetchone()[0]
print(f"  Total hands with player count mismatch: {mismatch_count} / {conn.execute('SELECT COUNT(*) FROM hands').fetchone()[0]}")

# Check: hands_seen in player_stats vs actual count in hand_players
print("\n=== player_stats.hands_seen vs actual hand_players count ===")
rows = conn.execute("""
    SELECT ps.player_id, ps.hands_seen, COALESCE(hp_count.actual, 0) as actual,
           ps.total_profit_bb, COALESCE(hp_sum.actual_bb, 0) as actual_profit_bb
    FROM player_stats ps
    LEFT JOIN (
        SELECT player_id, COUNT(*) as actual FROM hand_players GROUP BY player_id
    ) hp_count ON ps.player_id = hp_count.player_id
    LEFT JOIN (
        SELECT player_id, SUM(profit_bb) as actual_bb FROM hand_players GROUP BY player_id
    ) hp_sum ON ps.player_id = hp_sum.player_id
    ORDER BY ps.hands_seen DESC
""").fetchall()
for r in rows:
    flag = " *** MISMATCH" if r[1] != r[2] else ""
    profit_flag = f" *** PROFIT DIFF={r[3]-r[4]:.2f}" if abs(r[3] - r[4]) > 0.1 else ""
    print(f"  {r[0]}: stats_hands={r[1]} actual_hands={r[2]}{flag} | stats_bb={r[3]:.2f} actual_bb={r[4]:.2f}{profit_flag}")

conn.close()
