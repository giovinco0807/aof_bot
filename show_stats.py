import sqlite3
import pandas as pd
from pathlib import Path

DB_PATH = Path("d:/aof_bot/automation/data/hands.db")

def generate_md():
    if not DB_PATH.exists():
        return "Database not found."

    conn = sqlite3.connect(str(DB_PATH))
    query = """
    SELECT position, prior_actions, action, stack_bb 
    FROM hand_players 
    WHERE position != '' AND action IN ('A', 'F')
    """
    df = pd.read_sql_query(query, conn)
    conn.close()
    
    if df.empty:
        return "No valid data found."

    df['action'] = df['action'].astype(str).str.strip().str.upper()
    df['prior_actions'] = df['prior_actions'].astype(str).str.strip().str.upper()

    def categorize_situation(acts):
        if 'A' in acts:
            return "Facing All-in (Call)"
        else:
            return "Unopened (Open Jam)"
            
    df['Situation'] = df['prior_actions'].apply(categorize_situation)
    
    lines = []
    
    # 1. Overall action rate by position
    lines.append("### 全体のアクション率 (ポジション別)")
    lines.append("| Position | Push% | Total Hands |")
    lines.append("|---|---|---|")
    pos_stats = df.groupby('position')['action'].value_counts(normalize=True).unstack().fillna(0)
    for pos in ['SB', 'BB', 'BTN', 'CO']:
        if pos in pos_stats.index and 'A' in pos_stats.columns:
            val = pos_stats.loc[pos, 'A'] * 100
            total = len(df[df['position'] == pos])
            lines.append(f"| {pos} | {val:.1f}% | {total} |")

    # 2. Push rate by Situation
    lines.append("\n### シチュエーション別 Push% (未オープン vs 相手のオールイン後)")
    lines.append("| Position | Situation | Push% | Total Hands |")
    lines.append("|---|---|---|---|")
    sit_stats = df.groupby(['position', 'Situation'])['action'].agg(['count', lambda x: (x == 'A').mean()]).reset_index()
    sit_stats.columns = ['Position', 'Situation', 'Total Hands', 'Push%']
    
    pos_order = {"CO": 1, "BTN": 2, "SB": 3, "BB": 4}
    sit_stats['order'] = sit_stats['Position'].map(pos_order)
    sit_stats = sit_stats.sort_values(['order', 'Situation'])
    
    for _, row in sit_stats.iterrows():
        push_pct = row['Push%'] * 100
        lines.append(f"| {row['Position']} | {row['Situation']} | {push_pct:.1f}% | {row['Total Hands']} |")

    # 3. Push rate by Stack Size
    lines.append("\n### スタックサイズ別 Push% (Unopened のみ)")
    lines.append("| Stack Size | Push% | Total Hands |")
    lines.append("|---|---|---|")
    df_unopened = df[df['Situation'] == 'Unopened (Open Jam)'].copy()
    
    def stack_group(bb):
        if bb < 10: return "< 10 BB"
        elif bb <= 12: return "10-12 BB"
        elif bb <= 15: return "12-15 BB"
        elif bb <= 20: return "15-20 BB"
        else: return "20+ BB"
        
    df_unopened['Stack'] = df_unopened['stack_bb'].apply(stack_group)
    stack_stats = df_unopened.groupby('Stack')['action'].agg(['count', lambda x: (x == 'A').mean()]).reset_index()
    stack_stats.columns = ['Stack Size', 'Total Hands', 'Push%']
    
    stack_order = {"< 10 BB": 1, "10-12 BB": 2, "12-15 BB": 3, "15-20 BB": 4, "20+ BB": 5}
    stack_stats['order'] = stack_stats['Stack Size'].map(stack_order)
    stack_stats = stack_stats.sort_values('order')
    
    for _, row in stack_stats.iterrows():
        push_pct = row['Push%'] * 100
        lines.append(f"| {row['Stack Size']} | {push_pct:.1f}% | {row['Total Hands']} |")

    with open("d:/aof_bot/stats.md", "w", encoding="utf-8") as f:
        f.write("\n".join(lines))

if __name__ == "__main__":
    generate_md()
