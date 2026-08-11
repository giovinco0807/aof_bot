"""Command-line entry point for the OFC bot.

    python -m ofc.main --gui                        board and recommendation on screen
    python -m ofc.main --hero-uid 12345678          headless, advice to stdout
    python -m ofc.main --list-solvers               what is registered

Advice-only is the default and automation has to be asked for by name. That
is not caution for its own sake: a solver that has not been watched over a few
hundred hands will place cards you would not have placed, and the difference
is far easier to see in the log than to unwind at the table.
"""

import argparse
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from ofc import solver as solver_api                # noqa: E402
from ofc.advisor import Advisor                     # noqa: E402
from ofc.budget import TimeBudget                   # noqa: E402
from ofc.capture import OfcCapture                  # noqa: E402


def _load_extra_solver(path: Path) -> None:
    """Import a solver module by file path so it registers itself."""
    import importlib.util

    spec = importlib.util.spec_from_file_location(path.stem, path)
    if spec is None or spec.loader is None:
        raise ImportError(f"cannot import {path}")
    module = importlib.util.module_from_spec(spec)
    sys.modules[path.stem] = module
    spec.loader.exec_module(module)


def main() -> None:
    parser = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--gui", action="store_true", help="open the board window")
    parser.add_argument("--process", default="PPPoker.exe")
    parser.add_argument("--hero-uid", type=int, default=0,
                        help="your PPPoker UID — without it no seat is 'yours'")
    parser.add_argument("--solver", default="baseline")
    parser.add_argument("--solver-file", type=Path, action="append", default=[],
                        help="import an extra solver module before starting; repeatable")
    parser.add_argument("--budget", type=float, default=None,
                        help="seconds per decision, the same for every street; "
                             "overrides the saved per-street settings")
    parser.add_argument("--budget-street", action="append", default=[],
                        metavar="STREET=SECONDS",
                        help="thinking time for one street, e.g. --budget-street 0=8 "
                             "(street 0 is the opening five cards). Repeatable.")
    parser.add_argument("--budget-fantasyland", type=float, default=None,
                        metavar="SECONDS", help="thinking time for a Fantasyland deal")
    parser.add_argument("--ignore-table-clock", action="store_true",
                        help="do not shorten the budget to fit the table's own timer")
    parser.add_argument("--show-budget", action="store_true",
                        help="print the thinking time that would be used and exit")
    parser.add_argument("--auto-place", action="store_true",
                        help="actually place the cards (needs a calibrated layout)")
    parser.add_argument("--dry-place", action="store_true",
                        help="print the drags each decision would make, click nothing "
                             "— check a calibration against live hands this way first")
    parser.add_argument("--list-solvers", action="store_true")
    parser.add_argument("--quiet", action="store_true")
    args = parser.parse_args()

    for path in args.solver_file:
        try:
            _load_extra_solver(path)
            print(f"loaded solver module {path}")
        except Exception as exc:                    # noqa: BLE001
            parser.error(f"could not load {path}: {type(exc).__name__}: {exc}")

    if args.list_solvers:
        from ofc import solvers
        print("registered solvers:")
        for name in solver_api.available():
            print(f"  {name}")
        for name, why in solvers.failed.items():
            print(f"  {name}  (failed to load: {why})")
        return

    # Thinking time: the saved per-street settings, then whatever the flags
    # override. --budget flattens everything to one value; the more specific
    # flags adjust individual streets.
    budget = TimeBudget.uniform(args.budget) if args.budget else TimeBudget.load()
    for pair in args.budget_street:
        street, _, seconds = pair.partition("=")
        try:
            budget.set_street(int(street), float(seconds))
        except ValueError:
            parser.error(f"--budget-street wants STREET=SECONDS, got {pair!r}")
    if args.budget_fantasyland is not None:
        budget.fantasyland = max(0.1, args.budget_fantasyland)
    if args.ignore_table_clock:
        budget.respect_table_clock = False

    if args.show_budget:
        print(budget.describe())
        return

    if args.gui:
        from ofc.gui import OfcGui
        gui = OfcGui()
        if args.hero_uid:
            gui.var_hero_uid.set(str(args.hero_uid))
        gui.var_process.set(args.process)
        if args.solver in solver_api.available():
            gui.var_solver.set(args.solver)
        gui.apply_budget(budget)
        gui.run()
        return

    if args.solver not in solver_api.available():
        parser.error(f"unknown solver {args.solver!r}; "
                     f"available: {', '.join(solver_api.available())}")
    if not args.hero_uid:
        parser.error("--hero-uid is required: without it the bot cannot tell "
                     "which seat is yours")

    on_advice = None
    if args.auto_place or args.dry_place:
        from ofc.placer import Placer
        # Checked between drags, so Ctrl-C stops a placement already under
        # way rather than only preventing the next one.
        state = {"advisor": None}
        placer = Placer(dry_run=args.dry_place,
                        should_continue=lambda: (state["advisor"] is None
                                                 or state["advisor"].running))
        problems = placer.readiness()
        if problems:
            print("placement refused — the layout is not ready:")
            for problem in problems:
                print(f"  {problem}")
            print("run:  python -m ofc.placer --calibrate")
            return
        placer.enabled = True

        # The advisor hands over the request alongside the advice, because
        # the drags need the dealt order and the board the cards go onto.
        def on_advice(advice, request):
            placer.execute(advice, request)

        if args.dry_place:
            print("dry placement — every drag will be printed, nothing clicked")
        else:
            print("auto-place is ON — move the mouse to a screen corner to abort")

    advisor = Advisor(hero_uid=args.hero_uid, solver=args.solver,
                      time_budget=budget, verbose=not args.quiet,
                      on_advice=on_advice)
    if args.auto_place or args.dry_place:
        state["advisor"] = advisor
    capture = OfcCapture(process_name=args.process, advisor=advisor,
                         verbose=not args.quiet)

    print(f"solver: {args.solver} | hero uid: {args.hero_uid} | "
          f"mode: {'dry placement' if args.dry_place else 'auto-place' if on_advice else 'advice only'}")
    print(f"thinking time: {budget.describe()}")
    try:
        capture.run()
    except KeyboardInterrupt:
        print("\nstopping")
    except Exception as exc:                        # noqa: BLE001
        print(f"capture failed: {type(exc).__name__}: {exc}")
        sys.exit(1)
    finally:
        capture.stop()
        print(f"packets: {capture.packets} | decisions: {advisor.decisions} | "
              f"errors: {advisor.errors}")


if __name__ == "__main__":
    main()
