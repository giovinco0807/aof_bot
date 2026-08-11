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
import threading
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
    """Frida session that feeds OFC packets to an advisor.

    Attaching is a loop, not a one-shot. PPPoker is normally started after
    the bot, restarted during a session, or simply not up yet when the user
    presses the button — and each of those should mean "wait", not "give up".
    :meth:`run` therefore waits for the process to appear, attaches, and goes
    back to waiting if the session drops.
    """

    def __init__(self, process_name: str = "PPPoker.exe", advisor=None,
                 hook_script: Path = HOOK_SCRIPT, verbose: bool = True,
                 reconnect: bool = True, poll_interval: float = 2.0,
                 on_status=None):
        self.process_name = process_name
        self.advisor = advisor
        self.hook_script = Path(hook_script)
        self.verbose = verbose
        #: Keep waiting for the process, and re-attach if the session drops.
        self.reconnect = reconnect
        self.poll_interval = poll_interval
        #: Called with a short state word — waiting / attached / disconnected.
        #: Runs on the capture thread, so a GUI must marshal it.
        self.on_status = on_status

        self.running = False
        self.attached = False
        self.packets = 0
        self.attaches = 0
        self.session = None
        self.script = None
        self._dropped = threading.Event()
        self._stopping = False

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

        self._dropped.clear()
        self.session = frida.attach(pid)
        # The client closing, crashing, or being restarted all arrive here;
        # without this the capture would sit on a dead session forever.
        try:
            self.session.on("detached", self._on_detached)
        except Exception:                          # noqa: BLE001 - older frida
            pass
        self.script = self.session.create_script(self.hook_script.read_text(encoding="utf-8"))
        self.script.on("message", self._on_message)
        self.script.load()
        self.running = True
        self.attached = True
        self.attaches += 1

        if self.advisor is not None:
            self.advisor.start()
        self._status("attached")
        if self.verbose:
            print("  [OFC] hook loaded, following the table")

    def _on_detached(self, *args) -> None:
        self.attached = False
        self._dropped.set()
        reason = args[0] if args else "detached"
        self._status("disconnected")
        if self.verbose:
            print(f"  [OFC] session ended ({reason})")

    def _status(self, state: str) -> None:
        if self.on_status is None:
            return
        try:
            self.on_status(state)
        except Exception:                          # noqa: BLE001
            pass

    def stop(self) -> None:
        self._stopping = True
        self.running = False
        self.attached = False
        self._dropped.set()
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
        """Follow the client until stopped.

        Waits for PPPoker to appear rather than refusing when it is not up,
        and re-attaches after it restarts. With ``reconnect`` off this is the
        old behaviour: attach once, raise if the process is not there.
        """
        if not self.reconnect:
            self.start()
            try:
                while self.running and not self._dropped.is_set():
                    time.sleep(0.1)
            finally:
                self.stop()
            return

        self._stopping = False
        if self.advisor is not None:
            self.advisor.start()

        announced = False
        try:
            while not self._stopping:
                if find_pid(self.process_name) is None:
                    if not announced:
                        self._status("waiting")
                        if self.verbose:
                            print(f"  [OFC] waiting for {self.process_name} to start")
                        announced = True
                    time.sleep(self.poll_interval)
                    continue

                announced = False
                try:
                    self.start()
                except Exception as exc:           # noqa: BLE001
                    # A process that is up but not yet attachable — still
                    # loading, or a hook that failed — is worth retrying, not
                    # worth ending the session over.
                    self._status("waiting")
                    if self.verbose:
                        print(f"  [OFC] could not attach ({type(exc).__name__}: {exc}); "
                              "retrying")
                    time.sleep(self.poll_interval)
                    continue

                while self.running and not self._dropped.is_set() and not self._stopping:
                    time.sleep(0.1)

                self._teardown()
                if not self._stopping:
                    self._status("waiting")
        finally:
            self.stop()

    def _teardown(self) -> None:
        """Drop the current session, leaving the loop free to re-attach."""
        self.attached = False
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
        self._dropped.clear()

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
        if self.advisor is None:
            return
        try:
            self.advisor.feed(name, payload.get("tableId", 0), payload.get("data") or {})
        except Exception as exc:                   # noqa: BLE001
            # This runs on Frida's callback thread. Letting an exception out
            # of it risks the whole hook, which is a much larger loss than
            # one packet's worth of state.
            print(f"  [OFC] dropped {name}: {type(exc).__name__}: {exc}")


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
    installed = capture._handle_packet
    # Re-attaching points the existing wrapper at the new advisor rather than
    # stacking another one; silently keeping the old advisor would leave the
    # caller holding one that never receives a packet.
    original = getattr(installed, "_ofc_original", installed)

    def wrapped(payload):
        target = wrapped._ofc_advisor
        name = payload.get("name")
        if target is not None and name in OFC_PACKETS:
            try:
                target.feed(name, payload.get("tableId", 0), payload.get("data") or {})
            except Exception as exc:               # noqa: BLE001
                print(f"  [OFC] advisor failed on {name}: {type(exc).__name__}: {exc}")
        return original(payload)

    wrapped._ofc_original = original
    wrapped._ofc_advisor = advisor
    capture._handle_packet = wrapped
    advisor.start()


def detach_from_capture(capture) -> None:
    """Undo :func:`attach_to_capture`, restoring the original dispatch."""
    installed = getattr(capture, "_handle_packet", None)
    original = getattr(installed, "_ofc_original", None)
    if original is not None:
        capture._handle_packet = original


__all__ = ["OfcCapture", "attach_to_capture", "detach_from_capture", "find_pid",
           "OFC_PACKETS", "HOOK_SCRIPT"]
