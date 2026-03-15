import json

path = "d:/aof_bot/solver/data/charts_rb50/aof_4p_8bb.json"
d = json.load(open(path))

print("=" * 60)
print("  4人テーブル 8BB | Rake=1.0% Cap=1.5BB (50% RB)")
print("=" * 60)

for chart in d.get("charts", []):
    pos = chart.get("position", "?")
    desc = chart.get("description", "")
    entries = chart.get("entries", [])
    
    push = [(e["hand"], e["allin_freq"]) for e in entries if e["allin_freq"] > 0.5]
    mix = [(e["hand"], e["allin_freq"]) for e in entries if 0.05 < e["allin_freq"] <= 0.5]
    push.sort(key=lambda x: -x[1])
    mix.sort(key=lambda x: -x[1])
    
    total = 0
    for e in entries:
        name, freq = e["hand"], e["allin_freq"]
        if len(name) == 2: combos = 6
        elif name.endswith("s"): combos = 4
        elif name.endswith("o"): combos = 12
        else: combos = 16
        total += combos * freq
    pct = total / 1326 * 100
    
    print(f"\n--- {pos}: {desc} (Push%: {pct:.1f}%) ---")
    
    line = []
    for h, v in push:
        line.append(f"{h}" if v > 0.99 else f"{h}({v*100:.0f}%)")
    print(f"  Push: {', '.join(line)}")
    
    if mix:
        mline = [f"{h}({v*100:.0f}%)" for h, v in mix]
        print(f"  Mix:  {', '.join(mline)}")
