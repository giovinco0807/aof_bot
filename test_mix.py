import json

path = 'd:/aof_bot/solver/data/charts_rb50/aof_4p_8bb.json'
try:
    with open(path, 'r', encoding='utf-8') as f:
        d = json.load(f)
        
    target = ['BB faces CO push, BTN+SB fold', 'BB faces BTN push after CO+SB fold', 'BB faces SB push after CO+BTN fold']
    
    for chart in d.get('charts', []):
        desc = chart.get('description', '')
        if desc in target:
            print(f"[{desc}]")
            entries = sorted(chart.get('entries', []), key=lambda x: -x['allin_freq'])
            for e in entries:
                f_val = e['allin_freq']
                if 0.0 < f_val < 0.999: # exclude strict 0% and 100%
                    print(f"  {e['hand']}: {f_val*100:.1f}%")
            print('-'*30)
except Exception as e:
    print(f"Error: {e}")
