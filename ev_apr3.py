"""Calculate EV for April 3rd sessions."""
import sqlite3, datetime, sys
from treys import Card, Evaluator, Deck
from pathlib import Path

OUT = Path("d:/aof_bot/ev_apr3.txt")
_fh = open(str(OUT), 'w', encoding='utf-8')
def log(*a):
    line = ' '.join(str(x) for x in a)
    _fh.write(line + '\n'); _fh.flush()
    try: print(line, file=sys.stderr)
    except: pass

DB = Path("d:/aof_bot/automation/data/hands.db")
HERO_IDS = ["13076903", "13268363"]
DATE = "2026-04-03"
SESSION_GAP = 1800
SIMS = 1500
evaluator = Evaluator()

def calc_equity(cards_list, n=SIMS):
    if len(cards_list)<2: return [1.0]*len(cards_list)
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

def analyze(conn, hero_id):
    rows = conn.execute("""
        SELECT h.id, h.timestamp, h.num_players, h.pot_chips, h.rake_chips,
               h.bb_size, hp.action, hp.profit_chips, hp.cards, hp.position
        FROM hand_players hp JOIN hands h ON hp.hand_id = h.id
        WHERE hp.player_id = ? AND h.timestamp LIKE ?
        ORDER BY h.timestamp ASC
    """, (hero_id, DATE + "%")).fetchall()
    if not rows:
        log(f"  {hero_id}: No hands")
        return
    hand_ids = set(r[0] for r in rows)
    all_hp = {}
    for hid in hand_ids:
        all_hp[hid] = conn.execute("SELECT player_id,action,cards,profit_chips FROM hand_players WHERE hand_id=?", (hid,)).fetchall()

    sessions = []; cur = []; last_t = None
    for row in rows:
        hid,ts_str,np,pot,rake,bb,action,profit,cards,pos = row
        try: ts = datetime.datetime.fromisoformat(ts_str)
        except: continue
        if last_t and (ts-last_t).total_seconds()>SESSION_GAP:
            if cur: sessions.append(cur)
            cur = []
        cur.append({"hand_id":hid,"time":ts,"np":np,"pot":pot or 0,"rake":rake or 0,
                     "bb":bb or 2000,"action":action or "","profit":profit or 0})
        last_t = ts
    if cur: sessions.append(cur)

    log(f"\n  Hero: {hero_id} | {DATE} | Hands: {len(rows)} | Sessions: {len(sessions)}")
    log(f"  {'-'*74}")
    gpl=0; gev=0.0; gh=0; gsd=0; gmw=0; grk=0
    for si, sess in enumerate(sessions):
        s=sess[0]["time"]; e=sess[-1]["time"]; d=e-s; nh=len(sess)
        tp=0; tev=0.0; sd=0; mw=0; rk=0
        for h in sess:
            tp += h["profit"]; rk += h["rake"]
            ha = h["action"].upper()
            if ha == "F": tev += h["profit"]; continue
            players = all_hp.get(h["hand_id"], [])
            aw = []; hi = -1
            for pid,pa,pc,pp in players:
                if pa and pa.upper()=="A" and pc and len(pc)>=4:
                    if pid==hero_id: hi=len(aw)
                    aw.append((pid,pc,pp or 0))
            if len(aw)<2 or hi<0: tev += h["profit"]; continue
            eqs = calc_equity([c for _,c,_ in aw])
            if eqs is None: tev += h["profit"]; continue
            stack = 8 * h["bb"]
            tev += eqs[hi] * h["pot"] - stack
            sd += 1
            if len(aw)>2: mw += 1
        bb=sess[0]["bb"]; pl=tp/bb; ev=tev/bb; df=ev-pl
        hr_rk = sum(h["rake"]/h["np"] for h in sess if h["np"]>0)/bb
        dur=f"{int(d.total_seconds()//3600)}h{int((d.total_seconds()%3600)//60):02d}m"
        ps="+" if tp>=0 else ""; es="+" if tev>=0 else ""; ds="+" if df>=0 else ""
        lk = "LUCKY" if df<-3 else ("UNLUCKY" if df>3 else "~")
        log(f"  S{si+1:>2}: {s.strftime('%H:%M')}~{e.strftime('%H:%M')} ({dur:>6}) | {nh:>4}h | SD:{sd:>3}(MW:{mw:>2}) | P/L:{ps}{pl:>7.1f}BB | EV:{es}{ev:>7.1f}BB | diff:{ds}{df:>6.1f} {lk}")
        gpl+=tp; gev+=tev; gh+=nh; gsd+=sd; gmw+=mw; grk+=rk
        try: print(f"  [{hero_id}] S{si+1}/{len(sessions)} done", file=sys.stderr)
        except: pass
    if gh>0:
        bb=sessions[0][0]["bb"]
        pl=gpl/bb; ev=gev/bb; df=ev-pl; rk_bb=grk/bb
        # Hero's share of rake
        hrk = sum(h["rake"]/h["np"] for s in sessions for h in s if h["np"]>0)/bb
        ps="+" if gpl>=0 else ""; es="+" if gev>=0 else ""; ds="+" if df>=0 else ""
        log(f"  {'-'*74}")
        log(f"  TOTAL: {gh}h | {gsd} SD (MW:{gmw}) | Rake(個人): {hrk:.1f}BB | 70%RB: +{hrk*0.7:.1f}BB")
        log(f"    P/L:  {ps}{gpl:>8.0f} ({ps}{pl:>7.1f}BB) BB/100:{pl/gh*100:>+6.1f}")
        log(f"    EV:   {es}{gev:>8.0f} ({es}{ev:>7.1f}BB) BB/100:{ev/gh*100:>+6.1f}")
        log(f"    Diff: {ds}{df:>7.1f}BB")
        log(f"    RB込みP/L: {ps}{pl+hrk*0.7:>7.1f}BB | RB込みEV: {es}{ev+hrk*0.7:>7.1f}BB")

def main():
    conn = sqlite3.connect(str(DB))
    log(f"{'='*78}")
    log(f"  EV ANALYSIS - {DATE}")
    log(f"{'='*78}")
    for h in HERO_IDS: analyze(conn, h)
    conn.close()
    log(f"\n{'='*78}")
    _fh.close()

if __name__=="__main__": main()
