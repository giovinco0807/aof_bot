import sqlite3
import pandas as pd
from pathlib import Path

DB_PATH = Path("d:/aof_bot/automation/data/hands.db")

conn = sqlite3.connect(str(DB_PATH))

hero_id_df = pd.read_sql_query("SELECT player_id FROM player_stats ORDER BY hands_seen DESC LIMIT 1", conn)
hero_id = str(hero_id_df.iloc[0]['player_id']) if not hero_id_df.empty else ""

# We will use the raw hand_players table to get the true count of recorded hands per player.
# A left join with player_stats to fetch the player_name
query = """
    SELECT 
        hp.player_id, 
        COUNT(*) as actual_hands,
        ps.player_name
    FROM hand_players hp
    LEFT JOIN player_stats ps ON hp.player_id = ps.player_id
    WHERE hp.player_id != '0'
    GROUP BY hp.player_id
    ORDER BY actual_hands DESC 
    LIMIT 100
"""

df = pd.read_sql_query(query, conn)
conn.close()

lines = ["### 各ユーザーのハンド数（取得に成功した総ハンド数順・トップ100）"]
lines.append("")
lines.append(f"現在、**合計 {len(df)} プレイヤー** のデータが記録されています。")
lines.append("")
lines.append("| 順位 | ポーカーネーム | Player ID | 総ハンド数 (DB実録) |")
lines.append("|---|---|---|---|")

for idx, row in df.iterrows():
    pid = str(row['player_id'])
    hands = row['actual_hands']
    pname = row['player_name']
    
    if pid == hero_id:
        display_name = "**Hero (Bot)**"
    elif pname and pname != "Unknown":
        display_name = f"**{pname}**"
    else:
        display_name = "*(名前未取得)*"
        
    rank = idx + 1
    lines.append(f"| {rank} | {display_name} | `{pid}` | {hands:,} |")

with open("d:/aof_bot/all_players.md", "w", encoding="utf-8") as f:
    f.write("\n".join(lines))

print(f"Generated data for {len(df)} players.")
