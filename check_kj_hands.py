import sqlite3
import pandas as pd
from pathlib import Path

DB_PATH = Path("d:/aof_bot/automation/data/hands.db")

def get_169_rank(cards_str):
    if not cards_str or len(cards_str) != 4:
        return ""
    r1, s1 = cards_str[0], cards_str[1]
    r2, s2 = cards_str[2], cards_str[3]
    
    ranks = "AKQJT98765432"
    idx1 = ranks.find(r1.upper())
    idx2 = ranks.find(r2.upper())
    
    if idx1 == -1 or idx2 == -1: return ""
    
    if idx1 > idx2:
        r1, r2 = r2, r1
    
    if r1 == r2:
        return r1 + r2
    elif s1 == s2:
        return r1 + r2 + "s"
    else:
        return r1 + r2 + "o"

def main():
    conn = sqlite3.connect(str(DB_PATH))
    # Query all hands where player ID is 13082001 (King Jack), position is SB, Pushed, and cards are known
    query = """
    SELECT hp.cards
    FROM hand_players hp
    JOIN hands h ON hp.hand_id = h.id
    WHERE hp.player_id = '13082001' 
      AND hp.position = 'SB' 
      AND hp.prior_actions = 'F-F'
      AND hp.action = 'A'
      AND hp.cards != ''
      AND h.num_players = 4
    """
    df = pd.read_sql_query(query, conn)
    conn.close()
    
    df['hand_169'] = df['cards'].apply(get_169_rank)
    df = df[df['hand_169'] != ""]
    
    counts = df['hand_169'].value_counts()
    
    lines = []
    lines.append("### キングジャック(`13082001`)の SB Push 実測ハンド（ショーダウン露出分）")
    lines.append("")
    lines.append(f"これまでに確認された **{len(df)}回** のSBからのPushハンドの内訳です。")
    lines.append("")
    lines.append("| ハンド | 実際にPushした回数 |")
    lines.append("|---|---|")
    
    for hand, count in counts.items():
        lines.append(f"| **{hand}** | {count} 回 |")
        
    with open("d:/aof_bot/kj_hands.md", "w", encoding="utf-8") as f:
        f.write("\n".join(lines))
        
if __name__ == "__main__":
    main()
