"""Verify EV formula correctness with concrete hand examples.

Picks 5 showdown hands and shows full step-by-step calculation.
Also checks: sum of ALL players' EV should roughly = sum of actual profits = -rake.
"""
import sqlite3, sys, io
from treys import Card, Evaluator, Deck
from pathlib import Path

DB_PATH = Path("d:/aof_bot/automation/data/hands.db")
OUT = Path("d:/aof_bot/ev_handcheck.txt")
_fh = open(str(OUT), 'w', encoding='utf-8')
def log(*a):
    line = ' '.join(str(x) for x in a)
    _fh.write(line + '\n'); _fh.flush()

SIMS = 5000  # High accuracy for verification
evaluator = Evaluator()

def calc_equity(cards_list, n=SIMS):
    parsed = []
    for hc in cards_list:
        parsed.append([Card.new(hc[0:2]), Card.new(hc[2:4])])
    known = [c for h in parsed for c in h]
    wins = [0.0]*len(parsed)
    for _ in range(n):
        deck = Deck()
        for c in known:
            if c in deck.cards: deck.cards.remove(c)
        board = deck.draw(5)
        scores = [evaluator.evaluate(board, h) for h in parsed]
        best = min(scores)
        ws = [i for i,s in enumerate(scores) if s == best]
        sh = 1.0/len(ws)
        for w in ws: wins[w] += sh
    return [w/n for w in wins]

def main():
    conn = sqlite3.connect(str(DB_PATH))
    log("="*75)
    log("  EV FORMULA VERIFICATION - Hand-by-Hand Check")
    log("="*75)
    
    # Get 10 showdown hands where hero=13076903 went all-in on Apr 1
    hero_id = "13076903"
    hands = conn.execute("""
        SELECT h.id, h.timestamp, h.pot_chips, h.rake_chips, h.bb_size, h.num_players,
               hp.profit_chips
        FROM hand_players hp JOIN hands h ON hp.hand_id = h.id
        WHERE hp.player_id = ? AND h.timestamp LIKE '2026-04-01%'
              AND hp.action = 'A'
        ORDER BY h.timestamp ASC
        LIMIT 200
    """, (hero_id,)).fetchall()
    
    log(f"\n  Found {len(hands)} all-in hands for hero {hero_id}")
    
    # Pick 5 diverse hands (first, mid, last, a win, a loss)
    shown = 0
    total_actual_all = 0.0
    total_ev_new_all = 0.0
    total_ev_old_all = 0.0
    n_checked = 0
    
    for row in hands:
        hid, ts, pot, rake, bb, np, hero_profit = row
        pot = pot or 0; rake = rake or 0; bb = bb or 2000; hero_profit = hero_profit or 0
        
        # Get all players
        players = conn.execute("""
            SELECT player_id, action, cards, profit_chips
            FROM hand_players WHERE hand_id = ?
        """, (hid,)).fetchall()
        
        # Get all-in players with cards
        allin = []
        hero_idx = -1
        for pid, pa, pc, pp in players:
            if pa and pa.upper() == "A" and pc and len(pc) >= 4:
                if pid == hero_id: hero_idx = len(allin)
                allin.append({"pid": pid, "cards": pc, "profit": pp or 0})
        
        if len(allin) < 2 or hero_idx < 0:
            continue
        
        n_checked += 1
        
        # Calc equity
        eqs = calc_equity([p["cards"] for p in allin])
        net_pot = pot - rake
        
        # Loser-based stack
        loser_profs = [p["profit"] for p in allin if p["profit"] < 0]
        stack = -min(loser_profs) if loser_profs else 8 * bb
        
        # NEW EV for ALL players
        ev_all = []
        for i, p in enumerate(allin):
            ev_all.append(eqs[i] * net_pot - stack)
        
        # OLD EV
        old_ev_hero = eqs[hero_idx] * net_pot - (net_pot / len(allin))
        new_ev_hero = ev_all[hero_idx]
        
        total_actual_all += hero_profit
        total_ev_new_all += new_ev_hero
        total_ev_old_all += old_ev_hero
        
        # Show first 5 in detail
        if shown < 5:
            shown += 1
            log(f"\n  --- Hand #{shown} (id={hid}) {ts} ---")
            log(f"  Table: {np}P | Pot: {pot} | Rake: {rake} | Net: {net_pot} | BB: {bb}")
            log(f"  All-in players: {len(allin)} | Stack (from losers): {stack}")
            log(f"  Folders' dead money in pot: {pot - stack * len(allin)}")
            log(f"")
            
            # Show all players
            sum_actual = 0
            sum_ev = 0
            for i, p in enumerate(allin):
                hero_mark = " <<HERO" if i == hero_idx else ""
                eq_pct = eqs[i] * 100
                ev_profit = ev_all[i]
                actual = p["profit"]
                sum_actual += actual
                sum_ev += ev_profit
                sign_a = "+" if actual >= 0 else ""
                sign_e = "+" if ev_profit >= 0 else ""
                log(f"    P{i+1} [{p['cards']}] eq={eq_pct:5.1f}% | actual={sign_a}{actual:>7} | EV={sign_e}{ev_profit:>7.0f}{hero_mark}")
            
            # Dead money from folders
            fold_total = 0
            for pid, pa, pc, pp in players:
                if pa and pa.upper() == "F":
                    fold_total += (pp or 0)
            
            log(f"")
            log(f"    Sum(all-in actual) = {sum_actual}")
            log(f"    Sum(all-in EV)     = {sum_ev:.0f}")
            log(f"    Folders' loss       = {fold_total}")
            log(f"    -Rake              = {-rake}")
            log(f"    Sum(ALL actual)    = {sum_actual + fold_total} (should = {-rake})")
            log(f"")
            log(f"    OLD hero EV = eq*net_pot - net_pot/N = {eqs[hero_idx]:.3f}*{net_pot} - {net_pot}/{len(allin)} = {old_ev_hero:.0f}")
            log(f"    NEW hero EV = eq*net_pot - stack     = {eqs[hero_idx]:.3f}*{net_pot} - {stack} = {new_ev_hero:.0f}")
            log(f"    Hero actual profit = {hero_profit}")
            log(f"    OLD diff = {old_ev_hero - hero_profit:+.0f} | NEW diff = {new_ev_hero - hero_profit:+.0f}")
    
    # Aggregate check
    log(f"\n{'='*75}")
    log(f"  AGGREGATE CHECK over {n_checked} showdown hands")
    log(f"{'='*75}")
    log(f"  Total hero actual P/L: {total_actual_all:>+10.0f}")
    log(f"  Total hero OLD EV:     {total_ev_old_all:>+10.0f}  (diff: {total_ev_old_all - total_actual_all:>+8.0f})")
    log(f"  Total hero NEW EV:     {total_ev_new_all:>+10.0f}  (diff: {total_ev_new_all - total_actual_all:>+8.0f})")
    log(f"  OLD bias per hand:     {(total_ev_old_all - total_ev_new_all)/n_checked:>+.1f} chips")
    
    conn.close()
    log(f"\n{'='*75}")
    _fh.close()

if __name__ == "__main__":
    main()
