import sqlite3
import pandas as pd
from pathlib import Path
import sys
import math

sys.path.append("d:/aof_bot")
from automation.gto_lookup import GtoLookup

DB_PATH = Path("d:/aof_bot/automation/data/hands.db")

RANKS = "AKQJT98765432"
HANDS_169 = []
for i, r1 in enumerate(RANKS):
    for j, r2 in enumerate(RANKS):
        if i == j: HANDS_169.append((r1 + r2, 6))
        elif i < j:
            HANDS_169.append((r1 + r2 + "s", 4))
            HANDS_169.append((r1 + r2 + "o", 12))

def calc_gto_push_pct(gto, num_players, position, prior_actions):
    gto_prior = prior_actions.replace("-", "")
    chart = gto._find_chart(num_players, position, gto_prior)
    if not chart: return -1.0
    
    push_combos = 0.0
    entries = {e["hand"]: e.get("allin_freq", 0.0) for e in chart.get("entries", [])}
    for hand_name, combos in HANDS_169:
        freq = entries.get(hand_name, 0.0)
        push_combos += (freq * combos)
        
    return (push_combos / 1326.0) * 100

def get_confidence_interval(successes, n, z=1.96):
    if n == 0:
        return 0, 0, 0
    p = successes / n
    denominator = 1 + z**2 / n
    center = (p + z**2 / (2 * n)) / denominator
    spread = z * math.sqrt(p * (1 - p) / n + z**2 / (4 * n**2)) / denominator
    lower = max(0, center - spread)
    upper = min(1, center + spread)
    return p, lower, upper

def generate_top5_stats_ci():
    gto = GtoLookup("d:/aof_bot/solver/data/charts_rb50")
    conn = sqlite3.connect(str(DB_PATH))
    
    hero_id_df = pd.read_sql_query("SELECT player_id FROM player_stats ORDER BY hands_seen DESC LIMIT 1", conn)
    hero_id = hero_id_df.iloc[0]['player_id'] if not hero_id_df.empty else ""
    
    top_players_df = pd.read_sql_query("""
        SELECT player_id, COUNT(*) as actual_hands 
        FROM hand_players 
        GROUP BY player_id 
        ORDER BY actual_hands DESC 
        LIMIT 6
    """, conn)
    
    # Get names
    names_df = pd.read_sql_query("SELECT player_id, player_name FROM player_stats", conn)
    name_map = dict(zip(names_df['player_id'], names_df['player_name']))
    
    query = """
    SELECT hp.player_id, hp.position, hp.prior_actions, hp.action, h.num_players
    FROM hand_players hp
    JOIN hands h ON hp.hand_id = h.id
    WHERE hp.position != '' AND hp.action IN ('A', 'F') AND h.num_players = 4
    """
    df = pd.read_sql_query(query, conn)
    conn.close()
    
    df['action'] = df['action'].astype(str).str.upper()
    df['prior_actions'] = df['prior_actions'].astype(str).str.upper()
    
    situations = [
        {"name": "CO Open", "pos": "CO", "priors": ""},
        {"name": "BTN Open", "pos": "BTN", "priors": "F"},
        {"name": "SB Open", "pos": "SB", "priors": "F-F"},
        {"name": "BB Call vs SB", "pos": "BB", "priors": "F-F-A"},
    ]
    
    gto_pcts = {sit["name"]: calc_gto_push_pct(gto, 4, sit["pos"], sit["priors"]) for sit in situations}

    lines = []
    lines.append("### トッププレイヤーの GTO 乖離分析 (4-MAX) ※95%信頼区間つき")
    lines.append("")
    lines.append("> [!TIP]")
    lines.append("> 各数値の下にある `±X.X%` は**統計学的な誤差範囲（95%信頼区間）**です。")
    lines.append("> 相手の傾向が完全にGTOから外れている（エクスプロイト可能）な箇所は、**<span style='color: red'>赤（ルースすぎ）</span>と<span style='color: green'>緑（タイトすぎ）</span>で色付け**されています。")
    lines.append("")
    
    headers = ["Player", "DB Raw Hands"] + [f"{s['name']}<br>(GTO {gto_pcts[s['name']]:.1f}%)" for s in situations]
    lines.append("| " + " | ".join(headers) + " |")
    lines.append("|" + "|".join(["---"] * len(headers)) + "|")
    
    for _, row in top_players_df.iterrows():
        pid = str(row['player_id'])
        total_hands = row['actual_hands']
        if pid == "0": continue
        
        is_hero = (pid == str(hero_id))
        pname = name_map.get(pid, "")
        
        if is_hero:
            display_name = f"Hero (Bot)<br>`{pid}`"
        else:
            if pname and pname != "Unknown":
                display_name = f"**{pname}**<br>`{pid}`"
            else:
                display_name = f"*(名前未取得)*<br>`{pid}`"
                
        cols = [display_name, f"{total_hands:,}"]
        
        for sit in situations:
            filt = df[(df['player_id'] == pid) & (df['position'] == sit['pos']) & (df['prior_actions'] == sit['priors'])]
            n = len(filt)
            successes = sum(filt['action'] == 'A')
            gto_p = gto_pcts[sit["name"]] / 100.0
            
            if n < 10:
                cell = f"N/A<br>*(n={n})*"
            else:
                p, lower, upper = get_confidence_interval(successes, n)
                margin = (upper - lower) / 2
                
                actual_pct = p * 100
                margin_pct = margin * 100
                lower_pct = lower * 100
                upper_pct = upper * 100
                gto_pct = gto_p * 100
                
                if gto_pct < lower_pct:
                    color = "red"
                    sig = f" **(Over-Bluff)**"
                elif gto_pct > upper_pct:
                    color = "green"
                    sig = f" **(Under-Bluff)**"
                else:
                    color = "gray"
                    sig = ""
                
                diff = actual_pct - gto_pct
                diff_str = f"<span style='color: {color}'>{diff:+.1f}%{sig}</span>"
                cell = f"**{actual_pct:.1f}%** (±{margin_pct:.1f}%)<br>{diff_str}<br>*(n={n})*"
                
            cols.append(cell)
            
        lines.append("| " + " | ".join(cols) + " |")
        
    with open("d:/aof_bot/top5_gto_ci.md", "w", encoding="utf-8") as f:
        f.write("\n".join(lines))

if __name__ == "__main__":
    generate_top5_stats_ci()
