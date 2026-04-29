import json
d = json.load(open('d:/aof_bot/solver/data/charts_rb50/aof_4p_8bb.json'))
for c in d['charts']:
    print(c['position'] + ' -> ' + repr(c['prior_actions']))
