"""Verify EV formula for bias.

Current formula: hero_ev = equity * net_pot - net_pot / num_allin
Correct formula: hero_ev = equity * net_pot - hero_investment

In AoF, hero_investment = hero's full stack (8BB typically).
net_pot / num_allin != hero_investment because dead money from folders
inflates net_pot, making net_pot/N < actual_investment when dead_money > rake,
or > actual_investment when rake > dead_money.

This script:
1. Checks what the actual bias is using real hand data
2. Recalculates EV with corrected formula
3. Runs statistical significance test
"""
import sqlite3, datetime, sys, io, math
from treys import Card, Evaluator, Deck
from pathlib import Path

OUT = Path("d:/aof_bot/ev_verify.txt")
_fh = open(str(OUT), 'w', encoding='utf-8')
def log(*a):
    line = ' '.join(str(x) for x in a)
    _fh.write(line + '\n'); _fh.flush()
    try: print(line, file=sys.stderr)
    except: pass

DB_PATH = Path("d:/aof_bot/automation/data/hands.db")
EQUITY_SIMS = 1500
evaluator = Evaluator()

def calc_equity(cards_list, n=EQUITY_SIMS):
    if len(cards_list) < 2: return [1.0]*len(cards_list)
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
    
    hero_ids = ["13076903", "13268363"]
    date_filter = "2026-04-01"
    
    log("="*75)
    log("  EV FORMULA VERIFICATION")
    log("="*75)
    
    for hero_id in hero_ids:
        rows = conn.execute("""
            SELECT h.id, h.timestamp, h.pot_chips, h.rake_chips, h.bb_size,
                   hp.action, hp.profit_chips, hp.cards
            FROM hand_players hp JOIN hands h ON hp.hand_id = h.id
            WHERE hp.player_id = ? AND h.timestamp LIKE ?
            ORDER BY h.timestamp ASC
        """, (hero_id, date_filter + "%")).fetchall()
        
        if not rows: continue
        
        hand_ids = set(r[0] for r in rows)
        all_hp = {}
        for hid in hand_ids:
            ps = conn.execute("SELECT player_id, action, cards, profit_chips FROM hand_players WHERE hand_id = ?", (hid,)).fetchall()
            all_hp[hid] = ps
        
        log(f"\n  Hero: {hero_id} | {len(rows)} hands on {date_filter}")
        log(f"  {'-'*65}")

        cum_pl_old = 0.0  # Old formula (net_pot/N)
        cum_pl_new = 0.0  # New formula (actual investment from profit data)
        cum_actual = 0.0
        sd_count = 0
        bias_samples = []
        
        for row in rows:
            hid, ts, pot, rake, bb, action, profit, cards = row
            pot = pot or 0; rake = rake or 0; bb = bb or 2000; profit = profit or 0
            action = (action or "").upper()
            cum_actual += profit
            
            if action == "F":
                cum_pl_old += profit
                cum_pl_new += profit
                continue

            players = all_hp.get(hid, [])
            allin_wc = []
            hero_idx = -1
            all_profits = {}  # pid -> profit
            for pid, pa, pc, pp in players:
                all_profits[pid] = pp or 0
                if pa and pa.upper() == "A" and pc and len(pc) >= 4:
                    if pid == hero_id: hero_idx = len(allin_wc)
                    allin_wc.append((pid, pc, pp or 0))
            
            if len(allin_wc) < 2 or hero_idx < 0:
                cum_pl_old += profit
                cum_pl_new += profit
                continue
            
            eqs = calc_equity([c for _,c,_ in allin_wc])
            if eqs is None:
                cum_pl_old += profit
                cum_pl_new += profit
                continue
            
            nai = len(allin_wc)
            net_pot = pot - rake
            hero_eq = eqs[hero_idx]
            hero_profit_actual = profit
            
            # OLD formula: investment = net_pot / N
            old_ev = hero_eq * net_pot - (net_pot / nai)
            
            # NEW formula: derive investment from actual data
            # In AoF, all players have equal stacks.
            # We can infer stack from the LOSER's profit (loser_profit = -stack)
            # Find the biggest loser among all-in players
            loser_profits = [pp for _,_,pp in allin_wc if pp < 0]
            if loser_profits:
                stack_size = -min(loser_profits)  # Most negative = full stack loss
            else:
                stack_size = 8 * bb  # Fallback: 8BB
            
            new_ev = hero_eq * net_pot - stack_size
            
            cum_pl_old += old_ev
            cum_pl_new += new_ev
            sd_count += 1
            bias_samples.append(old_ev - new_ev)
        
        old_bb = cum_pl_old / bb
        new_bb = cum_pl_new / bb
        act_bb = cum_actual / bb
        
        old_diff = old_bb - act_bb
        new_diff = new_bb - act_bb
        
        avg_bias = sum(bias_samples) / len(bias_samples) if bias_samples else 0
        total_bias = sum(bias_samples) / bb
        
        log(f"  Showdowns: {sd_count}")
        log(f"  Actual P/L:         {act_bb:>+8.1f} BB")
        log(f"  OLD EV (net_pot/N): {old_bb:>+8.1f} BB  (diff: {old_diff:>+7.1f} BB)")
        log(f"  NEW EV (real stack):{new_bb:>+8.1f} BB  (diff: {new_diff:>+7.1f} BB)")
        log(f"  Formula bias total: {total_bias:>+7.1f} BB  (avg {avg_bias/bb:>+.3f} BB/hand)")
        log(f"")
        log(f"  === STATISTICAL TEST ===")
        
        # Standard deviation of EV-actual per showdown hand
        # In AoF HU with 8BB stacks: variance ~ eq*(1-eq)*pot^2
        # Typical: eq~0.5, pot~16BB -> var~64 -> sd~8BB per hand
        # Over N showdowns: SD = 8 * sqrt(N)
        sd_per_hand = 8.0  # rough estimate
        expected_sd = sd_per_hand * math.sqrt(sd_count)
        z_old = old_diff / expected_sd if expected_sd > 0 else 0
        z_new = new_diff / expected_sd if expected_sd > 0 else 0
        
        # Two-tailed p-value from z-score
        def p_from_z(z):
            # Approximate using error function
            return 1 - math.erf(abs(z) / math.sqrt(2))
        
        p_old = p_from_z(z_old)
        p_new = p_from_z(z_new)
        
        log(f"  Expected SD over {sd_count} showdowns: ~{expected_sd:.0f} BB")
        log(f"  OLD: z={z_old:+.2f} (p={p_old:.3f}) {'***SIGNIFICANT***' if p_old < 0.05 else 'normal variance'}")
        log(f"  NEW: z={z_new:+.2f} (p={p_new:.3f}) {'***SIGNIFICANT***' if p_new < 0.05 else 'normal variance'}")
        
        # Probability of running below EV
        if new_diff > 0:
            p_below = 1 - 0.5 * (1 + math.erf(new_diff / (expected_sd * math.sqrt(2))))
            log(f"  P(this unlucky or worse): {p_below:.1%}")
        
    conn.close()
    log(f"\n{'='*75}")
    _fh.close()

if __name__ == "__main__":
    main()
