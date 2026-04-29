import sqlite3
import pandas as pd
conn = sqlite3.connect('d:/aof_bot/automation/data/hands.db')

# Check date range
res1 = pd.read_sql_query("SELECT MIN(timestamp), MAX(timestamp), COUNT(*) FROM hands", conn)
print('hands table date range:')
print(res1.to_string())

res2 = pd.read_sql_query("SELECT MIN(last_seen), MAX(last_seen), COUNT(*) FROM player_stats", conn)
print('\nplayer_stats table date range:')
print(res2.to_string())

# Add a confidence interval calculation directly into the markdown python script
