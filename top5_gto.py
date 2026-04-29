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
        if i == j: HANDS_169.append((r1 + r2, 6))
        elif i < j:
            HANDS_169.append((r1 + r2 + "s", 4))
            HANDS_169.append((r1 + r2 + "o", 12))

def calc_gto_push_pct(gto: GtoLookup, num_players: int, position: str, prior_actions: str) -> float:
    gto_prior = prior_actions.replace("-", "")
    chart = gto._find_chart(num_players, position, gto_prior)
    if not chart: return -1.0
    
    push_combos = 0.0
    entries = {e["hand"]: e.get("allin_freq", 0.0) for e in chart.get("entries", [])}
    for hand_name, combos in HANDS_169:
        freq = entries.get(hand_name, 0.0)
        push_combos += (freq * combos)
        
    return (push_combos / 1326.0) * 100

def get_player_name(conn, player_id):
    # Try to find a name from hand_players ? No, name is not stored in hand_players.
    # We only have player_id. We can just use player_id.
    return str(player_id)

def generate_top5_stats():
    gto = GtoLookup("d:/aof_bot/solver/data/charts_rb50")
    conn = sqlite3.connect(str(DB_PATH))
    
    # Identify hero
    hero_id_df = pd.read_sql_query("SELECT player_id FROM player_stats ORDER BY hands_seen DESC LIMIT 1", conn)
    hero_id = hero_id_df.iloc[0]['player_id'] if not hero_id_df.empty else ""
    
    # Get top 5 players (excluding Hero, or including Hero? Let's get Top 6, so we have Hero + Top 5 Opponents)
    top_players_df = pd.read_sql_query("SELECT player_id, hands_seen FROM player_stats ORDER BY hands_seen DESC LIMIT 6", conn)
    
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
    
    # Key Situations to compare:
    situations = [
        {"name": "CO Open", "pos": "CO", "priors": ""},
        {"name": "BTN Open", "pos": "BTN", "priors": "F"},
        {"name": "SB Open", "pos": "SB", "priors": "F-F"},
        {"name": "BB Call vs SB", "pos": "BB", "priors": "F-F-A"},
    ]
    
    # Pre-calculate GTO percents for these 4 situations
    gto_pcts = {}
    for sit in situations:
        gto_pcts[sit["name"]] = calc_gto_push_pct(gto, 4, sit["pos"], sit["priors"])

    lines = []
    lines.append("### トッププレイー（多プレイ順）の GTO 乖離分析 (4-MAX)")
    lines.append("")
    
    # Header
    headers = ["Player", "Total Hands"] + [f"{s['name']}<br>(GTO {gto_pcts[s['name']]:.1f}%)" for s in situations]
    lines.append("| " + " | ".join(headers) + " |")
    lines.append("|" + "|".join(["---"] * len(headers)) + "|")
    
    for _, row in top_players_df.iterrows():
        pid = row['player_id']
        total_hands = row['hands_seen']
        
        is_hero = (pid == hero_id)
        display_name = f"Hero (Bot)\n`{pid}`" if is_hero else f"Villain\n`{pid}`"
        
        cols = [display_name, f"{total_hands:,}"]
        
        for sit in situations:
            filt = df[(df['player_id'] == pid) & (df['position'] == sit['pos']) & (df['prior_actions'] == sit['priors'])]
            actual_pct = (filt['action'] == 'A').mean() * 100 if not filt.empty else 0
            n = len(filt)
            
            gto_p = gto_pcts[sit["name"]]
            
            if n < 10: # Too small sample size to be meaningful
                cell = f"N/A<br>*(n={n})*"
            else:
                diff = actual_pct - gto_p
                diff_str = f"<span style='color: {'red' if diff > 5 else 'green' if diff < -5 else 'gray'}'>{diff:+.1f}%</span>"
                cell = f"**{actual_pct:.1f}%**<br>{diff_str}<br>*(n={n})*"
                
            cols.append(cell)
            
        lines.append("| " + " | ".join(cols) + " |")
        
    with open("d:/aof_bot/top5_gto.md", "w", encoding="utf-8") as f:
        f.write("\n".join(lines))

if __name__ == "__main__":
    generate_top5_stats()
