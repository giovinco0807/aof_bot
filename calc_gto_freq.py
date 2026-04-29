import sqlite3
import pandas as pd
from pathlib import Path
import sys

sys.path.append("d:/aof_bot")
from automation.gto_lookup import GtoLookup

DB_PATH = Path("d:/aof_bot/automation/data/hands.db")

RANKS = "AKQJT98765432"
HANDS_169 = []
for i, r1 in enumerate(RANKS):
    for j, r2 in enumerate(RANKS):
        if i == j:
            HANDS_169.append((r1 + r2, 6))
        elif i < j:
            HANDS_169.append((r1 + r2 + "s", 4))
            HANDS_169.append((r1 + r2 + "o", 12))

def calc_gto_push_pct(gto: GtoLookup, num_players: int, position: str, prior_actions: str) -> float:
    # Remove hyphens for GTO chart compatibility
    gto_prior = prior_actions.replace("-", "")
    
    chart = gto._find_chart(num_players, position, gto_prior)
    if not chart:
        return -1.0
        
    push_combos = 0.0
    total_combos = 1326.0
    
    entries = {e["hand"]: e.get("allin_freq", 0.0) for e in chart.get("entries", [])}
    
    for hand_name, combos in HANDS_169:
        freq = entries.get(hand_name, 0.0)
        push_combos += (freq * combos)
        
    return (push_combos / total_combos) * 100

def compare_gto_vs_actual():
    gto = GtoLookup("d:/aof_bot/solver/data/charts_rb50")
    conn = sqlite3.connect(str(DB_PATH))
    query = """
    SELECT hp.position, hp.prior_actions, hp.action, h.num_players
    FROM hand_players hp
    JOIN hands h ON hp.hand_id = h.id
    WHERE hp.position != '' AND hp.action IN ('A', 'F') AND h.num_players IN (2,3,4)
    """
    df = pd.read_sql_query(query, conn)
    conn.close()
    
    if df.empty:
        return

    df['action'] = df['action'].astype(str).str.upper()
    df['prior_actions'] = df['prior_actions'].astype(str).str.upper()

    def categorize_situation(row):
        acts = row['prior_actions']
        if 'A' in acts:
            return "Facing All-in (Call)"
        else:
            return "Unopened (Open Push)"
            
    df['Situation'] = df.apply(categorize_situation, axis=1)

    lines = []
    lines.append("### [4-MAX] 未オープン時の Push% (実測 vs GTO)")
    lines.append("| Position | 実際の Push% (n) | GTO Push% | 乖離 (Actual - GTO) |")
    lines.append("|---|---|---|---|")
    
    df_4p = df[df['num_players'] == 4]
    unopened_4p = df_4p[df_4p['Situation'] == 'Unopened (Open Push)']
    
    pos_stats = unopened_4p.groupby('position')['action'].agg(['count', lambda x: (x == 'A').mean()]).reset_index()
    pos_stats.columns = ['Position', 'Total', 'Actual%']
    pos_order = {"CO": 1, "BTN": 2, "SB": 3, "BB": 4}
    pos_stats['order'] = pos_stats['Position'].map(pos_order)
    pos_stats = pos_stats.sort_values('order')
    
    unop_priors = {"CO": "", "BTN": "F", "SB": "F-F", "BB": "F-F-F"}
    
    for _, row in pos_stats.iterrows():
        pos = row['Position']
        actual = row['Actual%'] * 100
        n = row['Total']
        gto_pct = calc_gto_push_pct(gto, 4, pos, unop_priors.get(pos, ""))
        
        diff = actual - gto_pct if gto_pct >= 0 else 0
        diff_str = f"{diff:+.1f}%" if gto_pct >= 0 else "-"
        gto_str = f"{gto_pct:.1f}%" if gto_pct >= 0 else "N/A"
        
        lines.append(f"| {pos} | {actual:.1f}% ({n}) | {gto_str} | {diff_str} |")
        
    lines.append("\n### [4-MAX] 相手のオールインに対する Call% (実測 vs GTO)")
    lines.append("| Situation | 実際の Call% (n) | GTO Call% | 乖離 (Actual - GTO) |")
    lines.append("|---|---|---|---|")
    
    facing_4p = df_4p[df_4p['Situation'] == 'Facing All-in (Call)']
    
    common_situations = [
        ("BTN", "A"),
        ("SB", "A-F"),
        ("SB", "F-A"),
        ("BB", "A-F-F"),
        ("BB", "F-A-F"),
        ("BB", "F-F-A"),
    ]
    
    for pos, priors in common_situations:
        filt = facing_4p[(facing_4p['position'] == pos) & (facing_4p['prior_actions'] == priors)]
        if filt.empty: continue
        
        actual = (filt['action'] == 'A').mean() * 100
        n = len(filt)
        gto_pct = calc_gto_push_pct(gto, 4, pos, priors)
        
        diff = actual - gto_pct if gto_pct >= 0 else 0
        diff_str = f"{diff:+.1f}%" if gto_pct >= 0 else "-"
        gto_str = f"{gto_pct:.1f}%" if gto_pct >= 0 else "N/A"
        
        desc = f"{pos} (vs {priors})"
        lines.append(f"| {desc} | {actual:.1f}% ({n}) | {gto_str} | {diff_str} |")

    lines.append("\n### [3-MAX] 未オープン時の Push% (実測 vs GTO)")
    lines.append("| Position | 実際の Push% (n) | GTO Push% | 乖離 (Actual - GTO) |")
    lines.append("|---|---|---|---|")
    
    df_3p = df[df['num_players'] == 3]
    unopened_3p = df_3p[df_3p['Situation'] == 'Unopened (Open Push)']
    
    if not unopened_3p.empty:
        pos_stats3 = unopened_3p.groupby('position')['action'].agg(['count', lambda x: (x == 'A').mean()]).reset_index()
        pos_stats3.columns = ['Position', 'Total', 'Actual%']
        pos_order3 = {"BTN": 1, "SB": 2, "BB": 3}
        pos_stats3['order'] = pos_stats3['Position'].map(pos_order3)
        pos_stats3 = pos_stats3.sort_values('order')
        
        unop_priors3 = {"BTN": "", "SB": "F", "BB": "F-F"}
        for _, row in pos_stats3.iterrows():
            pos = row['Position']
            actual = row['Actual%'] * 100
            n = row['Total']
            gto_pct = calc_gto_push_pct(gto, 3, pos, unop_priors3.get(pos, ""))
            diff = actual - gto_pct if gto_pct >= 0 else 0
            diff_str = f"{diff:+.1f}%" if gto_pct >= 0 else "-"
            gto_str = f"{gto_pct:.1f}%" if gto_pct >= 0 else "N/A"
            lines.append(f"| {pos} | {actual:.1f}% ({n}) | {gto_str} | {diff_str} |")

    with open("d:/aof_bot/gto_compare.md", "w", encoding="utf-8") as f:
        f.write("\n".join(lines))

if __name__ == "__main__":
    compare_gto_vs_actual()
