"""Deep-dive: understand the AoF pot/investment structure.
For 20 hands, show ALL player profits to derive the exact investment model.
"""
import sqlite3
from pathlib import Path

DB_PATH = Path("d:/aof_bot/automation/data/hands.db")
OUT = Path("d:/aof_bot/ev_structure.txt")
_fh = open(str(OUT), 'w', encoding='utf-8')
def log(*a):
    line = ' '.join(str(x) for x in a)
    _fh.write(line + '\n'); _fh.flush()

def main():
    conn = sqlite3.connect(str(DB_PATH))
    
    hands = conn.execute("""
        SELECT h.id, h.timestamp, h.pot_chips, h.rake_chips, h.bb_size, h.num_players
        FROM hand_players hp JOIN hands h ON hp.hand_id = h.id
        WHERE hp.player_id = '13076903' AND h.timestamp LIKE '2026-04-01%'
              AND hp.action = 'A'
        ORDER BY h.timestamp ASC
        LIMIT 20
    """, ).fetchall()
    
    log("="*80)
    log("  AoF POT/INVESTMENT STRUCTURE ANALYSIS")
    log("="*80)
    
    for row in hands:
        hid, ts, pot, rake, bb, np = row
        pot = pot or 0; rake = rake or 0; bb = bb or 2000
        
        players = conn.execute("""
            SELECT player_id, action, cards, profit_chips
            FROM hand_players WHERE hand_id = ?
            ORDER BY profit_chips DESC
        """, (hid,)).fetchall()
        
        allin_count = sum(1 for _,a,_,_ in players if a and a.upper() == 'A')
        fold_count = sum(1 for _,a,_,_ in players if a and a.upper() == 'F')
        
        log(f"\n  Hand {hid} | {np}P | Pot={pot:.0f} Rake={rake:.0f} BB={bb:.0f}")
        log(f"  All-in: {allin_count} | Fold: {fold_count}")
        
        sum_profit = 0
        sum_winner = 0
        sum_loser = 0
        for pid, action, cards, profit in players:
            profit = profit or 0
            action = (action or "").upper()
            hero = " <<HERO" if pid == '13076903' else ""
            card_str = cards if cards else "----"
            sign = "+" if profit >= 0 else ""
            log(f"    {action} [{card_str:>4}] profit={sign}{profit:>8.0f}{hero}")
            sum_profit += profit
            if profit > 0: sum_winner += profit
            if profit < 0: sum_loser += profit
        
        log(f"    Sum(profits)={sum_profit:.0f} | Winners={sum_winner:.0f} | Losers={sum_loser:.0f}")
        log(f"    -rake={-rake:.0f} | check: sum should = -rake: {'OK' if abs(sum_profit + rake) < 1 else 'MISMATCH'}")
        
        # Derive structure
        if allin_count > 0:
            # winner_profit = pot - rake - winner_investment
            # loser_profit = -loser_investment
            # pot = sum_investments + dead_money
            winner_profit_vals = [p for _,_,_,p in players if (p or 0) > 0]
            loser_profit_vals = [p for _,a,_,p in players if a and a.upper() == 'A' and (p or 0) < 0]
            
            if loser_profit_vals:
                loser_investment = -min(loser_profit_vals)
                log(f"    Loser investment (from profit) = {loser_investment:.0f}")
            if winner_profit_vals:
                winner_investment = pot - rake - max(winner_profit_vals)
                log(f"    Winner investment (pot-rake-profit) = {winner_investment:.0f}")
    
    conn.close()
    log(f"\n{'='*80}")
    _fh.close()

if __name__ == "__main__":
    main()
