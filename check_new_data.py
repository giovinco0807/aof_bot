import sqlite3
import pandas as pd
conn = sqlite3.connect('d:/aof_bot/automation/data/hands.db')

# Count hands recorded since 05:51 AM today
res1 = pd.read_sql_query("SELECT COUNT(*) as new_hands FROM hands WHERE timestamp > '2026-03-20T05:51:00'", conn)
print('New hands recorded today:', res1.iloc[0]['new_hands'])

# Check if player_name is being populated
res2 = pd.read_sql_query("SELECT player_id, player_name, hands_seen, last_seen FROM player_stats WHERE player_name != '' AND player_name IS NOT NULL ORDER BY last_seen DESC LIMIT 10", conn)
print('\nRecently named players (showing top 10):')
print(res2.to_string())

conn.close()
