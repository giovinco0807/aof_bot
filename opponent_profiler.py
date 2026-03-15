import sqlite3
import json
import os
import glob
from collections import defaultdict
from pathlib import Path

# Paths
DB_PATH = "D:/aof_bot/solver/data/history/hands.db"
CHARTS_DIR = "D:/aof_bot/solver/data/charts_rakeback_100m"
OUTPUT_PATH = "D:/aof_bot/solver/data/opponent_profiles.json"
POSITION_NAMES = {2: ["SB", "BB"], 3: ["BTN", "SB", "BB"], 4: ["CO", "BTN", "SB", "BB"]}

# Deviation thresholds for labels
LOOSE_THRESHOLD = 0.05   # Re-adjusted to 5% actual difference
PASSIVE_THRESHOLD = -0.05

def get_combos(hand):
    if len(hand) == 2:
        return 6
    if hand[2] == 's':
        return 4
    if hand[2] == 'o':
        return 12
    return 1

def load_gto_charts():
    """Load the 100M GTO charts into memory."""
    print(f"Loading GTO charts from {CHARTS_DIR}...")
    gto_data = {}
    for filename in glob.glob(os.path.join(CHARTS_DIR, "*.json")):
        with open(filename, 'r', encoding='utf-8') as f:
            data = json.load(f)
            num_players = data["num_players"]
            stack_bb = round(data["stack_bb"])
            
            for chart in data["charts"]:
                pos = chart["position"]
                prior = chart["prior_actions"]
                
                # Calculate weighted average allin freq for this exact situation across all hands
                total_weighted_freq = 0.0
                total_combos = 0
                for entry in chart["entries"]:
                    combos = get_combos(entry["hand"])
                    total_weighted_freq += entry["allin_freq"] * combos
                    total_combos += combos
                    
                avg_freq = total_weighted_freq / total_combos if total_combos > 0 else 0.0
                
                key = f"{num_players}p_{stack_bb}bb_{pos}_{prior}"
                gto_data[key] = avg_freq
    print(f"Loaded {len(gto_data)} situational GTO benchmarks.")
    return gto_data

def get_closest_stack(stack):
    """Find the closest standard stack size for matching."""
    standard_stacks = [1, 2, 3, 4, 5, 6, 7, 8, 10, 12, 15, 20]
    return min(standard_stacks, key=lambda x: abs(x - stack))

def analyze_hands(gto_data):
    """Analyze DB hands to build situational profiles."""
    if not os.path.exists(DB_PATH):
        print(f"DB not found at {DB_PATH}")
        return
        
    conn = sqlite3.connect(DB_PATH)
    cursor = conn.cursor()
    cursor.execute("SELECT num_players, stack_bb, player_ids, actions FROM hands")
    rows = cursor.fetchall()
    conn.close()
    
    # Store actions per situation: player -> situation_key -> [action1, action2]
    # actions: 1 for AllIn, 0 for Fold
    player_history = defaultdict(lambda: defaultdict(list))
    
    print(f"Processing {len(rows)} hands for profiling...")
    for row in rows:
        num_players, stack_bb, pids_json, actions = row
        pids = json.loads(pids_json)
        closest_stack = get_closest_stack(stack_bb)
        
        pos_names = POSITION_NAMES.get(num_players, ["0", "1", "2", "3"])
        
        for pos, pid in enumerate(pids):
            if not pid or pos >= len(actions):
                continue
                
            action_char = actions[pos]
            is_allin = 1 if action_char == 'A' else 0
            prior_actions = actions[:pos]
            pos_name = pos_names[pos] if pos < len(pos_names) else str(pos)
            
            situation_key = f"{num_players}p_{closest_stack}bb_{pos_name}_{prior_actions}"
            player_history[pid][situation_key].append(is_allin)

    print(f"Generating profiles for {len(player_history)} players...")
    profiles = {}
    
    for pid, situations in player_history.items():
        pid_profile = {}
        for sit_key, actions in situations.items():
            if len(actions) < 5:
                continue # Ignore spots with very low sample size
                
            observed_freq = sum(actions) / len(actions)
            gto_freq = gto_data.get(sit_key, 0.5) # Fallback to 50%
            
            deviation = observed_freq - gto_freq
            
            label = "NORMAL"
            if deviation > LOOSE_THRESHOLD:
                label = "LOOSE"
            elif deviation < PASSIVE_THRESHOLD:
                label = "PASSIVE"
                
            pid_profile[sit_key] = {
                "hands": len(actions),
                "observed": observed_freq,
                "gto": gto_freq,
                "deviation": deviation,
                "label": label
            }
            
        if pid_profile:
            profiles[pid] = pid_profile
            
    with open(OUTPUT_PATH, 'w', encoding='utf-8') as f:
        json.dump(profiles, f, indent=2)
        
    print(f"Successfully generated {OUTPUT_PATH}")

if __name__ == "__main__":
    gto_data = load_gto_charts()
    analyze_hands(gto_data)
