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

def analyze_all_leaks():
    gto = GtoLookup("d:/aof_bot/solver/data/charts_rb50")
    conn = sqlite3.connect(str(DB_PATH))
    
    hero_id_df = pd.read_sql_query("SELECT player_id FROM player_stats ORDER BY hands_seen DESC LIMIT 1", conn)
    hero_id = hero_id_df.iloc[0]['player_id'] if not hero_id_df.empty else ""
    
    # Get players with >= 1500 hands
    top_players_df = pd.read_sql_query("""
        SELECT player_id, COUNT(*) as actual_hands 
        FROM hand_players 
        GROUP BY player_id 
        HAVING actual_hands >= 1500
        ORDER BY actual_hands DESC 
    """, conn)
    
    # Get names
    names_df = pd.read_sql_query("SELECT player_id, player_name FROM player_stats", conn)
    name_map = dict(zip(names_df['player_id'], names_df['player_name']))
    
    # Query all 4-max actions
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
    
    # ALL extended situations
    situations = [
        {"desc": "COからの先制Push",       "pos": "CO", "priors": ""},
        {"desc": "BTNからの先制Push",      "pos": "BTN", "priors": "F"},
        {"desc": "SBからの先制Push",       "pos": "SB", "priors": "F-F"},
        {"desc": "BTNのCall (vs CO)",     "pos": "BTN", "priors": "A"},
        {"desc": "SBのCall (vs CO)",      "pos": "SB", "priors": "A-F"},
        {"desc": "SBのCall (vs BTN)",     "pos": "SB", "priors": "F-A"},
        {"desc": "BBのCall (vs CO)",      "pos": "BB", "priors": "A-F-F"},
        {"desc": "BBのCall (vs BTN)",     "pos": "BB", "priors": "F-A-F"},
        {"desc": "BBのCall (vs SB)",      "pos": "BB", "priors": "F-F-A"},
    ]
    
    gto_pcts = {sit["desc"]: calc_gto_push_pct(gto, 4, sit["pos"], sit["priors"]) for sit in situations}

    lines = []
    lines.append("## 常連プレイヤー（1,500ハンド以上）のエクスプロイト・リーク分析")
    lines.append("")
    lines.append("以下は、データベース上で **サンプルサイズが大きく、かつ統計的信頼区間（95%）を完全に逸脱している（明らかにGTOからズレている）** シチュエーションだけを抽出した「弱点一覧（リーク箇所）」です。")
    lines.append("")
    lines.append("> [!TIP]")
    lines.append("> * <span style='color: red'>**🟥 Over-Bluff / Over-Call**</span>：GTOよりプレイしすぎ。このアクションには通常より**広くパニッシュ**（コールやPush）できます。")
    lines.append("> * <span style='color: green'>**🟩 Under-Bluff / Under-Call**</span>：GTOよりプレイしなさすぎ。このアクションには**降りすぎ推奨**、または相手のブラインドを**広くスチール**できます。")
    lines.append("")

    for rank, row in top_players_df.iterrows():
        pid = str(row['player_id'])
        total_hands = row['actual_hands']
        if pid == "0": continue
        
        is_hero = (pid == str(hero_id))
        pname = name_map.get(pid, "")
        
        if is_hero:
            display_name = f"Hero (Bot) `{pid}`"
        else:
            if pname and pname != "Unknown":
                display_name = f"{pname} `{pid}`"
            else:
                display_name = f"*(名前未取得)* `{pid}`"
                
        lines.append(f"### {rank+1}. {display_name} - {total_hands:,} Hands")
        
        leaks = []
        for sit in situations:
            filt = df[(df['player_id'] == pid) & (df['position'] == sit['pos']) & (df['prior_actions'] == sit['priors'])]
            n = len(filt)
            successes = sum(filt['action'] == 'A')
            gto_p = gto_pcts[sit["desc"]] / 100.0
            
            if n < 20:
                continue # Skip small sample sizes for deep analysis
                
            p, lower, upper = get_confidence_interval(successes, n)
            margin = (upper - lower) / 2
            
            actual_pct = p * 100
            lower_pct = lower * 100
            upper_pct = upper * 100
            gto_pct = gto_p * 100
            
            if gto_pct < lower_pct:
                diff = actual_pct - gto_pct
                leaks.append(f"* <span style='color: red'>**🟥 ルースすぎ (+{diff:.1f}%)**</span> : **{sit['desc']}** | 実測 **{actual_pct:.1f}%** (±{margin*100:.1f}%) ＞ GTO {gto_pct:.1f}% *(n={n})*")
            elif gto_pct > upper_pct:
                diff = actual_pct - gto_pct
                leaks.append(f"* <span style='color: green'>**🟩 タイトすぎ ({diff:.1f}%)**</span> : **{sit['desc']}** | 実測 **{actual_pct:.1f}%** (±{margin*100:.1f}%) ＜ GTO {gto_pct:.1f}% *(n={n})*")
                
        if not leaks:
            lines.append("* 📊 *統計的に有意な大きなリーク（GTOからの逸脱）は見つかりませんでした。非常にバランスの取れたプレイをしています。*")
        else:
            lines.extend(leaks)
        lines.append("")
        
    with open("d:/aof_bot/leak_analysis.md", "w", encoding="utf-8") as f:
        f.write("\n".join(lines))

if __name__ == "__main__":
    analyze_all_leaks()
