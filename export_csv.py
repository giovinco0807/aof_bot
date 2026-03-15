import sqlite3
import csv
from pathlib import Path
from treys import Card, Evaluator, Deck
import time

db_path = Path("d:/aof_bot/automation/data/hands.db")
csv_path = Path("d:/aof_bot/automation/data/player_stats_aof_10_20.csv")

# 0. Set up Treys evaluator
evaluator = Evaluator()

def calculate_equity(hole_cards_list, board_cards, iters=2000):
    """Calculate equity for multiple hands using Monte Carlo simulation."""
    if len(hole_cards_list) < 2:
        return [1.0] * len(hole_cards_list)
        
    parsed_hands = []
    for hc in hole_cards_list:
        parsed_hands.append([Card.new(hc[0:2]), Card.new(hc[2:4])])
        
    parsed_board = []
    for i in range(0, len(board_cards), 2):
        if len(board_cards) - i >= 2:
            parsed_board.append(Card.new(board_cards[i:i+2]))

    wins = [0.0] * len(parsed_hands)
    
    for _ in range(iters):
        deck = Deck()
        # Remove known cards
        for hand in parsed_hands:
            for card in hand:
                if card in deck.cards: deck.cards.remove(card)
        for card in parsed_board:
            if card in deck.cards: deck.cards.remove(card)
            
        # Draw remaining board cards
        needed = 5 - len(parsed_board)
        current_board = list(parsed_board)
        if needed > 0:
            current_board.extend(deck.draw(needed))
            
        # Evaluate
        scores = [evaluator.evaluate(current_board, hand) for hand in parsed_hands]
        best_score = min(scores)  # Lower is better in Treys
        
        # Distribute win (split pot if multiple best scores)
        winners = [i for i, score in enumerate(scores) if score == best_score]
        win_share = 1.0 / len(winners)
        for w in winners:
            wins[w] += win_share
            
    return [w / iters for w in wins]


conn = sqlite3.connect(str(db_path))

# 1. Calculate per-player rake and EV
player_rake_totals = {}
player_ev_diffs = {}  # pid -> total EV diff chips

cursor = conn.execute("""
    SELECT h.id, h.rake_chips, h.pot_chips, h.board, hp.player_id, hp.profit_chips, hp.cards
    FROM hands h
    JOIN hand_players hp ON h.id = hp.hand_id
""")
hands_data = {}
for hand_id, rake, pot, board, pid, profit, cards in cursor.fetchall():
    if hand_id not in hands_data:
        hands_data[hand_id] = {"rake": rake, "pot": pot, "board": board, "players": []}
    hands_data[hand_id]["players"].append({"pid": pid, "profit": profit, "cards": cards})

print("Calculating EV and Rake for hands...")
processed_hands = 0
total_hands = len(hands_data)

for hand_id, h in hands_data.items():
    # Rake calculation (only positive profits pay rake)
    total_positive_profit = sum(p["profit"] for p in h["players"] if p["profit"] > 0)
    if total_positive_profit > 0 and h["rake"] > 0:
        for p in h["players"]:
            if p["profit"] > 0:
                share = h["rake"] * (p["profit"] / total_positive_profit)
                player_rake_totals[p["pid"]] = player_rake_totals.get(p["pid"], 0.0) + share

    # EV calculation (All-In Showdowns)
    # Filter for players who had hole cards and put money in (we approximate all-ins by having cards in AoF)
    showdown_players = [p for p in h["players"] if p["cards"]]
    if len(showdown_players) >= 2:
        hole_cards = [p["cards"] for p in showdown_players]
        # Calculate true pre-flop expected equity
        equities = calculate_equity(hole_cards, "", iters=200)
        # Calculate exactly who won the actual 5-card runout (1 iter is enough since no cards are needed)
        actual_shares = calculate_equity(hole_cards, h["board"] or "", iters=1)
        
        # Net pot = total pot - rake
        net_pot = h["pot"] - h["rake"]
        
        for i, p in enumerate(showdown_players):
            expected_win = net_pot * equities[i]
            actual_win = net_pot * actual_shares[i]
            
            ev_diff = expected_win - actual_win
            player_ev_diffs[p["pid"]] = player_ev_diffs.get(p["pid"], 0.0) + ev_diff
            
    processed_hands += 1
    if processed_hands % 500 == 0:
        print(f"Processed {processed_hands} / {total_hands} hands...")

# 2. Fetch main player stats
cursor = conn.execute("""
    SELECT player_id, hands_seen, hands_pushed, total_profit_chips, total_profit_bb, last_seen
    FROM player_stats
    ORDER BY hands_seen DESC
""")
rows = cursor.fetchall()
conn.close()

# User's PPPoker IDs (add here when known)
HERO_IDS = set()

with open(csv_path, "w", newline="", encoding="utf-8-sig") as f:
    writer = csv.writer(f)
    writer.writerow([
        "Player ID", "Hands Seen", "Hands Pushed", "Push %", "Profit (Chips)", 
        "Profit (BB)", "BB/100", "Rake Paid (Chips)", "Rakeback (Chips)", 
        "True Profit (Chips)", "True EV Profit (Chips)",
        "EV Profit (Chips)", "EV Profit (BB)", "EV BB/100", "Last Seen"
    ])
    
    total_rakeback_chips = 0.0
    for row in rows:
        pid, hands, pushed, profit_c, profit_bb, last_seen = row
        push_pct = f"{pushed/hands*100:.1f}%" if hands > 0 else "0.0%"
        profit_c = (profit_c or 0) / 100.0  # Convert from sub-chips to normal chips
        profit_bb = profit_bb or 0
        bb_per_100 = f"{profit_bb/hands*100:.1f}" if hands > 0 else "0.0"
        
        rake_chips = player_rake_totals.get(pid, 0.0) / 100.0
        
        # Calculate Rakeback (70% for known Hero IDs, 50% for everyone else)
        rakeback_rate = 0.70 if pid in HERO_IDS else 0.50
        rakeback_chips = rake_chips * rakeback_rate
        total_rakeback_chips += rakeback_chips
        
        # Calculate True Profit (with Rakeback)
        true_profit = profit_c + rakeback_chips
        
        # EV Adjustments
        ev_diff_c = player_ev_diffs.get(pid, 0.0) / 100.0
        ev_profit_c = profit_c + ev_diff_c
        
        # Calculate True EV Profit (with Rakeback)
        true_ev_profit = ev_profit_c + rakeback_chips
        
        # 20 points = 1 BB in 10/20 AoF (since blind=1000 subchips -> 10 normal chips. BB = 20 normal chips)
        ev_profit_bb = ev_profit_c / 20.0
        ev_bb_per_100 = f"{ev_profit_bb/hands*100:.1f}" if hands > 0 else "0.0"
        
        writer.writerow([
            pid, hands, pushed, push_pct, f"{profit_c:.2f}", f"{profit_bb:.1f}", bb_per_100, 
            f"{rake_chips:.2f}", f"{rakeback_chips:.2f}", f"{true_profit:.2f}", f"{true_ev_profit:.2f}",
            f"{ev_profit_c:.2f}", f"{ev_profit_bb:.1f}", ev_bb_per_100, last_seen
        ])

print(f"Exported {len(rows)} players to {csv_path}")

# Calculate and print totals
total_rake_chips = sum(player_rake_totals.values()) / 100.0
total_profit = sum(r[3] or 0 for r in rows) / 100.0
total_ev_profit = total_profit + (sum(player_ev_diffs.values()) / 100.0)
total_true_profit_chips = total_profit + total_rakeback_chips

print(f"Total Table Rake Collected: {total_rake_chips:.2f} chips")
print(f"Total Player Net Profit:    {total_profit:.2f} chips")
print(f"Total EV Net Profit:        {total_ev_profit:.2f} chips")
print(f"Total Rakeback Distributed: {total_rakeback_chips:.2f} chips")
print(f"Total True Profit (w/ RB):  {total_true_profit_chips:.2f} chips")
