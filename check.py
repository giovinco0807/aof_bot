import sqlite3
import pandas as pd

conn = sqlite3.connect('d:/aof_bot/automation/data/hands.db')

# Check if 13323436 exists anywhere in hand_players
res1 = pd.read_sql_query("SELECT COUNT(*) as cnt FROM hand_players WHERE player_id='13323436'", conn)
print('Exact match 13323436 in hand_players:', res1.iloc[0]['cnt'])

res2 = pd.read_sql_query("SELECT player_id, COUNT(*) FROM hand_players WHERE player_id LIKE '%133234%' GROUP BY player_id", conn)
print('\nSimilar player_ids in hand_players:')
print(res2.to_string())

res3 = pd.read_sql_query("SELECT COUNT(*) as cnt FROM hands WHERE player_ids LIKE '%13323436%'", conn)
print('\nExact match 13323436 in hands.player_ids:', res3.iloc[0]['cnt'])

# Check what the highest player_id counts are
res4 = pd.read_sql_query("SELECT player_id, COUNT(*) as cnt FROM hand_players GROUP BY player_id ORDER BY cnt DESC LIMIT 10", conn)
print('\nTop 10 players in hand_players:')
print(res4.to_string())

# Check top 10 players in player_stats
res5 = pd.read_sql_query("SELECT player_id, hands_seen FROM player_stats ORDER BY hands_seen DESC LIMIT 10", conn)
print('\nTop 10 players in player_stats:')
print(res5.to_string())

conn.close()
