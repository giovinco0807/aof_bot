"""AoF Bot main entry point.

Usage:
    python main.py              # Run the bot (default: exploit mode)
    python main.py --gto        # Run in pure GTO mode
    python main.py --dry-run    # Observe only, don't click buttons
"""

import argparse
import time
import sys

from config import create_default_config, API_URL
from table_reader import TableReader
from bot_controller import BotController
from hand_recorder import HandRecorder
from input_simulator import InputSimulator


def main():
    parser = argparse.ArgumentParser(description="AoF Poker Bot")
    parser.add_argument("--gto", action="store_true", help="Use pure GTO strategy (no exploit)")
    parser.add_argument("--dry-run", action="store_true", help="Observe only, no clicks")
    parser.add_argument("--api-url", default=API_URL, help="Solver API URL")
    parser.add_argument("--poll-interval", type=float, default=0.5, help="Screen poll interval (seconds)")
    args = parser.parse_args()

    print("=" * 50)
    print("  AoF Bot - All-in or Fold Poker Bot")
    print("=" * 50)
    print(f"  Mode: {'GTO' if args.gto else 'Exploit'}")
    print(f"  Dry run: {args.dry_run}")
    print(f"  API: {args.api_url}")
    print(f"  Poll interval: {args.poll_interval}s")
    print("=" * 50)
    print()
    print("Press Ctrl+C to stop the bot.")
    print("Move mouse to top-left corner to emergency stop (pyautogui failsafe).")
    print()

    # Initialize components
    config = create_default_config()
    reader = TableReader(config)
    bot = BotController(api_url=args.api_url, use_exploit=not args.gto)
    recorder = HandRecorder(bot)
    input_sim = InputSimulator(humanize=True)

    # Main loop
    try:
        while True:
            # Read table state
            state = reader.read_table()

            # Update hand recorder
            recorder.update(state)

            # Make decision if it's our turn
            if state.is_our_turn:
                decision = bot.decide(state)

                if decision and not args.dry_run:
                    if decision == "allin":
                        input_sim.click_allin(config.allin_button)
                    elif decision == "fold":
                        input_sim.click_fold(config.fold_button)
                elif decision:
                    print(f"[DRY RUN] Would {decision}")

            time.sleep(args.poll_interval)

    except KeyboardInterrupt:
        print("\nBot stopped.")
    except Exception as e:
        print(f"\nError: {e}")
        import traceback
        traceback.print_exc()
        sys.exit(1)


if __name__ == "__main__":
    main()
