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
    chart_info = gto._find_chart(num_players, position, gto_prior)
    if not chart_info: return -1.0
    
    push_combos = 0.0
    entries = {e["hand"]: e.get("allin_freq", 0.0) for e in chart_info.get("entries", [])}
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

def analyze_bvb_leaks():
    gto = GtoLookup("d:/aof_bot/solver/data/charts_rb50")
    conn = sqlite3.connect(str(DB_PATH))
    
    hero_id_df = pd.read_sql_query("SELECT player_id FROM player_stats ORDER BY hands_seen DESC LIMIT 1", conn)
    hero_id = hero_id_df.iloc[0]['player_id'] if not hero_id_df.empty else ""
    
    top_players_df = pd.read_sql_query("""
        SELECT player_id, COUNT(*) as actual_hands 
        FROM hand_players 
        GROUP BY player_id 
        HAVING actual_hands >= 1500
        ORDER BY actual_hands DESC 
    """, conn)
    
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
        {"desc": "SBの先制Push (対BBのエクスプロイト)", "pos": "SB", "priors": "F-F"},
        {"desc": "BBのCall (vs SBのエクスプロイト)",  "pos": "BB", "priors": "F-F-A"},
    ]
    
    gto_pcts = {sit["desc"]: calc_gto_push_pct(gto, 4, sit["pos"], sit["priors"]) for sit in situations}

    lines = []
    lines.append("## SB vs BB (ブラインド対決) 限定のエクスプロイト解析")
    lines.append("")
    lines.append("後ろにまだアクションが残っている他プレイヤーが存在しない「純粋な1対1（ヘッズアップ状態）」のシチュエーションのみを抽出し、誰からの搾取が安全かつ最大利益を生むかを分析しました。")
    lines.append("")
    lines.append("> [!TIP]")
    lines.append("> * **SBの先制Push**: 相手（SB）が過剰にPushしてくる（🟥）なら、こちらはBBで広くコールしてキャッチ可能。タイトすぎる（🟩）なら本来降りる手でもコールせず降りて搾取を防ぐ。")
    lines.append("> * **BBのCall**: 相手（BB）がルースにコールしすぎる（🟥）なら、こちらはSBからの「弱い手でのブラフPush」をやめる。タイトに降りすぎる（🟩）なら、こちらはSBから100%（エニハン）でPushしてブラインドを盗みまくる。")
    lines.append("")

    for rank, row in top_players_df.iterrows():
        pid = str(row['player_id'])
        total_hands = row['actual_hands']
        if pid == "0": continue
        
        is_hero = (pid == str(hero_id))
        if is_hero: continue # Skip hero for this opponent targeting list
        
        pname = name_map.get(pid, "")
        if pname and pname != "Unknown":
            display_name = f"**{pname}** (`{pid}`)"
        else:
            display_name = f"*(名前未取得)* (`{pid}`)"
                
        lines.append(f"### {rank}. {display_name} - {total_hands:,} Hands")
        
        leaks = []
        for sit in situations:
            filt = df[(df['player_id'] == pid) & (df['position'] == sit['pos']) & (df['prior_actions'] == sit['priors'])]
            n = len(filt)
            successes = sum(filt['action'] == 'A')
            gto_p = gto_pcts[sit["desc"]] / 100.0
            
            if n < 30:
                leaks.append(f"* {sit['desc']} : データ不足 *(n={n})*")
                continue
                
            p, lower, upper = get_confidence_interval(successes, n)
            margin = (upper - lower) / 2
            
            actual_pct = p * 100
            lower_pct = lower * 100
            upper_pct = upper * 100
            gto_pct = gto_p * 100
            
            status_text = ""
            if gto_pct < lower_pct:
                diff = actual_pct - gto_pct
                status = "🟥 Over-Bluff / Over-Call"
                status_text = f"<span style='color: red'>**{status} (+{diff:.1f}%)**</span>"
            elif gto_pct > upper_pct:
                diff = actual_pct - gto_pct
                status = "🟩 Under-Bluff / Under-Call"
                status_text = f"<span style='color: green'>**{status} ({diff:.1f}%)**</span>"
            else:
                diff = actual_pct - gto_pct
                status_text = f"<span style='color: gray'>GTO付近 ({diff:+.1f}%)</span>"
                
            leaks.append(f"* **{sit['desc']}** : {status_text} | 実測 **{actual_pct:.1f}%** (±{margin*100:.1f}%) vs GTO {gto_pct:.1f}% *(n={n})*")
                
        lines.extend(leaks)
        lines.append("")
        
    with open("d:/aof_bot/bvb_leaks.md", "w", encoding="utf-8") as f:
        f.write("\n".join(lines))

if __name__ == "__main__":
    analyze_bvb_leaks()
