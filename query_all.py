import sqlite3
import pandas as pd
conn = sqlite3.connect('d:/aof_bot/automation/data/hands.db')

res1 = conn.execute("SELECT COUNT(*) FROM hand_players WHERE position=''").fetchone()[0]
res2 = conn.execute("SELECT COUNT(*) FROM hand_players WHERE prior_actions=''").fetchone()[0]
res3 = pd.read_sql_query("SELECT position, COUNT(*) as cnt FROM hand_players GROUP BY position", conn)

print(f"Empty Positions (' '): {res1}")
print(f"Empty Prior Actions (Open Push ' '): {res2}")
print(res3.to_string())
