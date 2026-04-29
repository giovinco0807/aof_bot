import sqlite3
import pandas as pd
conn = sqlite3.connect('d:/aof_bot/automation/data/hands.db')
res = conn.execute("SELECT COUNT(*) FROM hand_players WHERE player_id='0'").fetchone()[0]
print('Zero UIDs in hand_players:', res)

res2 = pd.read_sql_query("SELECT COUNT(*) as cnt FROM hands WHERE player_ids LIKE '%\"0\"%' OR player_ids LIKE '%''0''%' OR player_ids LIKE '%[^1-9]0[^0-9]%'", conn)
print('Zero UIDs in hands table:', res2.iloc[0]['cnt'])
