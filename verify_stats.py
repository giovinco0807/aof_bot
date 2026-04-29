import sqlite3
import pandas as pd

def check_hero_vs_villains():
    conn = sqlite3.connect("d:/aof_bot/automation/data/hands.db")
    hero_id_df = pd.read_sql_query("SELECT player_id FROM player_stats ORDER BY hands_seen DESC LIMIT 1", conn)
    hero_id = hero_id_df.iloc[0]['player_id']
    
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
    df['is_hero'] = df['player_id'] == hero_id
    
    # Correct prior actions with hyphens
    unop_priors = {"CO": "", "BTN": "F", "SB": "F-F"}
    
    lines = []
    lines.append(f"Hero ID: {hero_id}\n")
    lines.append(f"{'Position':<8} | {'Category':<10} | {'Push%':<8} | {'Total Hands'}")
    lines.append("-" * 50)
    
    for pos in ["CO", "BTN", "SB"]:
        prior = unop_priors[pos]
        filt = df[(df['position'] == pos) & (df['prior_actions'] == prior)]
        
        # Hero
        hero_filt = filt[filt['is_hero']]
        if not hero_filt.empty:
            hero_pct = (hero_filt['action'] == 'A').mean() * 100
            lines.append(f"{pos:<8} | {'Bot (Hero)':<10} | {hero_pct:<7.1f}% | {len(hero_filt)}")
            
        # Villains
        villain_filt = filt[~filt['is_hero']]
        if not villain_filt.empty:
            villain_pct = (villain_filt['action'] == 'A').mean() * 100
            lines.append(f"{pos:<8} | {'Opponents':<10} | {villain_pct:<7.1f}% | {len(villain_filt)}")
            
        lines.append("-" * 50)

    with open("d:/aof_bot/verify_out.txt", "w", encoding="utf-8") as f:
        f.write("\n".join(lines))

if __name__ == "__main__":
    check_hero_vs_villains()
