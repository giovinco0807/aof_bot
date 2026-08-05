"""Live packet feed for the OFC bot.

This attaches Frida to PPPoker with the hook script this repository already
has and forwards the ``Pine*`` packets to an :class:`~ofc.advisor.Advisor`.

It is a separate reader rather than a change to ``hook/packet_capture.py``,
and that is a deliberate trade. The AoF capture is a long, working, live
piece of software; the OFC bot needs none of its hand history, exploit model
or all-in logic, and threading OFC state through it would put the working bot
at risk for no benefit. The hook script itself is shared and untouched — it
already decodes every OFC packet, which is the part that is expensive to
build and easy to break.

If you would rather run one process for both games, use
:func:`attach_to_capture` instead: it wires an advisor onto a live
``PacketCapture`` instance without modifying it.
"""

import sys
import time
from pathlib import Path
from typing import Optional

REPO_ROOT = Path(__file__).resolve().parent.parent
HOOK_SCRIPT = REPO_ROOT / "hook" / "pppoker_hook.js"

#: Packets this reader cares about. Everything else the hook sends is ignored.
OFC_PACKETS = frozenset({
    "PineGameStartBRC", "PineHandCardBRC", "PineActionBRC", "PineResultBRC",
    "PineSitDownBRC", "PineStandUpBRC", "PineRoomStatusBRC",
})


def find_pid(process_name: str) -> Optional[int]:
    """PID of a running process by image name.

    Uses ``tasklist`` on Windows, which is where PPPoker runs, and falls back
    to psutil elsewhere so the module stays importable for tests.
    """
    if sys.platform == "win32":
        import subprocess
        result = subprocess.run(
            ["tasklist", "/FI", f"IMAGENAME eq {process_name}", "/FO", "CSV", "/NH"],
            capture_output=True, text=True, timeout=10)
        for line in result.stdout.splitlines():
            parts = [p.strip('"') for p in line.split('","')]
            if len(parts) >= 2 and parts[0].lower() == process_name.lower():
                try:
                    return int(parts[1])
                except ValueError:
                    continue
        return None

    try:
        import psutil
    except ImportError:
        return None
    for process in psutil.process_iter(["name", "pid"]):
        if (process.info.get("name") or "").lower() == process_name.lower():
            return process.info["pid"]
    return None


class OfcCapture:
    """Frida session that feeds OFC packets to an advisor."""

    def __init__(self, process_name: str = "PPPoker.exe", advisor=None,
                 hook_script: Path = HOOK_SCRIPT, verbose: bool = True):
        self.process_name = process_name
        self.advisor = advisor
        self.hook_script = Path(hook_script)
        self.verbose = verbose

        self.running = False
        self.packets = 0
        self.session = None
        self.script = None

    # ------------------------------------------------------------ lifecycle
    def start(self) -> None:
        """Attach and load the hook. Raises rather than exiting the process.

        The AoF capture calls ``sys.exit`` on failure, which is fine for a
        command-line tool and hostile to a GUI; this raises so the caller can
        show the reason and stay alive.
        """
        try:
            import frida
        except ImportError as exc:
            raise RuntimeError("frida is not installed — pip install frida") from exc

        if not self.hook_script.is_file():
            raise FileNotFoundError(f"hook script missing: {self.hook_script}")

        pid = find_pid(self.process_name)
        if pid is None:
            raise RuntimeError(f"{self.process_name} is not running")

        if self.verbose:
            print(f"  [OFC] attaching to {self.process_name} (pid {pid})")

        self.session = frida.attach(pid)
        self.script = self.session.create_script(self.hook_script.read_text(encoding="utf-8"))
        self.script.on("message", self._on_message)
        self.script.load()
        self.running = True

        if self.advisor is not None:
            self.advisor.start()
        if self.verbose:
            print("  [OFC] hook loaded, waiting for OFC packets")

    def stop(self) -> None:
        self.running = False
        if self.script is not None:
            try:
                self.script.unload()
            except Exception:                      # noqa: BLE001
                pass
            self.script = None
        if self.session is not None:
            try:
                self.session.detach()
            except Exception:                      # noqa: BLE001
                pass
            self.session = None
        if self.advisor is not None:
            self.advisor.stop()

    def run(self) -> None:
        """Attach and block until stopped."""
        self.start()
        try:
            while self.running:
                time.sleep(0.1)
        finally:
            self.stop()

    # -------------------------------------------------------------- packets
    def _on_message(self, message: dict, data) -> None:
        """Frida's callback. Kept short — this runs on Frida's own thread."""
        if message.get("type") != "send":
            if self.verbose:
                print(f"  [OFC] frida: {message.get('description', message)}")
            return

        payload = message.get("payload") or {}
        if payload.get("type") != "packet":
            return

        name = payload.get("name")
        if name not in OFC_PACKETS:
            return

        self.packets += 1
        if self.advisor is not None:
            self.advisor.feed(name, payload.get("tableId", 0), payload.get("data") or {})


def attach_to_capture(capture, advisor) -> None:
    """Feed OFC packets from a running ``PacketCapture`` into an advisor.

    For running both games in one process. It wraps the capture's packet
    dispatch rather than editing it, so the AoF path keeps behaving exactly
    as it did — if the advisor raises, the wrapper swallows it and the
    original handler still runs.

        from hook.packet_capture import PacketCapture
        from ofc.advisor import Advisor
        from ofc.capture import attach_to_capture

        capture = PacketCapture(hero_uid=UID)
        advisor = Advisor(hero_uid=UID, event_queue=gui_queue)
        attach_to_capture(capture, advisor)
        capture.run()
    """
    original = capture._handle_packet
    if getattr(original, "_ofc_wrapped", False):
        return

    def wrapped(payload):
        name = payload.get("name")
        if name in OFC_PACKETS:
            try:
                advisor.feed(name, payload.get("tableId", 0), payload.get("data") or {})
            except Exception as exc:               # noqa: BLE001
                print(f"  [OFC] advisor failed on {name}: {type(exc).__name__}: {exc}")
        return original(payload)

    wrapped._ofc_wrapped = True
    capture._handle_packet = wrapped
    advisor.start()


__all__ = ["OfcCapture", "attach_to_capture", "find_pid", "OFC_PACKETS", "HOOK_SCRIPT"]
