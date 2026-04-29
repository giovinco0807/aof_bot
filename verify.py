def get_combos(hand_str):
    if len(hand_str) == 2: return 6
    if hand_str.endswith('s'): return 4
    if hand_str.endswith('o'): return 12
    return 0

raw_str = "AA,KK,QQ,JJ,TT,AKs,AQs,AJs,AKo,99,ATs,AQo,KQs,88,AJo,KJs,KTs,ATo,QJs,77,KQo,QTs,A9s,KJo,JTs,66,A8s,KTo,A9o,QTo,A7s,J9s,Q9s,K9s,55,A5s,A8o,A6s,JTo,A4s,K9o,44,A7o,A3s,T9s,Q9o,Q8s,K8s,A5o,J9o,A2s,A6o,K7s,A4o,33,J8s,K8o,98s,T8s,K6s,Q8o,A3o,K5s,A2o,K7o,Q7s,K4s,J8o,22,T9o,T7s,98o,K3s,K6o,Q6s,K2s,K5o,J7s,87s,Q7o,Q5s,K4o,97s,T8o,T6s,Q4s,K3o,J7o,Q6o,87o,K2o,Q3s,97o,Q2s,T7o,J6s,Q5o,86s,76s,J5s,96s,T6o,Q4o,J6o,Q3o,86o,J4s,76o,T5s,96o,J5o,Q2o,75s,85s,J3s,T5o,T4s,95s,J2s,J4o,75o,65s,85o,T3s,95o,J3o,T4o,84s,T2s,65o,74s,94s,J2o,T3o,54s,84o,94o,T2o,74o,64s,93s,83s,54o,93o,73s,64o,83o,92s,53s,92o,63s,73o,82s,43s,53o,82o,72s,63o,43o,62s,52s,72o,62o,42s,52o,32s,42o,32o"
hands = raw_str.split(",")

total_combos = sum(get_combos(h) for h in hands)
print(f"Total Combos extracted: {total_combos}")
print(f"Total Hands: {len(hands)}")
print(f"String length: {len(raw_str)}")
