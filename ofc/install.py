"""One command to get from a fresh clone to a working bot.

    python -m ofc.install

Everything after ``git clone`` happens here: the engine repository is
fetched next to this one, its Rust library is built, the Python packages
go in, and the test suite runs to prove the result. Each step says what it
is doing and stops on the first thing that does not work, because a setup
that half-succeeded silently is worse than one that failed loudly.

Safe to re-run. Every step checks whether it is already done first, so a
second run after fixing one problem does not redo the twenty-minute build.

Nothing here touches a live table. The last line prints the commands that
would, and they stay for the operator to type.
"""

import argparse
import json
import os
import shutil
import subprocess
import sys
from pathlib import Path

#: The engine lives on its own branch; ``main`` has no trained weights.
ENGINE_REPO = "https://github.com/giovinco0807/pineapple.git"
ENGINE_BRANCH = "codex/trainer-accounts"
#: The name ``M3Engine._find_root`` looks for beside this repository, so a
#: default install needs no configuration at all.
ENGINE_DIRNAME = "regular-ofc-pineapple"

REPO = Path(__file__).resolve().parents[1]
CONFIG_PATH = REPO / "ofc" / "data" / "m3engine.json"

#: Everything the OFC bot actually needs.
#:
#: Deliberately not automation/requirements.txt. That file is the AoF bot's,
#: and it pulls the whole OCR stack — easyocr brings torch, several GB of it.
#: The OFC bot reads cards from decoded packets and never looks at a pixel,
#: so none of it would ever be imported. pyautogui is not here either: the
#: drags and taps are ctypes SetCursorPos/mouse_event, and pc_input already
#: falls back to None when it is missing. Pass --with-aof for that stack.
OFC_PACKAGES = ("frida", "frida-tools", "pygetwindow")


class Failed(Exception):
    """A step could not finish. Carries what to do about it."""

    def __init__(self, what: str, fix: str = ""):
        super().__init__(what)
        self.what = what
        self.fix = fix


# --------------------------------------------------------------------- output
def say(message: str) -> None:
    print(message, flush=True)


def step(number: int, total: int, title: str) -> None:
    say(f"\n[{number}/{total}] {title}")


def run(command, cwd=None, quiet=False) -> subprocess.CompletedProcess:
    """Run a command, showing it first. Output is streamed, not swallowed."""
    say("      $ " + " ".join(str(part) for part in command))
    return subprocess.run(command, cwd=str(cwd) if cwd else None,
                          capture_output=quiet, text=True)


# ---------------------------------------------------------------- environment
def check_tools(need_cargo: bool) -> None:
    """Refuse early rather than half-way through a clone."""
    if sys.version_info < (3, 9):
        raise Failed(f"Python {sys.version_info.major}.{sys.version_info.minor} "
                     "is too old", "Python 3.9 or newer is needed")
    if shutil.which("git") is None:
        raise Failed("git was not found on PATH",
                     "install Git: https://git-scm.com/download/win")
    if need_cargo and shutil.which("cargo") is None:
        raise Failed(
            "cargo was not found on PATH",
            "the engine is a Rust library and has to be compiled.\n"
            "        Install Rust from https://rustup.rs/ , then open a NEW\n"
            "        terminal (the installer edits PATH) and re-run this.")


def warn_platform() -> None:
    """Say plainly what will and will not work off Windows.

    Setup runs anywhere — it is useful to build and test on the machine
    that is not going to play. Playing does not: the hook attaches to a
    Windows process and the drags are win32 calls.
    """
    if sys.platform == "win32":
        return
    say(f"  note: this is {sys.platform}, not Windows.")
    say("        Setup and the test suite work here, but playing does not:")
    say("        the capture attaches to PPPoker.exe and the placement drags")
    say("        are win32 calls. A library built here will not load on")
    say("        Windows either — build it there.")


# --------------------------------------------------------------------- engine
def engine_root(explicit) -> Path:
    """Where the engine repository should live."""
    if explicit:
        return Path(explicit).expanduser().resolve()
    configured = os.environ.get("OFC_REGULAR_ROOT")
    if configured:
        return Path(configured).expanduser().resolve()
    return REPO.parent / ENGINE_DIRNAME


def looks_like_engine(root: Path) -> bool:
    return (root / "rust" / "hu_m3_engine").is_dir()


def fetch_engine(root: Path) -> None:
    """Clone the engine, or bring an existing clone to the right branch."""
    if looks_like_engine(root):
        say(f"  already present: {root}")
        head = subprocess.run(["git", "rev-parse", "--abbrev-ref", "HEAD"],
                              cwd=str(root), capture_output=True, text=True)
        branch = head.stdout.strip()
        if branch and branch != ENGINE_BRANCH:
            say(f"  on branch {branch}, and the weights are on {ENGINE_BRANCH}")
            say("  leaving it alone — check it out yourself if that is wrong")
        return

    if root.exists() and any(root.iterdir()):
        raise Failed(f"{root} exists and is not the engine repository",
                     "move it aside, or pass --engine-root <other path>")

    say(f"  cloning {ENGINE_BRANCH} into {root}")
    say("  (a few hundred MB — the trained weights are committed, not LFS)")
    result = run(["git", "clone", "--branch", ENGINE_BRANCH,
                  "--single-branch", ENGINE_REPO, str(root)])
    if result.returncode != 0:
        raise Failed("the clone failed",
                     "check the network, and that this account can read\n"
                     f"        {ENGINE_REPO}")
    if not looks_like_engine(root):
        raise Failed(f"{root} was cloned but has no rust/hu_m3_engine",
                     f"the branch {ENGINE_BRANCH} may have moved")


def library_path(root: Path) -> Path:
    release = root / "target" / "release"
    for name in ("ofc_hu_m3_engine.dll", "libofc_hu_m3_engine.so",
                 "libofc_hu_m3_engine.dylib"):
        if (release / name).is_file():
            return release / name
    return release / ("ofc_hu_m3_engine.dll" if sys.platform == "win32"
                      else "libofc_hu_m3_engine.so")


def build_engine(root: Path) -> Path:
    library = library_path(root)
    if library.is_file():
        say(f"  already built: {library.name}")
        return library

    say("  compiling the engine (about 1-2 minutes, and only ever once)")
    result = run(["cargo", "build", "--release", "-p", "ofc_hu_m3_engine"],
                 cwd=root)
    if result.returncode != 0:
        raise Failed("the build failed",
                     "the cargo output above says why; a stale toolchain is\n"
                     "        the usual cause — try `rustup update`")

    library = library_path(root)
    if not library.is_file():
        raise Failed(f"the build reported success but {library} is not there",
                     "check the cargo output for where it put the library")
    return library


def check_weights(root: Path) -> int:
    """The library is useless without the trained weights beside it."""
    fixtures = root / "rust" / "hu_m3_engine" / "tests" / "fixtures"
    weights = sorted(fixtures.glob("*.bin")) if fixtures.is_dir() else []
    if not weights:
        raise Failed(f"no .bin weights in {fixtures}",
                     "the clone is incomplete, or on a branch without them")
    return len(weights)


def pin_engine(root: Path) -> None:
    """Write the path down, so nothing depends on an env var being set.

    Skipped when the engine sits where discovery already finds it — a
    config file that merely repeats the default is one more thing to go
    stale when a directory moves.
    """
    if root == REPO.parent / ENGINE_DIRNAME:
        say(f"  found automatically beside {REPO.name} — no config needed")
        return
    CONFIG_PATH.parent.mkdir(parents=True, exist_ok=True)
    CONFIG_PATH.write_text(json.dumps({"root": str(root)}, indent=2) + "\n",
                           encoding="utf-8")
    say(f"  wrote {CONFIG_PATH}")


# ------------------------------------------------------------------- packages
def install_packages(with_aof: bool = False) -> None:
    say(f"  {', '.join(OFC_PACKAGES)} — that is the whole list")
    result = run([sys.executable, "-m", "pip", "install", *OFC_PACKAGES])
    if result.returncode != 0:
        raise Failed(f"installing {', '.join(OFC_PACKAGES)} failed",
                     "the pip output above says why")

    if with_aof:
        requirements = REPO / "automation" / "requirements.txt"
        if not requirements.is_file():
            say(f"  no {requirements}, skipping the AoF stack")
        else:
            say("  also installing the AoF bot's OCR stack (--with-aof); this "
                "is a large download")
            result = run([sys.executable, "-m", "pip", "install", "-r",
                          str(requirements)])
            if result.returncode != 0:
                raise Failed("installing automation/requirements.txt failed",
                             "the pip output above says why")

    # Installed is not the same as importable — a wheel for the wrong Python
    # or the wrong architecture installs perfectly and then does not load.
    missing = []
    for module in ("frida", "pygetwindow"):
        probe = subprocess.run([sys.executable, "-c", f"import {module}"],
                               capture_output=True, text=True)
        if probe.returncode != 0:
            missing.append(f"{module}: {probe.stderr.strip().splitlines()[-1]}")
    if missing:
        raise Failed("a package installed but will not import:\n           "
                     + "\n           ".join(missing),
                     "usually a wheel built for a different Python version")
    say("  frida and pygetwindow import cleanly")


# ---------------------------------------------------------------------- proof
def run_tests(root: Path) -> None:
    """The only honest evidence that any of this worked."""
    environment = dict(os.environ, OFC_REGULAR_ROOT=str(root))
    result = subprocess.run([sys.executable, "-m", "ofc.tests.test_ofc"],
                            cwd=str(REPO), capture_output=True, text=True,
                            env=environment)
    tail = [line for line in result.stdout.splitlines() if line.strip()]
    for line in tail:
        if "FAIL" in line or "passed," in line:
            say("      " + line)
    if result.returncode != 0:
        raise Failed("the test suite did not pass",
                     "run `python -m ofc.tests.test_ofc` to see it in full")


def check_solver() -> bool:
    """Does the engine actually answer, through the same path a hand takes?"""
    result = subprocess.run([sys.executable, "-m", "ofc.main", "--list-solvers"],
                            cwd=str(REPO), capture_output=True, text=True)
    say(result.stdout.rstrip() or result.stderr.rstrip())
    return "m3" in result.stdout and "unavailable" not in result.stdout


# ----------------------------------------------------------------------- main
def main() -> int:
    parser = argparse.ArgumentParser(
        description="Set up the OFC bot after cloning it.")
    parser.add_argument("--engine-root", default=None,
                        help="where to put (or find) the engine repository; "
                             f"default is ../{ENGINE_DIRNAME}")
    parser.add_argument("--no-engine", action="store_true",
                        help="skip the engine entirely and use the baseline "
                             "solver, which is weak but needs nothing")
    parser.add_argument("--no-packages", action="store_true",
                        help="do not run pip")
    parser.add_argument("--with-aof", action="store_true",
                        help="also install the AoF bot's OCR stack from "
                             "automation/requirements.txt (several GB, and "
                             "the OFC bot never uses any of it)")
    args = parser.parse_args()

    total = 3 if args.no_engine else 5
    say("OFC bot setup")
    say(f"  repository: {REPO}")
    say(f"  python:     {sys.executable}")

    try:
        step(1, total, "Checking what is already here")
        check_tools(need_cargo=not args.no_engine)
        warn_platform()
        say("  git and python are fine")

        root = None
        if not args.no_engine:
            root = engine_root(args.engine_root)
            step(2, total, "Fetching the engine")
            fetch_engine(root)

            step(3, total, "Building the engine")
            library = build_engine(root)
            count = check_weights(root)
            say(f"  {library.name} is ready, with {count} trained weight files")
            pin_engine(root)

        step(total - 1, total, "Installing the Python packages")
        if args.no_packages:
            say("  skipped (--no-packages)")
        else:
            install_packages(with_aof=args.with_aof)

        step(total, total, "Proving it works")
        run_tests(root or REPO)
        if not args.no_engine:
            if check_solver():
                say("  the m3 engine loads and is selectable")
            else:
                say("  the m3 engine did NOT load — the line above says why")

    except Failed as failure:
        say(f"\n  STOPPED: {failure.what}")
        if failure.fix:
            say(f"  fix:     {failure.fix}")
        say("\n  Nothing was left half-applied; fix the above and re-run.")
        return 1
    except KeyboardInterrupt:
        say("\n  interrupted")
        return 130

    solver = "baseline" if args.no_engine else "m3"
    say("\nDone. Next, with PPPoker open and seated at an OFC table:")
    say("")
    say(f"  python -m ofc.main --hero-uid <YOUR UID> --solver {solver} --gui")
    say("")
    say("Automatic placement stays off until you have measured the table and")
    say("checked the drags against the real screen, in this order:")
    say("")
    say("  python -m ofc.placer --calibrate")
    say(f"  python -m ofc.main --hero-uid <UID> --solver {solver} --dry-place")
    say(f"  python -m ofc.main --hero-uid <UID> --solver {solver} --auto-place")
    say("")
    say("--dry-place prints every drag without clicking. Check its 'from'")
    say("coordinates against where your cards actually are: if the client")
    say("does not lay the hand out in packet order, auto-place would pick up")
    say("a different card every time. See ofc/README.md for the rest of what")
    say("only a live screen can answer.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
