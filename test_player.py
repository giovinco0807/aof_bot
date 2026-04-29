import sqlite3
import pandas as pd

conn = sqlite3.connect('d:/aof_bot/automation/data/hands.db')

q1 = "SELECT * FROM player_stats WHERE player_id='13323436'"
print("player_stats:")
print(pd.read_sql_query(q1, conn).to_string())

q2 = "SELECT position, num_players, COUNT(*) as cnt FROM hand_players hp JOIN hands h ON hp.hand_id = h.id WHERE hp.player_id='13323436' GROUP BY position, num_players"
print("\nhand_players:")
print(pd.read_sql_query(q2, conn).to_string())
