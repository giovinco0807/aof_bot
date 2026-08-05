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
    parser.add_argument("--budget", type=float, default=4.0,
                        help="seconds a solver may spend on one decision")
    parser.add_argument("--auto-place", action="store_true",
                        help="actually place the cards (needs a calibrated layout)")
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

    if args.gui:
        from ofc.gui import OfcGui
        gui = OfcGui()
        if args.hero_uid:
            gui.var_hero_uid.set(str(args.hero_uid))
        gui.var_process.set(args.process)
        if args.solver in solver_api.available():
            gui.var_solver.set(args.solver)
        gui.var_budget.set(args.budget)
        gui.run()
        return

    if args.solver not in solver_api.available():
        parser.error(f"unknown solver {args.solver!r}; "
                     f"available: {', '.join(solver_api.available())}")
    if not args.hero_uid:
        parser.error("--hero-uid is required: without it the bot cannot tell "
                     "which seat is yours")

    on_advice = None
    if args.auto_place:
        from ofc.placer import Placer
        placer = Placer()
        problems = placer.readiness()
        if problems:
            print("auto-place refused — the layout is not ready:")
            for problem in problems:
                print(f"  {problem}")
            print("run:  python -m ofc.placer --calibrate")
            return
        placer.enabled = True
        on_advice = placer.execute
        print("auto-place is ON — move the mouse to a screen corner to abort")

    advisor = Advisor(hero_uid=args.hero_uid, solver=args.solver,
                      time_budget=args.budget, verbose=not args.quiet,
                      on_advice=on_advice)
    capture = OfcCapture(process_name=args.process, advisor=advisor,
                         verbose=not args.quiet)

    print(f"solver: {args.solver} | hero uid: {args.hero_uid} | "
          f"mode: {'auto-place' if on_advice else 'advice only'}")
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
