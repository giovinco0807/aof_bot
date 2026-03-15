"""AoF 4-player Monte Carlo Simulator v3.

Position rotation every hand (like a real game).
Fish = player 0 (fixed seat). Positions rotate around the table.
GTO players tracked as A/B/C relative to fish.
Outputs interactive HTML charts.

Usage:
    python sim_aof.py --hands 100000 --sets 5
"""

import json, random, math, time, sys
from pathlib import Path
from collections import defaultdict, Counter
from itertools import combinations

RANKS = "23456789TJQKA"
SUITS = "shdc"
RANK_VAL = {r: i for i, r in enumerate(RANKS)}
ALL_CARDS = [r + s for r in RANKS for s in SUITS]

def hand_name(c1, c2):
    r1, s1, r2, s2 = c1[0], c1[1], c2[0], c2[1]
    if RANK_VAL[r1] < RANK_VAL[r2]:
        r1, r2, s1, s2 = r2, r1, s2, s1
    if r1 == r2: return r1 + r2
    return r1 + r2 + ("s" if s1 == s2 else "o")

def evaluate_hand_7(cards):
    best = 0
    for c in combinations(cards, 5):
        v = evaluate_5(c)
        if v > best: best = v
    return best

def evaluate_5(five):
    ranks = sorted([RANK_VAL[c[0]] for c in five], reverse=True)
    suits = [c[1] for c in five]
    is_flush = len(set(suits)) == 1
    is_straight, straight_high = False, 0
    uniq = sorted(set(ranks), reverse=True)
    if len(uniq) >= 5:
        for i in range(len(uniq) - 4):
            if uniq[i] - uniq[i+4] == 4:
                is_straight, straight_high = True, uniq[i]
                break
        if not is_straight and {12,0,1,2,3} <= set(ranks):
            is_straight, straight_high = True, 3
    cnt = Counter(ranks)
    groups = sorted(cnt.items(), key=lambda x: (x[1], x[0]), reverse=True)
    pat = tuple(g[1] for g in groups)
    k = [g[0] for g in groups]
    if is_straight and is_flush: return 800000000 + straight_high
    if pat[0] == 4: return 700000000 + k[0]*100 + k[1]
    if pat == (3,2): return 600000000 + k[0]*100 + k[1]
    if is_flush: return 500000000 + ranks[0]*10000+ranks[1]*1000+ranks[2]*100+ranks[3]*10+ranks[4]
    if is_straight: return 400000000 + straight_high
    if pat[0] == 3: return 300000000 + k[0]*10000+k[1]*100+k[2]
    if pat == (2,2,1): return 200000000 + max(k[0],k[1])*10000+min(k[0],k[1])*100+k[2]
    if pat[0] == 2: return 100000000 + k[0]*10000+k[1]*100+k[2]*10+k[3]
    return ranks[0]*10000+ranks[1]*1000+ranks[2]*100+ranks[3]*10+ranks[4]

CHARTS_DIR = Path("d:/aof_bot/solver/data/charts_rb50")
def load_gto_ranges(n=4):
    with open(CHARTS_DIR / f"aof_{n}p_8bb.json") as f:
        data = json.load(f)
    return {(c["position"], c["prior_actions"]): {e["hand"]: e["allin_freq"] for e in c["entries"]} for c in data["charts"]}

ALL_169 = []
def _init():
    seen = set()
    for i in range(12,-1,-1):
        for j in range(12,-1,-1):
            r1, r2 = RANKS[i], RANKS[j]
            h = (r1+r2) if i==j else (r1+r2+"s") if i>j else (r2+r1+"o")
            if h not in seen: seen.add(h); ALL_169.append(h)
_init()

def create_deviated_range(gto, dev):
    return {h: max(0, min(1, gto.get(h, 0) + dev)) for h in ALL_169 if max(0, min(1, gto.get(h, 0) + dev)) > 0.001}

POSITIONS = ["CO", "BTN", "SB", "BB"]
STACK = 8.0; SB_AMT = 0.5; BB_AMT = 1.0
RAKE_PCT = 0.03; RAKE_CAP = 3.0
REL_LABELS = {1: "GTO-A (Right)", 2: "GTO-B (Across)", 3: "GTO-C (Left)"}

def simulate_hand(seat_ranges, hand_idx):
    """seat_ranges = list of 4 range dicts, one per seat. Positions rotate each hand."""
    deck = ALL_CARDS[:]
    random.shuffle(deck)
    hands = [(deck[i*2], deck[i*2+1]) for i in range(4)]
    board = deck[8:13]

    # Position rotation: each hand shifts by 1
    rot = hand_idx % 4
    seat_to_pos = [POSITIONS[(rot + s) % 4] for s in range(4)]
    pos_to_seat = {seat_to_pos[s]: s for s in range(4)}

    sb_seat = pos_to_seat["SB"]
    bb_seat = pos_to_seat["BB"]

    pnl = [0.0] * 4
    pnl[sb_seat] -= SB_AMT
    pnl[bb_seat] -= BB_AMT

    actions = []
    pushers = []
    pushed_flags = [False] * 4  # track who pushed for VPIP

    # Action order: CO → BTN → SB → BB
    for pos in POSITIONS:
        seat = pos_to_seat[pos]
        prior = "".join(actions)
        ranges = seat_ranges[seat]
        key = (pos, prior)
        if key in ranges:
            h = hand_name(hands[seat][0], hands[seat][1])
            freq = ranges[key].get(h, 0.0)
            pushed = (freq >= 1.0) or (freq > 0 and random.random() < freq)
        else:
            pushed = False
        actions.append("A" if pushed else "F")
        if pushed:
            pushers.append(seat)
            pushed_flags[seat] = True

    rake_paid = [0.0] * 4  # per-player rake attribution

    if not pushers:
        pnl[bb_seat] += SB_AMT + BB_AMT
        return pnl, pushed_flags, seat_to_pos, rake_paid
    if len(pushers) == 1:
        pnl[pushers[0]] += SB_AMT + BB_AMT
        return pnl, pushed_flags, seat_to_pos, rake_paid

    for p in pushers:
        if p == sb_seat:      pnl[p] -= STACK - SB_AMT
        elif p == bb_seat:    pnl[p] -= STACK - BB_AMT
        else:                 pnl[p] -= STACK

    dead = (SB_AMT if sb_seat not in pushers else 0) + (BB_AMT if bb_seat not in pushers else 0)
    total_pot = STACK * len(pushers) + dead
    total_rake = min(total_pot * RAKE_PCT, RAKE_CAP)

    best_score, winners = -1, []
    for p in pushers:
        sc = evaluate_hand_7(list(hands[p]) + board)
        if sc > best_score: best_score, winners = sc, [p]
        elif sc == best_score: winners.append(p)

    losers = [p for p in pushers if p not in winners]

    # Rake attribution: winner 1/3, losers 2/3
    winner_rake = total_rake / 3.0
    loser_rake = total_rake * 2.0 / 3.0
    for w in winners:
        rake_paid[w] = winner_rake / len(winners)
    if losers:
        for l in losers:
            rake_paid[l] = loser_rake / len(losers)

    # PnL: pot minus total rake goes to winners
    share = (total_pot - total_rake) / len(winners)
    for w in winners:
        pnl[w] += share

    return pnl, pushed_flags, seat_to_pos, rake_paid


def compute_stats(lst):
    n = len(lst)
    if n == 0: return {"mean":0,"std":0,"ci95":0,"total":0,"n":0}
    m = sum(lst)/n
    v = sum((x-m)**2 for x in lst)/n
    s = math.sqrt(v)
    return {"mean":m,"std":s,"ci95":1.96*s/math.sqrt(n),"total":sum(lst),"n":n}

def cumsum(lst):
    out, s = [], 0.0
    for v in lst: s += v; out.append(s)
    return out

def downsample(data, max_pts=4000):
    if len(data) <= max_pts: return data
    step = len(data) / max_pts
    return [round(data[int(i*step)], 2) for i in range(max_pts)]


# ─── HTML ───

def gen_html(results, path, info):
    html = f"""<!DOCTYPE html><html lang="ja"><head><meta charset="UTF-8">
<title>AoF Simulation</title>
<script src="https://cdn.jsdelivr.net/npm/chart.js@4.4.0/dist/chart.umd.min.js"></script>
<style>
*{{margin:0;padding:0;box-sizing:border-box}}
body{{font-family:'Segoe UI',sans-serif;background:linear-gradient(135deg,#0f0c29,#302b63,#24243e);color:#e0e0e0;padding:24px;min-height:100vh}}
h1{{text-align:center;font-size:2em;margin-bottom:6px;background:linear-gradient(90deg,#00d2ff,#3a7bd5);-webkit-background-clip:text;-webkit-text-fill-color:transparent}}
.sub{{text-align:center;color:#888;margin-bottom:30px;font-size:.85em}}
.grid{{display:grid;grid-template-columns:1fr 1fr;gap:18px;max-width:1500px;margin:0 auto 40px}}
.card{{background:rgba(255,255,255,.05);border:1px solid rgba(255,255,255,.08);border-radius:14px;padding:18px}}
.card.full{{grid-column:1/-1}}
.card h2{{font-size:1em;margin-bottom:10px;color:#00d2ff}}
.cc{{position:relative;width:100%;height:420px}}
.cc.tall{{height:550px}}
table{{width:100%;border-collapse:collapse;font-size:.82em}}
th,td{{padding:7px 10px;text-align:right;border-bottom:1px solid rgba(255,255,255,.06)}}
th{{color:#00d2ff;font-weight:600}}td:first-child,th:first-child{{text-align:left}}
.pos{{color:#4caf50}}.neg{{color:#ff5252}}
.sg{{display:grid;grid-template-columns:repeat(5,1fr);gap:10px;margin-bottom:18px}}
.sb{{background:rgba(255,255,255,.04);padding:12px;border-radius:10px;text-align:center}}
.sb .l{{font-size:.7em;color:#888;margin-bottom:3px}}.sb .v{{font-size:1.3em;font-weight:700}}
.stitle{{text-align:center;font-size:1.4em;margin:40px 0 16px;color:#fff}}
</style></head><body>
<h1>🃏 AoF 4P Simulation</h1>
<div class="sub">{info}</div>
"""
    for label, d in results.items():
        fs = d["fish"]; ga = d["gto_avg"]; edge = ga["mean"]-fs["mean"]
        fc = "pos" if fs["mean"]>=0 else "neg"; gc = "pos" if ga["mean"]>=0 else "neg"
        cid = label.replace(" ","").replace("+","p").replace("-","m").replace("(","").replace(")","").replace("%","")
        fvpip = d["fish_vpip"]; gvpip = d["gto_vpip"]

        html += f"""<div class="stitle">Fish = {label}</div>
<div class="sg">
  <div class="sb"><div class="l">Fish EV</div><div class="v {fc}">{fs['mean']*100:+.2f}</div><div class="l">bb/100</div></div>
  <div class="sb"><div class="l">GTO Avg EV</div><div class="v {gc}">{ga['mean']*100:+.2f}</div><div class="l">bb/100</div></div>
  <div class="sb"><div class="l">Edge vs Fish</div><div class="v pos">{edge*100:+.2f}</div><div class="l">bb/100</div></div>
  <div class="sb"><div class="l">Fish VPIP</div><div class="v">{fvpip:.1f}%</div><div class="l">vs GTO {gvpip:.1f}%</div></div>
  <div class="sb"><div class="l">Total Hands</div><div class="v">{fs['n']//1000}K</div><div class="l">&nbsp;</div></div>
</div>
<div class="grid">
  <div class="card full"><h2>📈 Cumulative PnL (BB)</h2><div class="cc tall"><canvas id="cum_{cid}"></canvas></div></div>
  <div class="card"><h2>📊 EV by Relative Position (bb/100)</h2><div class="cc"><canvas id="rel_{cid}"></canvas></div></div>
  <div class="card"><h2>📊 EV by Absolute Position (bb/100)</h2><div class="cc"><canvas id="abs_{cid}"></canvas></div></div>
  <div class="card full"><h2>📋 Results</h2><table>
    <tr><th>Player</th><th>EV (bb/100)</th><th>95% CI</th><th>StdDev</th><th>VPIP</th><th>Rake/100h</th><th>Total BB</th><th>Hands</th></tr>
"""
        html += _row("🐟 Fish", fs, fvpip, d["fish_rake"])
        for k in ["GTO-A (Right)", "GTO-B (Across)", "GTO-C (Left)"]:
            html += _row(k, d["gto_rel"][k], gvpip, d["gto_rel_rake"][k])
        gar = d["gto_avg_rake"]
        html += f'<tr style="border-top:2px solid #00d2ff"><td><b>GTO Avg</b></td><td class="{gc}"><b>{ga["mean"]*100:+.2f}</b></td><td>±{ga["ci95"]*100:.2f}</td><td>{ga["std"]:.3f}</td><td>{gvpip:.1f}%</td><td>{gar["mean"]*100:.2f}</td><td class="{gc}"><b>{ga["total"]:+,.0f}</b></td><td>{ga["n"]:,}</td></tr>\n'
        html += '<tr><td colspan="8" style="color:#888;padding-top:10px">Absolute Position (all players)</td></tr>\n'
        for p in POSITIONS:
            s = d["abs"][p]
            r = d["abs_rake"][p]
            c = "pos" if s["mean"]>=0 else "neg"
            html += f'<tr><td>{p}</td><td class="{c}">{s["mean"]*100:+.2f}</td><td>±{s["ci95"]*100:.2f}</td><td>{s["std"]:.3f}</td><td></td><td>{r["mean"]*100:.2f}</td><td class="{c}">{s["total"]:+,.0f}</td><td>{s["n"]:,}</td></tr>\n'
        rake_total = -(fs["total"] + sum(d["gto_rel"][k]["total"] for k in d["gto_rel"]))
        html += f'<tr style="border-top:2px solid #555"><td>💰 Total Rake</td><td>{rake_total/fs["n"]*100:.2f}</td><td></td><td></td><td></td><td></td><td>{rake_total:+,.0f}</td><td></td></tr>\n'
        html += "</table></div></div>\n"

        # Chart JS
        fc_d = json.dumps(d["cum"]["fish"]); ga_d = json.dumps(d["cum"]["gto_a"]); gb_d = json.dumps(d["cum"]["gto_b"]); gc_d = json.dumps(d["cum"]["gto_c"])
        n_pts = d["cum"]["n_pts"]; total_h = d["cum"]["total_hands"]
        rl = json.dumps(["Fish","GTO-A\n(Right)","GTO-B\n(Across)","GTO-C\n(Left)"])
        re = json.dumps([round(fs["mean"]*100,2)] + [round(d["gto_rel"][k]["mean"]*100,2) for k in ["GTO-A (Right)","GTO-B (Across)","GTO-C (Left)"]])
        al = json.dumps(POSITIONS); ae = json.dumps([round(d["abs"][p]["mean"]*100,2) for p in POSITIONS])

        html += f"""<script>(function(){{
const fc={fc_d},ga={ga_d},gb={gb_d},gc={gc_d};
const xl=Array.from({{length:fc.length}},(_,i)=>Math.round(i*{total_h}/{n_pts}));
new Chart(document.getElementById('cum_{cid}'),{{type:'line',data:{{labels:xl,datasets:[
  {{label:'Fish',data:fc,borderColor:'#ff5252',borderWidth:2,pointRadius:0,fill:false}},
  {{label:'GTO-A (Right)',data:ga,borderColor:'#4caf50',borderWidth:1.5,pointRadius:0,fill:false}},
  {{label:'GTO-B (Across)',data:gb,borderColor:'#2196f3',borderWidth:1.5,pointRadius:0,fill:false}},
  {{label:'GTO-C (Left)',data:gc,borderColor:'#ff9800',borderWidth:1.5,pointRadius:0,fill:false}},
]}},options:{{responsive:true,maintainAspectRatio:false,
  plugins:{{legend:{{labels:{{color:'#ccc',font:{{size:12}}}}}}}},
  scales:{{
    x:{{title:{{display:true,text:'Hands',color:'#888'}},ticks:{{color:'#666',maxTicksLimit:10}},grid:{{color:'rgba(255,255,255,.04)'}}}},
    y:{{title:{{display:true,text:'BB',color:'#888'}},ticks:{{color:'#666'}},grid:{{color:'rgba(255,255,255,.04)'}}}}
  }}
}}}});
const rl={rl},re={re};
new Chart(document.getElementById('rel_{cid}'),{{type:'bar',data:{{labels:rl,datasets:[{{data:re,backgroundColor:re.map(v=>v>=0?'#4caf50':'#ff5252'),borderWidth:0}}]}},
  options:{{responsive:true,maintainAspectRatio:false,plugins:{{legend:{{display:false}}}},scales:{{x:{{ticks:{{color:'#ccc'}},grid:{{display:false}}}},y:{{title:{{display:true,text:'bb/100',color:'#888'}},ticks:{{color:'#666'}},grid:{{color:'rgba(255,255,255,.04)'}}}}}}}}
}});
const al={al},ae={ae};
new Chart(document.getElementById('abs_{cid}'),{{type:'bar',data:{{labels:al,datasets:[{{data:ae,backgroundColor:ae.map(v=>v>=0?'#00d2ff':'#ff9800'),borderWidth:0}}]}},
  options:{{responsive:true,maintainAspectRatio:false,plugins:{{legend:{{display:false}}}},scales:{{x:{{ticks:{{color:'#ccc'}},grid:{{display:false}}}},y:{{title:{{display:true,text:'bb/100',color:'#888'}},ticks:{{color:'#666'}},grid:{{color:'rgba(255,255,255,.04)'}}}}}}}}
}});
}})();</script>
"""
    html += "</body></html>"
    with open(path, "w", encoding="utf-8") as f:
        f.write(html)

def _row(name, s, vpip, rake_s):
    c = "pos" if s["mean"]>=0 else "neg"
    return f'<tr><td>{name}</td><td class="{c}">{s["mean"]*100:+.2f}</td><td>±{s["ci95"]*100:.2f}</td><td>{s["std"]:.3f}</td><td>{vpip:.1f}%</td><td>{rake_s["mean"]*100:.2f}</td><td class="{c}">{s["total"]:+,.0f}</td><td>{s["n"]:,}</td></tr>\n'

# ─── Mixed simulation ───

def run_mixed_sim(args, seat_ranges_list, seat_labels, devs, total):
    """Simulate with per-seat deviations."""
    SEAT_NAMES = []
    for i, d in enumerate(devs):
        if abs(d) < 0.001:
            SEAT_NAMES.append(f"GTO (Seat {i})")
        else:
            SEAT_NAMES.append(f"{d*100:+.0f}% (Seat {i})")

    seat_pnl = {i: [] for i in range(4)}
    seat_rake = {i: [] for i in range(4)}
    seat_pushes = [0] * 4
    seat_hands = [0] * 4
    abs_pnl = {pos: [] for pos in POSITIONS}
    abs_rake = {pos: [] for pos in POSITIONS}

    for set_id in range(args.sets):
        random.seed(42 + set_id * 10000)
        t0 = time.time()

        for hi in range(args.hands):
            pnl, pushed, seat_to_pos, rake_paid = simulate_hand(seat_ranges_list, hi)

            for s in range(4):
                seat_pnl[s].append(pnl[s])
                seat_rake[s].append(rake_paid[s])
                seat_hands[s] += 1
                if pushed[s]: seat_pushes[s] += 1
                abs_pnl[seat_to_pos[s]].append(pnl[s])
                abs_rake[seat_to_pos[s]].append(rake_paid[s])

            if (hi + 1) % 20000 == 0:
                el = time.time() - t0
                print(f"\r  Set {set_id+1}: {hi+1:,}/{args.hands:,} ({(hi+1)/el:.0f} h/s)", end="", flush=True)

        el = time.time() - t0
        print(f"\r  Set {set_id+1}: {args.hands:,} in {el:.1f}s ({args.hands/el:.0f} h/s)         ")

    # Stats
    seat_stats = {i: compute_stats(seat_pnl[i]) for i in range(4)}
    seat_rake_stats = {i: compute_stats(seat_rake[i]) for i in range(4)}
    seat_vpip = [seat_pushes[i] / seat_hands[i] * 100 for i in range(4)]
    abs_stats = {p: compute_stats(v) for p, v in abs_pnl.items()}
    abs_rake_stats = {p: compute_stats(v) for p, v in abs_rake.items()}

    # Print
    print()
    for i in range(4):
        s = seat_stats[i]
        print(f"  {SEAT_NAMES[i]:20s}  EV={s['mean']*100:+.2f} bb/100  VPIP={seat_vpip[i]:.1f}%")
    print(f"  Position: " + "  ".join(f"{p}={abs_stats[p]['mean']*100:+.1f}" for p in POSITIONS))

    # Cumulative data
    cums = {i: downsample(cumsum(seat_pnl[i])) for i in range(4)}
    n_pts = len(cums[0])
    total_h = seat_stats[0]["n"]

    # Build label
    label = "Mix: " + ", ".join(f"{d*100:+.0f}%" if abs(d) > 0.001 else "GTO" for d in devs)

    all_results = {label: {
        "seat_stats": seat_stats, "seat_rake": seat_rake_stats,
        "seat_vpip": seat_vpip, "seat_names": SEAT_NAMES,
        "abs": abs_stats, "abs_rake": abs_rake_stats,
        "cum": {i: cums[i] for i in range(4)},
        "n_pts": n_pts, "total_hands": total_h,
    }}

    # Generate HTML
    info = f"{total:,} hands ({args.hands:,}×{args.sets}) | {STACK}BB | Rake {RAKE_PCT*100:.0f}% cap {RAKE_CAP:.0f}BB | Mix mode"
    gen_mixed_html(all_results, info, Path("d:/aof_bot/sim_results.html"))


def gen_mixed_html(results, info, path):
    colors = ["#ff4d6a", "#00d2ff", "#00ff88", "#ffaa00"]
    html = f"""<!DOCTYPE html><html><head><meta charset="utf-8"><title>AoF Mix Simulation</title>
<script src="https://cdn.jsdelivr.net/npm/chart.js"></script>
<style>
body{{margin:0;padding:20px 40px;background:#0d1117;color:#e6edf3;font-family:'Segoe UI',sans-serif}}
h1{{text-align:center;color:#fff;font-size:2em;margin:10px 0}}
.sub{{text-align:center;color:#888;margin-bottom:20px}}
.grid{{display:grid;grid-template-columns:1fr 1fr;gap:14px;margin-bottom:18px}}
.card{{background:#161b22;padding:16px;border-radius:12px;border:1px solid #30363d}}
.card.full{{grid-column:span 2}}
h2{{color:#58a6ff;font-size:1em;margin:0 0 10px}}
.cc{{position:relative;height:280px}}.cc.tall{{height:420px}}
canvas{{width:100%!important;height:100%!important}}
table{{width:100%;border-collapse:collapse}}th,td{{padding:6px 10px;text-align:right}}
th{{color:#58a6ff;border-bottom:2px solid #30363d}}td{{border-bottom:1px solid #21262d}}
td:first-child,th:first-child{{text-align:left}}.pos{{color:#3fb950}}.neg{{color:#ff4d6a}}
.sg{{display:grid;grid-template-columns:repeat(5,1fr);gap:10px;margin-bottom:18px}}
.sb{{background:rgba(255,255,255,.04);padding:12px;border-radius:10px;text-align:center}}
.sb .l{{font-size:.7em;color:#888;margin-bottom:3px}}.sb .v{{font-size:1.3em;font-weight:700}}
.stitle{{text-align:center;font-size:1.4em;margin:40px 0 16px;color:#fff}}
</style></head><body>
<h1>🃏 AoF 4P Mix Simulation</h1>
<div class="sub">{info}</div>
"""
    for label, d in results.items():
        sn = d["seat_names"]
        ss = d["seat_stats"]
        sr = d["seat_rake"]
        sv = d["seat_vpip"]

        html += f"""<div class="stitle">{label}</div>
<div class="sg">"""
        for i in range(4):
            c = "pos" if ss[i]["mean"] >= 0 else "neg"
            html += f'<div class="sb"><div class="l">{sn[i]}</div><div class="v {c}">{ss[i]["mean"]*100:+.2f}</div><div class="l">bb/100</div></div>\n'
        html += f'<div class="sb"><div class="l">Total Hands</div><div class="v">{ss[0]["n"]//1000}K</div></div></div>\n'

        cid = "mix"
        html += f"""<div class="grid">
  <div class="card full"><h2>📈 Cumulative PnL (BB)</h2><div class="cc tall"><canvas id="cum_{cid}"></canvas></div></div>
  <div class="card"><h2>📊 EV by Seat (bb/100)</h2><div class="cc"><canvas id="seat_{cid}"></canvas></div></div>
  <div class="card"><h2>📊 EV by Absolute Position (bb/100)</h2><div class="cc"><canvas id="abs_{cid}"></canvas></div></div>
  <div class="card full"><h2>📋 Results</h2><table>
    <tr><th>Player</th><th>EV (bb/100)</th><th>95% CI</th><th>StdDev</th><th>VPIP</th><th>Rake/100h</th><th>Total BB</th><th>Hands</th></tr>
"""
        for i in range(4):
            c = "pos" if ss[i]["mean"] >= 0 else "neg"
            html += f'<tr><td>{sn[i]}</td><td class="{c}">{ss[i]["mean"]*100:+.2f}</td><td>±{ss[i]["ci95"]*100:.2f}</td><td>{ss[i]["std"]:.3f}</td><td>{sv[i]:.1f}%</td><td>{sr[i]["mean"]*100:.2f}</td><td class="{c}">{ss[i]["total"]:+,.0f}</td><td>{ss[i]["n"]:,}</td></tr>\n'

        html += f'<tr><td colspan="8" style="color:#888;padding-top:10px">Absolute Position (all players)</td></tr>\n'
        for p in POSITIONS:
            s = d["abs"][p]; r = d["abs_rake"][p]
            c = "pos" if s["mean"] >= 0 else "neg"
            html += f'<tr><td>{p}</td><td class="{c}">{s["mean"]*100:+.2f}</td><td>±{s["ci95"]*100:.2f}</td><td>{s["std"]:.3f}</td><td></td><td>{r["mean"]*100:.2f}</td><td class="{c}">{s["total"]:+,.0f}</td><td>{s["n"]:,}</td></tr>\n'

        rake_total = -sum(ss[i]["total"] for i in range(4))
        html += f'<tr style="border-top:2px solid #555"><td>💰 Total Rake</td><td>{rake_total/ss[0]["n"]*100:.2f}</td><td></td><td></td><td></td><td></td><td>{rake_total:+,.0f}</td><td></td></tr>\n'
        html += "</table></div></div>\n"

        # Charts
        cum_data = [json.dumps(d["cum"][i]) for i in range(4)]
        n_pts = d["n_pts"]; total_h = d["total_hands"]
        seat_ev = json.dumps([round(ss[i]["mean"]*100, 2) for i in range(4)])
        seat_lb = json.dumps([sn[i] for i in range(4)])
        al = json.dumps(POSITIONS); ae = json.dumps([round(d["abs"][p]["mean"]*100,2) for p in POSITIONS])

        html += f"""<script>(function(){{
const d0={cum_data[0]},d1={cum_data[1]},d2={cum_data[2]},d3={cum_data[3]};
const n={n_pts},th={total_h};
const labels=Array.from({{length:n}},(_, i)=>Math.round(i*th/n));
new Chart(document.getElementById('cum_{cid}'),{{type:'line',data:{{labels:labels,datasets:[
  {{label:'{sn[0]}',data:d0,borderColor:'{colors[0]}',borderWidth:1.5,pointRadius:0,fill:false}},
  {{label:'{sn[1]}',data:d1,borderColor:'{colors[1]}',borderWidth:1.5,pointRadius:0,fill:false}},
  {{label:'{sn[2]}',data:d2,borderColor:'{colors[2]}',borderWidth:1.5,pointRadius:0,fill:false}},
  {{label:'{sn[3]}',data:d3,borderColor:'{colors[3]}',borderWidth:1.5,pointRadius:0,fill:false}}
]}},options:{{responsive:true,maintainAspectRatio:false,plugins:{{legend:{{labels:{{color:'#ccc'}}}}}},scales:{{x:{{type:'linear',title:{{display:true,text:'Hands',color:'#888'}},ticks:{{color:'#888',callback:v=>v>=1e6?(v/1e6)+'M':v>=1e3?(v/1e3)+'K':v}}}},y:{{title:{{display:true,text:'Cumulative BB',color:'#888'}},ticks:{{color:'#888'}}}}}}}}}});
new Chart(document.getElementById('seat_{cid}'),{{type:'bar',data:{{labels:{seat_lb},datasets:[{{data:{seat_ev},backgroundColor:d=>{colors}.map((c,i)=>c)}}]}},options:{{responsive:true,maintainAspectRatio:false,plugins:{{legend:{{display:false}}}},scales:{{x:{{ticks:{{color:'#ccc'}}}},y:{{title:{{display:true,text:'bb/100',color:'#888'}},ticks:{{color:'#888'}}}}}}}}}});
new Chart(document.getElementById('abs_{cid}'),{{type:'bar',data:{{labels:{al},datasets:[{{data:{ae},backgroundColor:['#00d2ff','#00d2ff','#ffaa00','#ffaa00']}}]}},options:{{responsive:true,maintainAspectRatio:false,plugins:{{legend:{{display:false}}}},scales:{{x:{{ticks:{{color:'#ccc'}}}},y:{{title:{{display:true,text:'bb/100',color:'#888'}},ticks:{{color:'#888'}}}}}}}}}});
}})();</script>
"""
    html += "</body></html>"
    with open(path, "w", encoding="utf-8") as f:
        f.write(html)
    print(f"\n{'='*70}\n  ✅ {path}\n{'='*70}")


# ─── Main ───

def main():
    import argparse
    p = argparse.ArgumentParser()
    p.add_argument("--hands", type=int, default=100000)
    p.add_argument("--sets", type=int, default=5)
    p.add_argument("--deviation", type=float, default=0.01)
    p.add_argument("--rake", type=float, default=0.03)
    p.add_argument("--rake-cap", type=float, default=3.0)
    p.add_argument("--mix", type=str, default=None,
                   help="Per-seat deviations, e.g. '1,3,3,0' for +1%%,+3%%,+3%%,GTO")
    args = p.parse_args()

    global RAKE_PCT, RAKE_CAP
    RAKE_PCT = args.rake; RAKE_CAP = args.rake_cap
    total = args.hands * args.sets

    gto_ranges = load_gto_ranges(4)

    if args.mix:
        # ── Mixed deviation mode ──
        devs = [float(x)/100 for x in args.mix.split(",")]
        assert len(devs) == 4, "--mix must have 4 comma-separated values"
        seat_labels = []
        seat_ranges_list = []
        for i, d in enumerate(devs):
            if abs(d) < 0.001:
                seat_labels.append(f"Seat{i}=GTO")
                seat_ranges_list.append(gto_ranges)
            else:
                seat_labels.append(f"Seat{i}={d*100:+.0f}%")
                seat_ranges_list.append({k: create_deviated_range(v, d) for k, v in gto_ranges.items()})

        print("="*70)
        print(f"  AoF 4P Simulator v3 — {total:,} hands ({args.hands:,} × {args.sets} sets)")
        print(f"  Stack={STACK}BB  Rake={RAKE_PCT*100:.1f}% cap {RAKE_CAP}BB")
        print(f"  Mix: {' | '.join(seat_labels)}")
        print(f"  Position rotates every hand")
        print("="*70)
        print(f"  {len(gto_ranges)} GTO charts loaded")

        run_mixed_sim(args, seat_ranges_list, seat_labels, devs, total)
        return

    # ── Original LOOSE/TIGHT mode ──
    print("="*70)
    print(f"  AoF 4P Simulator v3 — {total:,} hands ({args.hands:,} × {args.sets} sets)")
    print(f"  Stack={STACK}BB  Rake={RAKE_PCT*100:.1f}% cap {RAKE_CAP}BB  Dev=±{args.deviation*100:.1f}%")
    print(f"  Position rotates every hand")
    print("="*70)
    print(f"  {len(gto_ranges)} GTO charts loaded")

    fish_loose = {k: create_deviated_range(v, +args.deviation) for k, v in gto_ranges.items()}
    fish_tight = {k: create_deviated_range(v, -args.deviation) for k, v in gto_ranges.items()}

    all_results = {}

    for label, fish_ranges in [("LOOSE (+1%)", fish_loose), ("TIGHT (-1%)", fish_tight)]:
        print(f"\n{'='*70}\n  {label}\n{'='*70}")

        fish_pnl, fish_pushes, fish_hands_total = [], 0, 0
        fish_rake = []  # per-hand rake paid by fish
        gto_pnl = {v: [] for v in REL_LABELS.values()}
        gto_rake = {v: [] for v in REL_LABELS.values()}
        gto_pushes, gto_hands_total = 0, 0
        abs_pnl = {pos: [] for pos in POSITIONS}
        abs_rake = {pos: [] for pos in POSITIONS}

        for set_id in range(args.sets):
            random.seed(42 + set_id * 10000)
            t0 = time.time()

            for hi in range(args.hands):
                seat_r = [fish_ranges if s==0 else gto_ranges for s in range(4)]
                pnl, pushed, seat_to_pos, rake_paid = simulate_hand(seat_r, hi)

                # Fish = seat 0
                fish_pnl.append(pnl[0])
                fish_rake.append(rake_paid[0])
                fish_hands_total += 1
                if pushed[0]: fish_pushes += 1

                # GTO = seats 1,2,3
                for s in [1, 2, 3]:
                    rel = REL_LABELS[s]
                    gto_pnl[rel].append(pnl[s])
                    gto_rake[rel].append(rake_paid[s])
                    gto_hands_total += 1
                    if pushed[s]: gto_pushes += 1

                # Absolute position tracking
                for s in range(4):
                    abs_pnl[seat_to_pos[s]].append(pnl[s])
                    abs_rake[seat_to_pos[s]].append(rake_paid[s])

                if (hi + 1) % 20000 == 0:
                    el = time.time() - t0
                    print(f"\r  Set {set_id+1}: {hi+1:,}/{args.hands:,} ({(hi+1)/el:.0f} h/s)", end="", flush=True)

            el = time.time() - t0
            print(f"\r  Set {set_id+1}: {args.hands:,} in {el:.1f}s ({args.hands/el:.0f} h/s)         ")

        # Stats
        fish_stats = compute_stats(fish_pnl)
        fish_rake_stats = compute_stats(fish_rake)
        gto_rel_stats = {k: compute_stats(v) for k, v in gto_pnl.items()}
        gto_rel_rake = {k: compute_stats(v) for k, v in gto_rake.items()}
        all_gto, all_gto_rake = [], []
        for k in gto_pnl:
            all_gto.extend(gto_pnl[k])
            all_gto_rake.extend(gto_rake[k])
        gto_avg = compute_stats(all_gto)
        gto_avg_rake = compute_stats(all_gto_rake)
        abs_stats = {p: compute_stats(v) for p, v in abs_pnl.items()}
        abs_rake_stats = {p: compute_stats(v) for p, v in abs_rake.items()}

        fish_vpip = fish_pushes / fish_hands_total * 100 if fish_hands_total else 0
        gto_vpip = gto_pushes / gto_hands_total * 100 if gto_hands_total else 0

        fc = downsample(cumsum(fish_pnl))
        ga = downsample(cumsum(gto_pnl["GTO-A (Right)"]))
        gb = downsample(cumsum(gto_pnl["GTO-B (Across)"]))
        gc = downsample(cumsum(gto_pnl["GTO-C (Left)"]))
        n_pts = len(fc)
        total_h = fish_stats["n"]

        all_results[label] = {
            "fish": fish_stats, "gto_rel": gto_rel_stats, "gto_avg": gto_avg,
            "abs": abs_stats, "fish_vpip": fish_vpip, "gto_vpip": gto_vpip,
            "fish_rake": fish_rake_stats, "gto_rel_rake": gto_rel_rake,
            "gto_avg_rake": gto_avg_rake, "abs_rake": abs_rake_stats,
            "cum": {"fish": fc, "gto_a": ga, "gto_b": gb, "gto_c": gc, "n_pts": n_pts, "total_hands": total_h},
        }

        edge = gto_avg["mean"] - fish_stats["mean"]
        print(f"\n  Fish: {fish_stats['mean']*100:+.2f} bb/100 (VPIP {fish_vpip:.1f}%)")
        print(f"  GTO:  {gto_avg['mean']*100:+.2f} bb/100 (VPIP {gto_vpip:.1f}%)")
        print(f"  Edge: {edge*100:.2f} bb/100")
        for k, s in gto_rel_stats.items():
            print(f"    {k:<20} {s['mean']*100:+.2f} bb/100")
        print(f"  Position: ", end="")
        for pos in POSITIONS:
            print(f"{pos}={abs_stats[pos]['mean']*100:+.1f}  ", end="")
        print()

    out = str(Path("d:/aof_bot/sim_results.html"))
    info = f"{total:,} hands ({args.hands:,}×{args.sets}) | {STACK}BB | Rake {RAKE_PCT*100:.0f}% cap {RAKE_CAP:.0f}BB | ±{args.deviation*100:.0f}% | Rotating positions"
    gen_html(all_results, out, info)
    print(f"\n{'='*70}\n  ✅ {out}\n{'='*70}")

if __name__ == "__main__":
    main()
