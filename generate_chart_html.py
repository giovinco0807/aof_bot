import json
from pathlib import Path

RANKS = "AKQJT98765432"

def hand_name(r, c):
    if r == c: return RANKS[r] + RANKS[c]
    elif r < c: return RANKS[r] + RANKS[c] + "s"
    else: return RANKS[c] + RANKS[r] + "o"

def freq_color(freq):
    if freq >= 0.95: return "#2ecc71"   # green - always push
    elif freq >= 0.75: return "#82e0aa"
    elif freq >= 0.50: return "#f9e79f"  # yellow - mix
    elif freq >= 0.25: return "#f5b041"  # orange
    elif freq >= 0.05: return "#e74c3c"  # red - rare
    else: return "#2c3e50"               # dark - never

def generate_html(charts_path, output_path):
    data = json.load(open(charts_path))
    np = data.get("num_players", "?")
    stack = data.get("stack_bb", "?")
    
    html = f"""<!DOCTYPE html>
<html><head><meta charset="utf-8">
<title>GTO Range Chart - {np}p {stack}BB</title>
<style>
body {{ font-family: 'Segoe UI', Arial, sans-serif; background: #1a1a2e; color: #eee; padding: 20px; }}
h1 {{ color: #e94560; text-align: center; }}
h2 {{ color: #0f3460; background: #e94560; padding: 8px 16px; border-radius: 8px; display: inline-block; }}
.chart-container {{ display: flex; flex-wrap: wrap; gap: 30px; justify-content: center; }}
.chart-box {{ background: #16213e; border-radius: 12px; padding: 16px; box-shadow: 0 4px 15px rgba(0,0,0,0.4); }}
.chart-title {{ font-size: 14px; font-weight: bold; color: #e94560; margin-bottom: 8px; text-align: center; }}
.push-pct {{ font-size: 12px; color: #aaa; text-align: center; margin-bottom: 6px; }}
table {{ border-collapse: collapse; }}
td {{ width: 36px; height: 28px; text-align: center; font-size: 10px; font-weight: bold;
     border: 1px solid #0f3460; cursor: default; }}
td:hover {{ outline: 2px solid #fff; z-index: 1; position: relative; }}
.legend {{ display: flex; gap: 12px; justify-content: center; margin: 20px 0; flex-wrap: wrap; }}
.legend-item {{ display: flex; align-items: center; gap: 4px; font-size: 12px; }}
.legend-color {{ width: 20px; height: 14px; border-radius: 3px; }}
</style></head><body>
<h1>GTO Push Range - {np}人テーブル {stack}BB</h1>
<p style="text-align:center;color:#aaa;">Rake 1.0% / Cap 1.5BB (50% Rakeback) | 100M iterations</p>
<div class="legend">
  <div class="legend-item"><div class="legend-color" style="background:#2ecc71"></div>95-100%</div>
  <div class="legend-item"><div class="legend-color" style="background:#82e0aa"></div>75-95%</div>
  <div class="legend-item"><div class="legend-color" style="background:#f9e79f"></div>50-75%</div>
  <div class="legend-item"><div class="legend-color" style="background:#f5b041"></div>25-50%</div>
  <div class="legend-item"><div class="legend-color" style="background:#e74c3c"></div>5-25%</div>
  <div class="legend-item"><div class="legend-color" style="background:#2c3e50"></div>Fold</div>
</div>
<div class="chart-container">
"""
    
    for chart in data.get("charts", []):
        pos = chart.get("position", "?")
        desc = chart.get("description", "")
        entries = chart.get("entries", [])
        
        freq_map = {}
        for e in entries:
            freq_map[e["hand"]] = e["allin_freq"]
        
        # Calculate total push %
        total = 0
        for e in entries:
            name, freq = e["hand"], e["allin_freq"]
            if len(name) == 2: combos = 6
            elif name.endswith("s"): combos = 4
            elif name.endswith("o"): combos = 12
            else: combos = 16
            total += combos * freq
        pct = total / 1326 * 100
        
        html += f'<div class="chart-box">\n'
        html += f'<div class="chart-title">{pos}: {desc}</div>\n'
        html += f'<div class="push-pct">Push: {pct:.1f}%</div>\n'
        html += '<table>\n'
        
        for r in range(13):
            html += '<tr>'
            for c in range(13):
                name = hand_name(r, c)
                freq = freq_map.get(name, 0)
                bg = freq_color(freq)
                text_color = "#fff" if freq < 0.05 or freq >= 0.75 else "#000"
                pct_text = f"{freq*100:.0f}" if freq >= 0.01 else ""
                tooltip = f"{name}: {freq*100:.1f}%"
                html += f'<td style="background:{bg};color:{text_color}" title="{tooltip}">{name}<br><small>{pct_text}</small></td>'
            html += '</tr>\n'
        
        html += '</table></div>\n'
    
    html += '</div></body></html>'
    
    with open(output_path, "w", encoding="utf-8") as f:
        f.write(html)
    print(f"Chart saved to: {output_path}")

# Generate for all player counts
for np_label, fname in [("4p", "aof_4p_8bb.json"), ("3p", "aof_3p_8bb.json"), ("2p", "aof_2p_8bb.json")]:
    src = Path(f"d:/aof_bot/solver/data/charts_rb50/{fname}")
    if src.exists():
        out = Path(f"d:/aof_bot/solver/data/charts_rb50/chart_{np_label}_8bb.html")
        generate_html(str(src), str(out))
