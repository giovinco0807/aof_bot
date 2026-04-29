"""Statistical variance analysis for AoF.
Computes actual per-hand variance from DB, projects to 20K hands,
and calculates probability of observed downswings.
"""
import sqlite3, math, sys
from pathlib import Path

DB = Path("d:/aof_bot/automation/data/hands.db")
OUT = Path("d:/aof_bot/ev_variance.txt")
_fh = open(str(OUT), 'w', encoding='utf-8')
def log(*a):
    line = ' '.join(str(x) for x in a)
    _fh.write(line + '\n'); _fh.flush()

def normal_cdf(z):
    """Approximate standard normal CDF."""
    return 0.5 * (1 + math.erf(z / math.sqrt(2)))

def main():
    conn = sqlite3.connect(str(DB))
    bb = 2000.0
    
    log("="*75)
    log("  AoF VARIANCE ANALYSIS")
    log("="*75)
    
    heroes = [
        ("13076903", "2026-03-24", "2026-04-04"),
        ("13268363", "2026-03-24", "2026-04-04"),
    ]
    
    all_profits_combined = []
    
    for hero_id, ds, de in heroes:
        rows = conn.execute("""
            SELECT hp.profit_chips, hp.action, h.bb_size
            FROM hand_players hp JOIN hands h ON hp.hand_id = h.id
            WHERE hp.player_id = ? AND h.timestamp >= ? AND h.timestamp < ?
            ORDER BY h.timestamp ASC
        """, (hero_id, ds, de)).fetchall()
        
        if not rows: continue
        
        n = len(rows)
        profits_bb = [(p or 0) / (b or 2000) for p, _, b in rows]
        
        # Basic stats
        total = sum(profits_bb)
        mean = total / n
        var = sum((x - mean)**2 for x in profits_bb) / n
        sd = math.sqrt(var)
        
        # Separate by action type
        fold_profs = [(p or 0)/(b or 2000) for p,a,b in rows if a and a.upper()=='F']
        allin_profs = [(p or 0)/(b or 2000) for p,a,b in rows if a and a.upper()=='A']
        
        fold_mean = sum(fold_profs)/len(fold_profs) if fold_profs else 0
        fold_var = sum((x-fold_mean)**2 for x in fold_profs)/len(fold_profs) if fold_profs else 0
        allin_mean = sum(allin_profs)/len(allin_profs) if allin_profs else 0
        allin_var = sum((x-allin_mean)**2 for x in allin_profs)/len(allin_profs) if allin_profs else 0
        
        all_profits_combined.extend(profits_bb)
        
        log(f"\n  Hero: {hero_id} | {n} hands ({ds} ~ {de})")
        log(f"  {'-'*65}")
        log(f"  Overall:    mean={mean:>+.3f} BB/hand  SD={sd:.2f} BB/hand  var={var:.2f}")
        log(f"  Fold ({len(fold_profs):>4}): mean={fold_mean:>+.3f} BB/hand  SD={math.sqrt(fold_var):.2f}")
        log(f"  Allin({len(allin_profs):>4}): mean={allin_mean:>+.3f} BB/hand  SD={math.sqrt(allin_var):.2f}")
        log(f"  Total P/L: {total:>+.1f} BB  BB/100: {mean*100:>+.2f}")
    
    # Combined stats
    n_total = len(all_profits_combined)
    mean_all = sum(all_profits_combined) / n_total
    var_all = sum((x - mean_all)**2 for x in all_profits_combined) / n_total
    sd_all = math.sqrt(var_all)
    
    log(f"\n{'='*75}")
    log(f"  COMBINED STATS (both accounts)")
    log(f"{'='*75}")
    log(f"  Total hands: {n_total}")
    log(f"  Per-hand:  mean = {mean_all:>+.4f} BB  SD = {sd_all:.3f} BB  var = {var_all:.3f}")
    log(f"  BB/100:     {mean_all*100:>+.2f}")
    
    log(f"\n{'='*75}")
    log(f"  PROJECTION TO N HANDS")
    log(f"{'='*75}")
    
    for N in [5000, 10000, 20000, 50000]:
        # Over N hands:
        # E[total] = N * mean
        # SD[total] = sd * sqrt(N)
        expected = N * mean_all
        sd_total = sd_all * math.sqrt(N)
        
        # 95% confidence interval
        ci95_lo = expected - 1.96 * sd_total
        ci95_hi = expected + 1.96 * sd_total
        
        # Breakeven probability (P(total > 0))
        if sd_total > 0:
            z_be = -expected / sd_total
            p_profit = 1 - normal_cdf(z_be)
        else:
            p_profit = 0.5
        
        log(f"\n  N = {N:,} hands:")
        log(f"    Expected P/L: {expected:>+.0f} BB  (BB/100: {mean_all*100:>+.2f})")
        log(f"    SD: {sd_total:.0f} BB")
        log(f"    95% CI: [{ci95_lo:>+.0f}, {ci95_hi:>+.0f}] BB")
        log(f"    P(profitable): {p_profit:.1%}")
        log(f"    1 SD range: [{expected-sd_total:>+.0f}, {expected+sd_total:>+.0f}]")
        log(f"    2 SD range: [{expected-2*sd_total:>+.0f}, {expected+2*sd_total:>+.0f}]")
    
    # Observed downswings
    log(f"\n{'='*75}")
    log(f"  OBSERVED DOWNSWING PROBABILITY")
    log(f"{'='*75}")
    
    # This week both accounts
    cases = [
        ("13076903 今週(6426h)", 6426, -293.6, -26.6),
        ("13268363 今週(4487h)", 4487, -165.0, -116.1),
        ("両アカ合計 今週(10913h)", 10913, -458.7, -142.7),
        ("13268363 先週(6493h)", 6493, -687.5, -382.4),
        ("13076903 先週(900h)", 900, -29.9, -28.6),
        ("全期間合計(~17800h)", 17800, -1175.9, -553.7),
    ]
    
    for label, n_hands, actual_pl, ev_pl in cases:
        diff = actual_pl - ev_pl  # negative = ran below EV
        sd_n = sd_all * math.sqrt(n_hands)
        z = diff / sd_n if sd_n > 0 else 0
        p = normal_cdf(z)  # P(running this bad or worse)
        
        log(f"\n  {label}:")
        log(f"    P/L={actual_pl:>+.1f}BB  EV={ev_pl:>+.1f}BB  Diff={diff:>+.1f}BB")
        log(f"    Expected SD: {sd_n:.0f}BB")
        log(f"    z-score: {z:+.2f}")
        log(f"    P(this bad or worse): {p:.1%}")
        if z < -1:
            log(f"    → 約 {int(1/p)} 回に1回程度の下振れ")
    
    conn.close()
    log(f"\n{'='*75}")
    _fh.close()
    try: print(f"Done. Saved to {OUT}", file=sys.stderr)
    except: pass

if __name__ == "__main__":
    main()
