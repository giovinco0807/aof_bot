"""Calculate EV for April 1st sessions, for 2 hero IDs."""
import sqlite3, datetime, sys, io
from treys import Card, Evaluator, Deck
from pathlib import Path

sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding='utf-8')
# Also write to file
_out_file = open('d:/aof_bot/ev_latest.txt', 'w', encoding='utf-8')
_orig_print = print
def print(*args, **kwargs):
    if kwargs.get('file') is sys.stderr:
        _orig_print(*args, **kwargs)
    else:
        _orig_print(*args, **kwargs)
        kwargs.pop('file', None)
        _orig_print(*args, file=_out_file, **kwargs)

DB_PATH = Path("d:/aof_bot/automation/data/hands.db")
HERO_IDS = ["13268363", "13076903"]
DATE_FILTER = "2026-04-01"
SESSION_GAP = 1800
EQUITY_SIMS = 2000

evaluator = Evaluator()

def calc_equity(cards_list, n=EQUITY_SIMS):
    if len(cards_list) < 2:
        return [1.0] * len(cards_list)
    parsed = []
    for hc in cards_list:
        if len(hc) < 4: return None
        try:
            parsed.append([Card.new(hc[0:2]), Card.new(hc[2:4])])
        except: return None
    known = [c for h in parsed for c in h]
    wins = [0.0] * len(parsed)
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
        FROM hand_players hp
        JOIN hands h ON hp.hand_id = h.id
        WHERE hp.player_id = ? AND h.timestamp LIKE ?
        ORDER BY h.timestamp ASC
    """, (hero_id, DATE_FILTER + "%")).fetchall()
    
    if not rows:
        print(f"  No hands found for {hero_id} on {DATE_FILTER}")
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
    
    print(f"\n  Hero: {hero_id} | Date: {DATE_FILTER} | Hands: {len(rows)} | Sessions: {len(sessions)}")
    print(f"  {'-'*65}")
    
    grand_pl = 0; grand_ev = 0.0; grand_hands = 0; grand_sd = 0
    
    for si, session in enumerate(sessions):
        start = session[0]["time"]
        end = session[-1]["time"]
        dur = end - start
        nh = len(session)
        
        total_profit = 0; total_ev = 0.0; sd_count = 0; mw_count = 0; fold_c = 0; nc_c = 0
        
        for hand in session:
            hp = hand["profit"]
            total_profit += hp
            ha = hand["action"].upper()
            
            if ha == "F":
                total_ev += hp; fold_c += 1; continue
            
            players = all_hp.get(hand["hand_id"], [])
            allin_wc = []
            hero_idx = -1
            for pid, pa, pc, pp in players:
                if pa and pa.upper() == "A" and pc and len(pc) >= 4:
                    if pid == hero_id: hero_idx = len(allin_wc)
                    allin_wc.append((pid, pc))
            
            if len(allin_wc) < 2 or hero_idx < 0:
                total_ev += hp; nc_c += 1; continue
            
            eqs = calc_equity([c for _,c in allin_wc])
            if eqs is None:
                total_ev += hp; continue
            
            nai = len(allin_wc)
            net_pot = hand["pot"] - hand["rake"]
            hero_ev = eqs[hero_idx] * net_pot - (net_pot / nai)
            total_ev += hero_ev
            sd_count += 1
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
        luck = "LUCKY" if diff_bb < -1 else ("UNLUCKY" if diff_bb > 1 else "NEUTRAL")
        
        print(f"  Sess {si+1}: {start.strftime('%H:%M')}~{end.strftime('%H:%M')} ({dur_s}) | {nh} hands | SD: {sd_count} (MW:{mw_count})")
        print(f"    P/L: {pls}{total_profit:>8.0f} ({pls}{pl_bb:>6.1f}BB) BB/100:{bb100:>+6.1f}")
        print(f"    EV:  {evs}{total_ev:>8.0f} ({evs}{ev_bb:>6.1f}BB) BB/100:{ev100:>+6.1f}")
        print(f"    Diff: {ds}{diff_bb:>6.1f}BB  [{luck}]")
        
        grand_pl += total_profit; grand_ev += total_ev; grand_hands += nh; grand_sd += sd_count
    
    if grand_hands > 0:
        bb = sessions[0][0]["bb_size"] if sessions else 2000
        if bb <= 0: bb = 2000
        gpl = grand_pl / bb; gev = grand_ev / bb; gdiff = gev - gpl
        pls = "+" if grand_pl >= 0 else ""
        evs = "+" if grand_ev >= 0 else ""
        ds = "+" if gdiff >= 0 else ""
        print(f"  {'-'*65}")
        print(f"  TOTAL: {grand_hands} hands, {grand_sd} showdowns")
        print(f"    P/L: {pls}{grand_pl:>8.0f} ({pls}{gpl:>6.1f}BB) BB/100:{gpl/grand_hands*100:>+6.1f}")
        print(f"    EV:  {evs}{grand_ev:>8.0f} ({evs}{gev:>6.1f}BB) BB/100:{gev/grand_hands*100:>+6.1f}")
        print(f"    Diff: {ds}{gdiff:>6.1f}BB")

def main():
    conn = sqlite3.connect(str(DB_PATH))
    print(f"{'='*70}")
    print(f"  EV ANALYSIS - {DATE_FILTER}")
    print(f"{'='*70}")
    for hid in HERO_IDS:
        analyze_hero(conn, hid)
    conn.close()
    print(f"\n{'='*70}")

if __name__ == "__main__":
    main()
