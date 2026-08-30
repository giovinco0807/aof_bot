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
#: The OFC-only reader that finds its own offsets through IL2CPP metadata
#: instead of a dump. Required on Android, where the shared script's fixed
#: offsets belong to a different compilation entirely; usable on Windows too,
#: which is where it can be checked against a setup known to work.
ANDROID_HOOK = Path(__file__).resolve().parent / "hook_android.js"


def default_hook(device: str) -> Path:
    """The reader to load for this kind of device.

    Local keeps the shared script, because that is the one the AoF bot has
    been running and there is no reason to change what works. Anything else
    is a phone or an emulator, where fixed offsets from the Windows dump are
    not offsets into anything.
    """
    return HOOK_SCRIPT if device in ("", "local") else ANDROID_HOOK

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


#: Where frida-server listens unless told otherwise.
FRIDA_PORT = 27042


def open_device(target: str = "local", timeout: float = 10.0):
    """The Frida device to attach through.

    ==========================  ====================================
    ``local``                   this machine
    ``usb``                     a phone or emulator on the cable
    ``remote``                  frida-server on this machine's port
    ``192.168.1.50``            frida-server on that host, over the
                                network — **no cable**
    ``192.168.1.50:27042``      the same, on a chosen port
    anything else               a Frida device id, for picking
                                between two phones on the cable
    ==========================  ====================================

    The network form is what lets the machine running this sit somewhere
    else entirely — a box in a cupboard, or a server — while the phone is
    the only thing in your hands. Frida's own transport carries it; nothing
    here has to know the difference, because packets arrive decoded either
    way.

    What does know the difference is the hook script, which resolves its
    methods by fixed offsets taken from the Windows build. Connecting to an
    Android device will reach the client and then fail to find what it is
    looking for. That failure is reported, not papered over.
    """
    import frida                                   # noqa: PLC0415

    if target in ("", "local"):
        return frida.get_local_device()
    try:
        if target == "usb":
            return frida.get_usb_device(timeout=timeout)
        if target == "remote":
            return frida.get_remote_device()
        address = _network_address(target)
        if address:
            # add_remote_device, not get_remote_device: the latter only ever
            # returns the default one, so a host given here would be ignored
            # and the attach would silently go somewhere else.
            return frida.get_device_manager().add_remote_device(address)
        return frida.get_device(target, timeout=timeout)
    except Exception as exc:                       # noqa: BLE001
        raise RuntimeError(
            f"no {target} device: {type(exc).__name__}: {exc}\n"
            "  frida-server has to be running on the device (which needs "
            "root).\n"
            "  Over the network it must also be listening on all interfaces\n"
            "  (`frida-server -l 0.0.0.0:27042`) and reachable from here."
        ) from exc


def _network_address(target: str) -> Optional[str]:
    """``host`` or ``host:port`` as an address, or None if it is neither.

    Deliberately narrow. A bare word is a device id — Frida hands those out
    for phones on the cable — and treating one as a hostname would turn a
    typo into a connection attempt against whatever that name resolves to.
    So an address has to look like one: a dotted or numeric host, a bracketed
    IPv6 literal, or anything carrying an explicit port.
    """
    if not target:
        return None
    if target.startswith("["):                     # [::1] or [::1]:27042
        return target if "]:" in target else f"{target}:{FRIDA_PORT}"

    host, colon, port = target.rpartition(":")
    if colon:
        if not port.isdigit() or not host:
            return None
        return f"{host}:{port}"
    if "." in target and not target.endswith("."):
        return f"{target}:{FRIDA_PORT}"
    return None


def find_pid_on(device, process_name: str) -> Optional[int]:
    """The client's pid on a Frida device, or None.

    Matched on the process name, and on Android also on the identifier, since
    what the launcher calls an app and what the process table calls it are
    not the same string.
    """
    wanted = process_name.lower()
    try:
        processes = device.enumerate_processes()
    except Exception as exc:                       # noqa: BLE001
        raise RuntimeError(f"could not list processes on {device}: "
                           f"{type(exc).__name__}: {exc}") from exc
    for process in processes:
        name = (getattr(process, "name", "") or "").lower()
        identifier = ((getattr(process, "parameters", None) or {}).get("identifier")
                      or "").lower()
        if wanted in (name, identifier):
            return process.pid
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
                 hook_script: Optional[Path] = None, verbose: bool = True,
                 reconnect: bool = True, poll_interval: float = 2.0,
                 on_status=None, device: str = "local"):
        self.process_name = process_name
        #: Which Frida device to attach through — see :func:`open_device`.
        self.device = device
        self._device = None
        self.advisor = advisor
        self.hook_script = Path(hook_script) if hook_script else default_hook(device)
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
    def _find(self) -> Optional[int]:
        """The client's pid, wherever this capture is looking for it.

        Raises when the device itself cannot be reached — a phone over the
        network is a link that drops, not a process table that is always
        there. The caller decides whether that ends the session.
        """
        if self.device in ("", "local"):
            return find_pid(self.process_name)
        if self._device is None:
            self._device = open_device(self.device)
        return find_pid_on(self._device, self.process_name)

    def _forget_device(self) -> None:
        """Drop the cached device so the next look re-opens the connection.

        A remote device object outlives the link it was made for. Retrying
        through a dead one fails exactly as often as the first attempt did,
        which turns one dropped connection into a session that never comes
        back.
        """
        self._device = None

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

        if self.device in ("", "local"):
            pid = find_pid(self.process_name)
            if pid is None:
                raise RuntimeError(f"{self.process_name} is not running")
            attach_via = frida
        else:
            self._device = open_device(self.device)
            pid = find_pid_on(self._device, self.process_name)
            if pid is None:
                raise RuntimeError(
                    f"{self.process_name} is not running on the {self.device} "
                    "device")
            attach_via = self._device

        if self.verbose:
            where = "" if self.device in ("", "local") else f" on {self.device}"
            print(f"  [OFC] attaching to {self.process_name} (pid {pid}){where}")

        self._dropped.clear()
        self.session = attach_via.attach(pid)
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
            unreachable = False
            while not self._stopping:
                # Looking for the client can itself fail: over the network the
                # phone goes out of range, changes network, or sleeps. That is
                # a reason to wait and try again, never a reason to end the
                # session — which is what an exception escaping here would do.
                try:
                    pid = self._find()
                except Exception as exc:           # noqa: BLE001
                    self._forget_device()
                    self._status("waiting")
                    if self.verbose and not unreachable:
                        print(f"  [OFC] cannot reach {self.device} "
                              f"({type(exc).__name__}); retrying quietly")
                    unreachable = True
                    announced = True
                    time.sleep(self.poll_interval)
                    continue

                if unreachable:
                    unreachable = False
                    announced = False
                    if self.verbose:
                        print(f"  [OFC] {self.device} is reachable again")

                if pid is None:
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
                    # worth ending the session over. Over the network the
                    # cause may be the link rather than the client, so the
                    # device goes too and the next attempt redials.
                    self._forget_device()
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
        kind = payload.get("type")
        if kind in ("status", "error"):
            # What the reader resolved, and whether it resolved it. Printing
            # this is the whole point of the reader saying it: an attach that
            # found the wrong method looks exactly like one that worked, right
            # up until no packets arrive.
            if self.verbose or kind == "error":
                print(f"  [OFC hook] {payload.get('message', '')}")
            return
        if kind != "packet":
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
           "OFC_PACKETS", "HOOK_SCRIPT", "ANDROID_HOOK", "default_hook"]
