import json

def analyze_mix_hands(path, target_descs):
    try:
        with open(path, 'r', encoding='utf-8') as f:
            d = json.load(f)
        
        for chart in d.get('charts', []):
            desc = chart.get('description', '')
            if desc in target_descs:
                print(f"[{desc}]")
                entries = sorted(chart.get('entries', []), key=lambda x: -x['allin_freq'])
                
                total_mix_combos = 0
                for e in entries:
                    f_val = e['allin_freq']
                    if 0.0 < f_val < 0.999: # exclude strict 0% and 100%
                        hand = e['hand']
                        if len(hand) == 2: combos = 6
                        elif hand.endswith('s'): combos = 4
                        elif hand.endswith('o'): combos = 12
                        else: combos = 16
                        
                        reduction = combos * f_val
                        total_mix_combos += reduction
                        print(f"  {hand}: {f_val*100:.1f}% (reduction: {reduction:.2f} combos)")
                
                pct_reduction = total_mix_combos / 1326.0 * 100
                print(f"=> Total reduction: {total_mix_combos:.2f} combos ({pct_reduction:.3f}%)\n")
    except Exception as e:
        print(f"Error ({path}): {e}")

print("=== 3P TABLE ===")
analyze_mix_hands('d:/aof_bot/solver/data/charts_rb50/aof_3p_8bb.json', 
                  ['BB faces BTN push, SB fold', 'BB faces SB push after BTN fold', 'BB faces BTN push', 'BB faces SB push']) 

print("=== 2P TABLE ===")
analyze_mix_hands('d:/aof_bot/solver/data/charts_rb50/aof_2p_8bb.json', 
                  ['BB faces SB push'])
