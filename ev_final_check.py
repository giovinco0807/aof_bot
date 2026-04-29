"""Final verification: pot_chips IS already net of rake.
Correct formula: hero_ev = equity * pot_chips - stack_size
Verify with zero-sum check on 200 hands.
"""
import sqlite3, sys
from treys import Card, Evaluator, Deck
from pathlib import Path

DB_PATH = Path("d:/aof_bot/automation/data/hands.db")
OUT = Path("d:/aof_bot/ev_final_check.txt")
_fh = open(str(OUT), 'w', encoding='utf-8')
def log(*a):
    line = ' '.join(str(x) for x in a)
    _fh.write(line + '\n'); _fh.flush()

SIMS = 3000
evaluator = Evaluator()

def calc_equity(cards_list, n=SIMS):
    parsed = []
    for hc in cards_list:
        if len(hc)<4: return None
        try: parsed.append([Card.new(hc[0:2]),Card.new(hc[2:4])])
        except: return None
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
    hero_id = "13076903"
    
    hands = conn.execute("""
        SELECT h.id, h.pot_chips, h.rake_chips, h.bb_size, hp.profit_chips
        FROM hand_players hp JOIN hands h ON hp.hand_id = h.id
        WHERE hp.player_id = ? AND h.timestamp LIKE '2026-04-01%' AND hp.action = 'A'
        ORDER BY h.timestamp ASC LIMIT 200
    """, (hero_id,)).fetchall()
    
    log("="*75)
    log("  FINAL EV VERIFICATION")
    log("  pot_chips = net (winner receives this)")
    log("  gross_pot = pot_chips + rake_chips")
    log("  hero_ev = equity * pot_chips - stack")
    log("="*75)
    
    n = 0
    sum_actual = 0.0
    sum_ev_wrong1 = 0.0  # OLD: eq*(pot-rake) - (pot-rake)/N
    sum_ev_wrong2 = 0.0  # PREV FIX: eq*(pot-rake) - stack
    sum_ev_correct = 0.0 # CORRECT: eq*pot - stack  (pot is already net)
    
    detail_count = 0
    
    for row in hands:
        hid, pot, rake, bb, hero_profit = row
        pot = pot or 0; rake = rake or 0; bb = bb or 2000; hero_profit = hero_profit or 0
        
        players = conn.execute("SELECT player_id, action, cards, profit_chips FROM hand_players WHERE hand_id = ?", (hid,)).fetchall()
        
        allin = []
        hero_idx = -1
        for pid, pa, pc, pp in players:
            if pa and pa.upper() == "A" and pc and len(pc) >= 4:
                if pid == hero_id: hero_idx = len(allin)
                allin.append((pid, pc, pp or 0))
        
        if len(allin) < 2 or hero_idx < 0: continue
        
        eqs = calc_equity([c for _,c,_ in allin])
        if eqs is None: continue
        
        n += 1
        stack = 16000.0  # 8BB = 8*2000
        eq = eqs[hero_idx]
        
        ev_old  = eq * (pot - rake) - (pot - rake) / len(allin)
        ev_fix1 = eq * (pot - rake) - stack
        ev_fix2 = eq * pot - stack   # pot is already net!
        
        sum_actual += hero_profit
        sum_ev_wrong1 += ev_old
        sum_ev_wrong2 += ev_fix1
        sum_ev_correct += ev_fix2
        
        # Show first 3 in detail
        if detail_count < 3:
            detail_count += 1
            gross = pot + rake
            log(f"\n  Hand {hid}: pot_chips={pot:.0f} rake={rake:.0f} gross={gross:.0f} N_allin={len(allin)}")
            log(f"    gross_pot / N = {gross/len(allin):.0f} (should = stack {stack:.0f})")
            
            for i, (pid, pc, pp) in enumerate(allin):
                hero_mark = " <<HERO" if i == hero_idx else ""
                log(f"    P{i+1}[{pc}] eq={eqs[i]:.3f} actual={pp:>+8.0f}"
                    f" | ev_correct={eqs[i]*pot - stack:>+8.0f}{hero_mark}")
            
            # Zero-sum check with correct formula
            sum_ev_all = sum(eqs[i]*pot - stack for i in range(len(allin)))
            sum_act_all = sum(pp for _,_,pp in allin)
            dead = sum(pp or 0 for _,a,_,pp in players if a and a.upper()=='F')
            log(f"    Sum(allin EV)={sum_ev_all:+.0f} Sum(allin actual)={sum_act_all:+.0f} dead={dead:.0f}")
            log(f"    Check: pot - N*stack = {pot - len(allin)*stack:.0f} = dead_money - rake? (dead={dead:.0f} rake={rake:.0f} diff={dead+rake:.0f})")
    
    bb = 2000.0
    log(f"\n{'='*75}")
    log(f"  AGGREGATE over {n} showdown hands (hero={hero_id})")
    log(f"{'='*75}")
    log(f"  Actual P/L:     {sum_actual/bb:>+8.1f} BB")
    log(f"  OLD (net/N):    {sum_ev_wrong1/bb:>+8.1f} BB  diff={sum_ev_wrong1/bb - sum_actual/bb:>+7.1f}")
    log(f"  FIX1 (net-stk): {sum_ev_wrong2/bb:>+8.1f} BB  diff={sum_ev_wrong2/bb - sum_actual/bb:>+7.1f}")
    log(f"  FIX2 (pot-stk): {sum_ev_correct/bb:>+8.1f} BB  diff={sum_ev_correct/bb - sum_actual/bb:>+7.1f}")
    log(f"")
    log(f"  The formula with smallest systematic bias is the correct one.")
    log(f"  Random MC noise is ~sqrt(N)*8BB = ~{8*n**0.5:.0f} BB")
    
    conn.close()
    log(f"\n{'='*75}")
    _fh.close()

if __name__ == "__main__":
    main()
