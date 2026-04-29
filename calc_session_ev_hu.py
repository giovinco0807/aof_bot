"""Calculate EV for recent sessions from hand history DB.

Groups hands into sessions (30-min gap = new session).
For showdown hands, computes preflop equity via Monte Carlo (treys).
Outputs per-session: actual P/L, equity-based EV, and diff.
Supports multiway pots (2P, 3P, 4P).
"""

import sqlite3
import datetime
from treys import Card, Evaluator, Deck
from pathlib import Path

DB_PATH = Path("d:/aof_bot/automation/data/hands.db")
import sys
HERO_ID = sys.argv[1] if len(sys.argv) > 1 else "13268363"
SESSION_GAP_SECONDS = 1800  # 30 min gap = new session
NUM_SESSIONS = int(sys.argv[2]) if len(sys.argv) > 2 else 5
EQUITY_SIMS = 2000

evaluator = Evaluator()


def calculate_equity(hole_cards_list, n_sims=EQUITY_SIMS):
    """Calculate preflop equity for multiple hands using Monte Carlo.
    
    Args:
        hole_cards_list: list of card strings like ["AhKd", "QcQs", "Ts9s"]
    Returns:
        list of equities (floats, sum to 1.0)
    """
    if len(hole_cards_list) < 2:
        return [1.0] * len(hole_cards_list)

    parsed_hands = []
    for hc in hole_cards_list:
        if len(hc) < 4:
            return None
        try:
            c1 = Card.new(hc[0:2])
            c2 = Card.new(hc[2:4])
            parsed_hands.append([c1, c2])
        except Exception:
            return None

    all_known = []
    for hand in parsed_hands:
        all_known.extend(hand)

    wins = [0.0] * len(parsed_hands)

    for _ in range(n_sims):
        deck = Deck()
        for card in all_known:
            if card in deck.cards:
                deck.cards.remove(card)
        board = deck.draw(5)
        scores = [evaluator.evaluate(board, hand) for hand in parsed_hands]
        best = min(scores)
        winners = [i for i, s in enumerate(scores) if s == best]
        share = 1.0 / len(winners)
        for w in winners:
            wins[w] += share

    return [w / n_sims for w in wins]


def main():
    conn = sqlite3.connect(str(DB_PATH))
    
    # Get all hero hands with full data
    rows = conn.execute("""
        SELECT h.id, h.timestamp, h.num_players, h.pot_chips, h.rake_chips,
               h.bb_size, hp.action, hp.profit_chips, hp.cards, hp.position
        FROM hand_players hp
        JOIN hands h ON hp.hand_id = h.id
        WHERE hp.player_id = ? AND h.num_players IN (3, 4)
        ORDER BY h.timestamp ASC
    """, (HERO_ID,)).fetchall()
    
    if not rows:
        print(f"No hands found for hero {HERO_ID}")
        return
    
    print(f"Total hero hands: {len(rows)}")
    
    # Build hand_id -> all players mapping for showdown hands
    all_hand_players = {}
    for hand_id, _, _, _, _, _, _, _, _, _ in rows:
        if hand_id not in all_hand_players:
            players = conn.execute("""
                SELECT player_id, action, cards, profit_chips
                FROM hand_players
                WHERE hand_id = ?
            """, (hand_id,)).fetchall()
            all_hand_players[hand_id] = players
    
    conn.close()
    
    # Group into sessions by 30-min gaps
    sessions = []
    current_session = []
    last_time = None
    
    for row in rows:
        hand_id, ts_str, np, pot, rake, bb, action, profit, cards, pos = row
        try:
            ts = datetime.datetime.fromisoformat(ts_str)
        except Exception:
            continue
        
        if last_time and (ts - last_time).total_seconds() > SESSION_GAP_SECONDS:
            if current_session:
                sessions.append(current_session)
            current_session = []
        
        current_session.append({
            "hand_id": hand_id,
            "time": ts,
            "num_players": np,
            "pot": pot or 0,
            "rake": rake or 0,
            "bb_size": bb or 2000,
            "action": action or "",
            "profit": profit or 0,
            "cards": cards or "",
            "position": pos or "",
        })
        last_time = ts
    
    if current_session:
        sessions.append(current_session)
    
    print(f"Total sessions: {len(sessions)}")
    print()
    
    # Process last N sessions
    target_sessions = sessions[-NUM_SESSIONS:]
    
    print(f"{'='*80}")
    print(f"  LAST {len(target_sessions)} SESSIONS - EV ANALYSIS (hero={HERO_ID})")
    print(f"{'='*80}")
    print()
    
    grand_total_pl = 0
    grand_total_ev = 0
    grand_total_hands = 0
    grand_showdown_hands = 0
    
    for sess_idx, session in enumerate(target_sessions):
        start = session[0]["time"]
        end = session[-1]["time"]
        duration = end - start
        n_hands = len(session)
        
        total_profit = 0
        total_ev = 0.0
        showdown_count = 0
        multiway_count = 0
        fold_count = 0
        nocontest_count = 0
        
        for hand in session:
            hero_profit = hand["profit"]
            total_profit += hero_profit
            
            hero_action = hand["action"].upper()
            
            # Case 1: Hero folded
            if hero_action == "F":
                total_ev += hero_profit
                fold_count += 1
                continue
            
            # Get all players for this hand
            players = all_hand_players.get(hand["hand_id"], [])
            
            # Collect all-in players with cards (showdown participants)
            allin_with_cards = []
            hero_idx = -1
            for pid, p_action, p_cards, p_profit in players:
                if p_action and p_action.upper() == "A" and p_cards and len(p_cards) >= 4:
                    if pid == HERO_ID:
                        hero_idx = len(allin_with_cards)
                    allin_with_cards.append((pid, p_cards))
            
            # Case 2: No showdown (hero pushed, everyone folded)
            if len(allin_with_cards) < 2 or hero_idx < 0:
                total_ev += hero_profit
                nocontest_count += 1
                continue
            
            # Case 3: Showdown - compute equity
            card_strings = [cards for _, cards in allin_with_cards]
            equities = calculate_equity(card_strings, n_sims=EQUITY_SIMS)
            
            if equities is None:
                total_ev += hero_profit
                continue
            
            num_allin = len(allin_with_cards)
            net_pot = hand["pot"] - hand["rake"]
            hero_equity = equities[hero_idx]
            hero_ev = hero_equity * net_pot - (net_pot / num_allin)
            
            total_ev += hero_ev
            showdown_count += 1
            if num_allin > 2:
                multiway_count += 1
        
        # Convert to BB
        bb_size = session[0]["bb_size"]
        if bb_size <= 0:
            bb_size = 2000
        pl_bb = total_profit / bb_size
        ev_bb = total_ev / bb_size
        diff_bb = ev_bb - pl_bb
        bb100 = pl_bb / n_hands * 100 if n_hands > 0 else 0
        ev_bb100 = ev_bb / n_hands * 100 if n_hands > 0 else 0
        
        dur_str = f"{int(duration.total_seconds() // 3600)}h{int((duration.total_seconds() % 3600) // 60):02d}m"
        
        pl_sign = "+" if total_profit >= 0 else ""
        ev_sign = "+" if total_ev >= 0 else ""
        diff_sign = "+" if diff_bb >= 0 else ""
        
        print(f"  Session {sess_idx + 1}: {start.strftime('%m/%d %H:%M')} ~ {end.strftime('%H:%M')} ({dur_str})")
        print(f"  Hands: {n_hands} | Showdowns: {showdown_count} (multiway: {multiway_count}) | Folds: {fold_count} | No-contest: {nocontest_count}")
        print(f"  P/L:  {pl_sign}{total_profit:>8.0f} chips ({pl_sign}{pl_bb:>6.1f} BB) | BB/100: {bb100:>+6.1f}")
        print(f"  EV:   {ev_sign}{total_ev:>8.0f} chips ({ev_sign}{ev_bb:>6.1f} BB) | BB/100: {ev_bb100:>+6.1f}")
        print(f"  Diff: {diff_sign}{diff_bb:>6.1f} BB {'(LUCKY)' if diff_bb < 0 else '(UNLUCKY)' if diff_bb > 0 else ''}")
        print(f"  {'_'*60}")
        
        grand_total_pl += total_profit
        grand_total_ev += total_ev
        grand_total_hands += n_hands
        grand_showdown_hands += showdown_count
    
    # Grand totals
    print()
    grand_pl_bb = grand_total_pl / bb_size if bb_size > 0 else 0
    grand_ev_bb = grand_total_ev / bb_size if bb_size > 0 else 0
    grand_diff = grand_ev_bb - grand_pl_bb
    grand_bb100 = grand_pl_bb / grand_total_hands * 100 if grand_total_hands > 0 else 0
    grand_ev_bb100 = grand_ev_bb / grand_total_hands * 100 if grand_total_hands > 0 else 0
    
    pl_s = "+" if grand_total_pl >= 0 else ""
    ev_s = "+" if grand_total_ev >= 0 else ""
    diff_s = "+" if grand_diff >= 0 else ""
    
    print(f"{'='*80}")
    print(f"  TOTAL ({len(target_sessions)} sessions, {grand_total_hands} hands, "
          f"{grand_showdown_hands} showdowns)")
    print(f"  P/L:  {pl_s}{grand_total_pl:>8.0f} chips ({pl_s}{grand_pl_bb:>6.1f} BB) | BB/100: {grand_bb100:>+6.1f}")
    print(f"  EV:   {ev_s}{grand_total_ev:>8.0f} chips ({ev_s}{grand_ev_bb:>6.1f} BB) | BB/100: {grand_ev_bb100:>+6.1f}")
    print(f"  Diff: {diff_s}{grand_diff:>6.1f} BB {'(Running above EV)' if grand_diff < 0 else '(Running below EV)' if grand_diff > 0 else ''}")
    print(f"{'='*80}")


if __name__ == "__main__":
    main()
