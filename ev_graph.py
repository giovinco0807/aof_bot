"""Generate cumulative P/L vs EV graph for April 1st sessions."""
import sqlite3, datetime, sys, io
from treys import Card, Evaluator, Deck
from pathlib import Path
import matplotlib
matplotlib.use('Agg')
import matplotlib.pyplot as plt
import matplotlib.ticker as ticker
import numpy as np

DB_PATH = Path("d:/aof_bot/automation/data/hands.db")
HERO_IDS = [("13076903", "HIRO (13076903)"), ("13268363", "Account 2 (13268363)")]
DATE_FILTER = "2026-04-01"
EQUITY_SIMS = 2000
OUT_PATH = Path("d:/aof_bot/ev_graph_apr1.png")

evaluator = Evaluator()

def calc_equity(cards_list, n=EQUITY_SIMS):
    if len(cards_list) < 2:
        return [1.0] * len(cards_list)
    parsed = []
    for hc in cards_list:
        if len(hc) < 4: return None
        try: parsed.append([Card.new(hc[0:2]), Card.new(hc[2:4])])
        except: return None
    known = [c for h in parsed for c in h]
    wins = [0.0] * len(parsed)
    for _ in range(n):
        deck = Deck()
        for c in known:
            if c in deck.cards: deck.cards.remove(c)
        board = deck.draw(5)
        scores = [evaluator.evaluate(board, h) for h in parsed]
        best = min(scores)
        ws = [i for i,s in enumerate(scores) if s == best]
        sh = 1.0/len(ws)
        for w in ws: wins[w] += sh
    return [w/n for w in wins]


def get_hero_data(conn, hero_id):
    """Get hand-by-hand P/L and EV for a hero on the target date."""
    rows = conn.execute("""
        SELECT h.id, h.timestamp, h.pot_chips, h.rake_chips, h.bb_size,
               hp.action, hp.profit_chips, hp.cards
        FROM hand_players hp
        JOIN hands h ON hp.hand_id = h.id
        WHERE hp.player_id = ? AND h.timestamp LIKE ?
        ORDER BY h.timestamp ASC
    """, (hero_id, DATE_FILTER + "%")).fetchall()

    if not rows:
        return [], [], [], []

    # Pre-fetch all hand players for showdown equity
    hand_ids = set(r[0] for r in rows)
    all_hp = {}
    for hid in hand_ids:
        ps = conn.execute("SELECT player_id, action, cards, profit_chips FROM hand_players WHERE hand_id = ?", (hid,)).fetchall()
        all_hp[hid] = ps

    hand_nums = []
    cum_pl = []
    cum_ev = []
    timestamps = []
    running_pl = 0.0
    running_ev = 0.0

    total = len(rows)
    for idx, row in enumerate(rows):
        hid, ts_str, pot, rake, bb, action, profit, cards = row
        pot = pot or 0
        rake = rake or 0
        bb = bb or 2000
        profit = profit or 0
        action = (action or "").upper()

        running_pl += profit

        # EV calculation
        if action == "F":
            running_ev += profit
        else:
            players = all_hp.get(hid, [])
            allin_wc = []
            hero_idx = -1
            for pid, pa, pc, pp in players:
                if pa and pa.upper() == "A" and pc and len(pc) >= 4:
                    if pid == hero_id: hero_idx = len(allin_wc)
                    allin_wc.append((pid, pc, pp or 0))

            if len(allin_wc) < 2 or hero_idx < 0:
                running_ev += profit
            else:
                eqs = calc_equity([c for _,c,_ in allin_wc])
                if eqs is None:
                    running_ev += profit
                else:
                    # pot_chips in DB is already net of rake
                    stack_size = 8 * bb
                    hero_ev = eqs[hero_idx] * pot - stack_size
                    running_ev += hero_ev

        hand_nums.append(idx + 1)
        cum_pl.append(running_pl / bb)  # Convert to BB
        cum_ev.append(running_ev / bb)
        try:
            timestamps.append(datetime.datetime.fromisoformat(ts_str))
        except:
            timestamps.append(None)

        if (idx + 1) % 200 == 0:
            print(f"  [{hero_id}] {idx+1}/{total} hands processed...", file=sys.stderr)

    return hand_nums, cum_pl, cum_ev, timestamps


def main():
    print("Generating EV graph...", file=sys.stderr)
    conn = sqlite3.connect(str(DB_PATH))

    # Dark theme
    plt.style.use('dark_background')
    fig, axes = plt.subplots(len(HERO_IDS), 1, figsize=(14, 5 * len(HERO_IDS)),
                             gridspec_kw={'hspace': 0.35})
    if len(HERO_IDS) == 1:
        axes = [axes]

    colors_pl = ['#FF6B6B', '#4ECDC4']
    colors_ev = ['#FFE66D', '#A8E6CF']
    colors_fill = ['#FF6B6B22', '#4ECDC422']

    for i, (hero_id, label) in enumerate(HERO_IDS):
        print(f"Processing {label}...", file=sys.stderr)
        hand_nums, cum_pl, cum_ev, timestamps = get_hero_data(conn, hero_id)

        if not hand_nums:
            axes[i].text(0.5, 0.5, f"No data for {label}", ha='center', va='center',
                        fontsize=14, color='gray', transform=axes[i].transAxes)
            continue

        ax = axes[i]

        # Plot EV line (behind)
        ax.plot(hand_nums, cum_ev, color=colors_ev[i], linewidth=2.0,
                label=f'EV ({cum_ev[-1]:+.1f} BB)', alpha=0.9, zorder=3)

        # Plot actual P/L line (front)
        ax.plot(hand_nums, cum_pl, color=colors_pl[i], linewidth=2.5,
                label=f'Actual P/L ({cum_pl[-1]:+.1f} BB)', alpha=0.95, zorder=4)

        # Fill between P/L and EV
        pl_arr = np.array(cum_pl)
        ev_arr = np.array(cum_ev)
        ax.fill_between(hand_nums, cum_pl, cum_ev,
                        where=(pl_arr >= ev_arr), color='#00FF0015', zorder=2)
        ax.fill_between(hand_nums, cum_pl, cum_ev,
                        where=(pl_arr < ev_arr), color='#FF000015', zorder=2)

        # Zero line
        ax.axhline(y=0, color='white', linewidth=0.5, alpha=0.3, linestyle='--')

        # Final values annotation
        final_pl = cum_pl[-1]
        final_ev = cum_ev[-1]
        diff = final_ev - final_pl
        ax.annotate(f'P/L: {final_pl:+.1f}BB', xy=(hand_nums[-1], final_pl),
                    xytext=(10, -5), textcoords='offset points',
                    color=colors_pl[i], fontsize=10, fontweight='bold')
        ax.annotate(f'EV: {final_ev:+.1f}BB', xy=(hand_nums[-1], final_ev),
                    xytext=(10, 10), textcoords='offset points',
                    color=colors_ev[i], fontsize=10, fontweight='bold')

        # Add time labels on x-axis (show timestamps at intervals)
        if timestamps:
            tick_interval = max(1, len(hand_nums) // 8)
            tick_positions = list(range(0, len(hand_nums), tick_interval))
            if tick_positions[-1] != len(hand_nums) - 1:
                tick_positions.append(len(hand_nums) - 1)
            tick_labels = []
            for tp in tick_positions:
                ts = timestamps[tp]
                if ts:
                    tick_labels.append(ts.strftime('%H:%M'))
                else:
                    tick_labels.append('')
            ax.set_xticks([hand_nums[tp] for tp in tick_positions])
            ax.set_xticklabels(tick_labels, fontsize=8)

        ax.set_xlabel('Time', fontsize=10)
        ax.set_ylabel('Cumulative (BB)', fontsize=11)
        ax.set_title(f'{label} - April 1, 2026  |  {len(hand_nums)} hands  |  '
                     f'Diff: {diff:+.1f}BB {"(Unlucky)" if diff > 0 else "(Lucky)"}',
                     fontsize=13, fontweight='bold', pad=12)
        ax.legend(loc='upper left', fontsize=10, framealpha=0.7)
        ax.grid(True, alpha=0.15)

        # Style
        ax.spines['top'].set_visible(False)
        ax.spines['right'].set_visible(False)

    conn.close()

    plt.savefig(str(OUT_PATH), dpi=150, bbox_inches='tight',
                facecolor='#1a1a2e', edgecolor='none')
    print(f"Graph saved to {OUT_PATH}", file=sys.stderr)
    plt.close()


if __name__ == "__main__":
    main()
