"""Generate HTML range charts from AoF solver JSON output.

Usage:
    python gen_chart.py data/charts_rb50/aof_2p_8bb.json
    python gen_chart.py data/charts_rb50/aof_*.json
"""
import json, sys, glob
from pathlib import Path

RANKS = "AKQJT98765432"

def hand_grid():
    """Return 13x13 grid of hand names (suited upper-left, offsuit lower-left)."""
    grid = []
    for r, rank_r in enumerate(RANKS):
        row = []
        for c, rank_c in enumerate(RANKS):
            if r == c:
                row.append(rank_r + rank_c)
            elif r < c:
                row.append(rank_r + rank_c + "s")
            else:
                row.append(rank_c + rank_r + "o")
        grid.append(row)
    return grid

def freq_color(pct):
    if pct >= 95: return "#2ecc71", "#fff"
    if pct >= 75: return "#82e0aa", "#fff"
    if pct >= 50: return "#f9e79f", "#000"
    if pct >= 25: return "#f5b041", "#000"
    if pct >= 5:  return "#e74c3c", "#000"
    return "#2c3e50", "#fff"

def gen_chart_html(json_path):
    with open(json_path) as f:
        data = json.load(f)

    np = data["num_players"]
    stack = data["stack_bb"]
    structure = data.get("structure", "")
    charts = data["charts"]

    grid = hand_grid()
    title = f"GTO Push Range - {np}人テーブル {stack}BB"
    subtitle = f"Rake 1.0% / Cap 1.5BB (50% Rakeback) | 1B iterations"

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
<h1>{title}</h1>
<p style="text-align:center;color:#aaa;">{subtitle}</p>
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

    for chart in charts:
        pos = chart["position"]
        desc = chart["description"]
        entries = chart["entries"]

        freqs = {}
        for e in entries:
            freqs[e["hand"]] = e["allin_freq"] * 100

        push_count = sum(1 for v in freqs.values() if v >= 50)
        total_pct = sum(freqs.values()) / len(freqs) if freqs else 0

        html += f'<div class="chart-box">\n'
        html += f'<div class="chart-title">{pos}: {desc}</div>\n'
        html += f'<div class="push-pct">Push: {total_pct:.1f}%</div>\n'
        html += '<table>\n'

        for row in grid:
            html += '<tr>'
            for hand in row:
                pct = freqs.get(hand, 0)
                bg, fg = freq_color(pct)
                small = f"{pct:.0f}" if pct >= 1 else ""
                html += (f'<td style="background:{bg};color:{fg}" '
                         f'title="{hand}: {pct:.1f}%">{hand}<br>'
                         f'<small>{small}</small></td>')
            html += '</tr>\n'

        html += '</table></div>\n'

    html += '</div></body></html>'

    out_path = json_path.replace(".json", "_chart.html")
    with open(out_path, "w", encoding="utf-8") as f:
        f.write(html)
    print(f"  {Path(json_path).name} → {Path(out_path).name} ({len(charts)} charts)")
    return out_path

if __name__ == "__main__":
    if len(sys.argv) < 2:
        print("Usage: python gen_chart.py <json_file> ...")
        sys.exit(1)

    files = []
    for arg in sys.argv[1:]:
        files.extend(glob.glob(arg))

    if not files:
        print("No files found")
        sys.exit(1)

    print(f"Generating charts for {len(files)} files:")
    for f in files:
        gen_chart_html(f)
    print("Done!")
