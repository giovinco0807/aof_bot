import sqlite3
import pandas as pd
conn = sqlite3.connect('d:/aof_bot/automation/data/hands.db')
hero_df = pd.read_sql_query("SELECT player_id FROM player_stats ORDER BY hands_seen DESC LIMIT 1", conn)
hero_id = hero_df.iloc[0]['player_id']

df = pd.read_sql_query("SELECT position, prior_actions, action, COUNT(*) as cnt FROM hand_players WHERE player_id=? GROUP BY position, prior_actions, action", conn, params=(hero_id,))
print(df.to_string())
