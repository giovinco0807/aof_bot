import sqlite3
import pandas as pd
conn = sqlite3.connect('d:/aof_bot/automation/data/hands.db')

bad_rows = pd.read_sql_query("SELECT hand_id, seat_id, position, action, profit_chips FROM hand_players WHERE position='' LIMIT 10", conn)
print("Preview of empty position rows:")
print(bad_rows.to_string())

bad_hands = pd.read_sql_query("SELECT id, num_players, dealer_seat, pot_chips, rake_chips FROM hands WHERE id IN (SELECT DISTINCT hand_id FROM hand_players WHERE position='') LIMIT 5", conn)
print("\nPreview of hands table for these rows:")
print(bad_hands.to_string())
