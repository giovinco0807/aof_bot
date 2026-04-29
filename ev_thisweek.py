"""Calculate EV for this week (Mar 31 - Apr 3) for 2 hero IDs.
Uses correct formula: hero_ev = equity * pot_chips - 8*BB
(pot_chips in DB is already net of rake)
"""
import sqlite3, datetime, sys, io
from treys import Card, Evaluator, Deck
from pathlib import Path

OUT_FILE = Path("d:/aof_bot/ev_thisweek.txt")
_fh = open(str(OUT_FILE), 'w', encoding='utf-8')
def log(*a):
    line = ' '.join(str(x) for x in a)
    _fh.write(line + '\n'); _fh.flush()
    try: print(line, file=sys.stderr)
    except: pass

DB_PATH = Path("d:/aof_bot/automation/data/hands.db")
HERO_IDS = ["13076903", "13268363"]
DATE_START = "2026-03-31"
DATE_END = "2026-04-06"  # exclusive
SESSION_GAP = 1800
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

def analyze_hero(conn, hero_id):
    rows = conn.execute("""
        SELECT h.id, h.timestamp, h.num_players, h.pot_chips, h.rake_chips,
               h.bb_size, hp.action, hp.profit_chips, hp.cards, hp.position
        FROM hand_players hp JOIN hands h ON hp.hand_id = h.id
        WHERE hp.player_id = ? AND h.timestamp >= ? AND h.timestamp < ?
        ORDER BY h.timestamp ASC
    """, (hero_id, DATE_START, DATE_END)).fetchall()

    if not rows:
        log(f"  No hands found for {hero_id}")
        return

    hand_ids = set(r[0] for r in rows)
    all_hp = {}
    for hid in hand_ids:
        ps = conn.execute("SELECT player_id, action, cards, profit_chips FROM hand_players WHERE hand_id = ?", (hid,)).fetchall()
        all_hp[hid] = ps

    sessions = []
    cur = []
    last_t = None
    for row in rows:
        hid, ts_str, np, pot, rake, bb, action, profit, cards, pos = row
        try: ts = datetime.datetime.fromisoformat(ts_str)
        except: continue
        if last_t and (ts - last_t).total_seconds() > SESSION_GAP:
            if cur: sessions.append(cur)
            cur = []
        cur.append({"hand_id":hid,"time":ts,"num_players":np,"pot":pot or 0,
                     "rake":rake or 0,"bb_size":bb or 2000,"action":action or "",
                     "profit":profit or 0,"cards":cards or "","position":pos or ""})
        last_t = ts
    if cur: sessions.append(cur)

    log(f"\n  Hero: {hero_id} | {DATE_START} ~ {DATE_END} | Hands: {len(rows)} | Sessions: {len(sessions)}")
    log(f"  {'-'*74}")

    grand_pl = 0; grand_ev = 0.0; grand_hands = 0; grand_sd = 0; grand_mw = 0; grand_rake = 0

    for si, session in enumerate(sessions):
        start = session[0]["time"]
        end = session[-1]["time"]
        dur = end - start
        nh = len(session)
        total_profit = 0; total_ev = 0.0; sd_count = 0; mw_count = 0; fold_c = 0; nc_c = 0; sess_rake = 0

        for hand in session:
            hp = hand["profit"]
            total_profit += hp
            sess_rake += hand["rake"]
            ha = hand["action"].upper()

            if ha == "F":
                total_ev += hp; fold_c += 1; continue

            players = all_hp.get(hand["hand_id"], [])
            allin_wc = []
            hero_idx = -1
            for pid, pa, pc, pp in players:
                if pa and pa.upper() == "A" and pc and len(pc) >= 4:
                    if pid == hero_id: hero_idx = len(allin_wc)
                    allin_wc.append((pid, pc, pp or 0))

            if len(allin_wc) < 2 or hero_idx < 0:
                total_ev += hp; nc_c += 1; continue

            eqs = calc_equity([c for _,c,_ in allin_wc])
            if eqs is None:
                total_ev += hp; continue

            # pot_chips is already net of rake
            stack_size = 8 * (hand["bb_size"] or 2000)
            hero_ev = eqs[hero_idx] * hand["pot"] - stack_size
            total_ev += hero_ev
            sd_count += 1
            nai = len(allin_wc)
            if nai > 2: mw_count += 1

        bb = session[0]["bb_size"]
        if bb <= 0: bb = 2000
        pl_bb = total_profit / bb
        ev_bb = total_ev / bb
        diff_bb = ev_bb - pl_bb
        bb100 = pl_bb / nh * 100 if nh > 0 else 0
        ev100 = ev_bb / nh * 100 if nh > 0 else 0
        dur_s = f"{int(dur.total_seconds()//3600)}h{int((dur.total_seconds()%3600)//60):02d}m"

        pls = "+" if total_profit >= 0 else ""
        evs = "+" if total_ev >= 0 else ""
        ds = "+" if diff_bb >= 0 else ""
        luck = "LUCKY" if diff_bb < -3 else ("UNLUCKY" if diff_bb > 3 else "~")

        log(f"  S{si+1:>2}: {start.strftime('%m/%d %H:%M')}~{end.strftime('%H:%M')} ({dur_s:>6}) | {nh:>4}h | SD:{sd_count:>3}(MW:{mw_count:>2}) | P/L:{pls}{pl_bb:>7.1f}BB | EV:{evs}{ev_bb:>7.1f}BB | diff:{ds}{diff_bb:>6.1f} {luck}")

        grand_pl += total_profit; grand_ev += total_ev; grand_hands += nh
        grand_sd += sd_count; grand_mw += mw_count; grand_rake += sess_rake

        try: print(f"  [{hero_id}] Session {si+1}/{len(sessions)} done ({grand_hands} hands)", file=sys.stderr)
        except: pass

    if grand_hands > 0:
        bb = sessions[0][0]["bb_size"] if sessions else 2000
        if bb <= 0: bb = 2000
        gpl = grand_pl / bb; gev = grand_ev / bb; gdiff = gev - gpl
        pls = "+" if grand_pl >= 0 else ""
        evs = "+" if grand_ev >= 0 else ""
        ds = "+" if gdiff >= 0 else ""
        log(f"  {'-'*74}")
        log(f"  TOTAL: {grand_hands} hands | {grand_sd} showdowns (MW:{grand_mw}) | Rake: {grand_rake:.0f}")
        log(f"    P/L:  {pls}{grand_pl:>10.0f} chips ({pls}{gpl:>7.1f}BB) BB/100:{gpl/grand_hands*100:>+6.1f}")
        log(f"    EV:   {evs}{grand_ev:>10.0f} chips ({evs}{gev:>7.1f}BB) BB/100:{gev/grand_hands*100:>+6.1f}")
        log(f"    Diff: {ds}{gdiff:>7.1f}BB  Rake: {grand_rake/bb:.1f}BB")

def main():
    conn = sqlite3.connect(str(DB_PATH))
    log(f"{'='*78}")
    log(f"  EV ANALYSIS - This Week ({DATE_START} ~ {DATE_END})")
    log(f"{'='*78}")
    for hid in HERO_IDS:
        analyze_hero(conn, hid)

    # Combined totals
    log(f"\n{'='*78}")
    log(f"  COMBINED TOTALS (both accounts)")
    log(f"{'='*78}")
    bb = 2000
    total_pl = 0; total_ev = 0; total_h = 0
    for hid in HERO_IDS:
        rows = conn.execute("""
            SELECT SUM(hp.profit_chips), COUNT(*)
            FROM hand_players hp JOIN hands h ON hp.hand_id = h.id
            WHERE hp.player_id = ? AND h.timestamp >= ? AND h.timestamp < ?
        """, (hid, DATE_START, DATE_END)).fetchone()
        if rows[0]:
            total_pl += rows[0]
            total_h += rows[1]
    # EV is trickier to combine, just note it
    log(f"  (See individual hero totals above for EV breakdown)")
    log(f"  Combined P/L: {'+' if total_pl>=0 else ''}{total_pl:.0f} chips ({'+' if total_pl>=0 else ''}{total_pl/bb:.1f}BB)")
    log(f"  Total hands: {total_h}")

    conn.close()
    log(f"\n{'='*78}")
    _fh.close()
    try: print(f"Results saved to {OUT_FILE}", file=sys.stderr)
    except: pass

if __name__ == "__main__":
    main()
