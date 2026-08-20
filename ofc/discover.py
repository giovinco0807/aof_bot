"""Find out which UID is yours, by watching one hand.

``--hero-uid`` is required and there was no way to answer it from inside
the bot, which left the first run stuck on a question the packets can
answer exactly.

They can answer it exactly because of an asymmetry in the wire: a
``PineHandCardBRC`` lists every seat, but only hero's entry carries real
cards. The client is never told what anybody else is holding. So the seat
whose ``cards`` array is non-empty is hero — not a guess, not a heuristic,
and not something a table full of players can make ambiguous.

Nothing is placed, solved or recorded here. It watches, prints, and stops
as soon as it knows.
"""

from typing import Callable, Dict, Optional, Tuple

from .cards import wire_list_to_text

#: Packets that name a seat, in the order they usually arrive.
ROSTER_PACKETS = ("PineRoomStatusBRC", "PineSitDownBRC", "PineGameStartBRC",
                  "PineResultBRC")


def _as_int(value, default: int = 0) -> int:
    try:
        return int(value)
    except (TypeError, ValueError):
        return default


class Discoverer:
    """Watches the packet stream and reports hero's UID.

    Shaped like an :class:`~ofc.advisor.Advisor` so far as
    :class:`~ofc.capture.OfcCapture` is concerned: it has ``feed``. That is
    the whole interface the capture uses, so discovery costs no changes
    there.
    """

    def __init__(self, verbose: bool = True,
                 on_hero: Optional[Callable[[int], None]] = None):
        self.verbose = verbose
        self.on_hero = on_hero
        self.hero_uid: Optional[int] = None
        #: (table, seat) -> {"uid": int, "name": str}
        self.seats: Dict[Tuple[int, int], dict] = {}
        self.packets = 0
        self._announced: set = set()

    # ---------------------------------------------------------------- input
    def feed(self, name: str, table_id: int, pkt: dict) -> bool:
        """Consume one packet. Returns False for packets it ignores."""
        self.packets += 1
        if name in ROSTER_PACKETS:
            self._roster(table_id, name, pkt)
            return True
        if name == "PineHandCardBRC":
            self._deal(table_id, pkt)
            return True
        return False

    # -------------------------------------------------------------- roster
    def _roster(self, table_id: int, name: str, pkt: dict) -> None:
        entries = (pkt.get("players") or pkt.get("playerResults")
                   or pkt.get("startInfo") or ())
        if name == "PineSitDownBRC" and pkt.get("player"):
            entries = [pkt["player"]]
        for entry in entries:
            self._note(table_id, entry or {})

    def _note(self, table_id: int, entry: dict) -> None:
        seat_id = _as_int(entry.get("seatId"), -1)
        if seat_id < 0:
            return
        uid = _as_int(entry.get("uid"), 0)
        label = str(entry.get("name") or "")
        seat = self.seats.setdefault((table_id, seat_id), {"uid": 0, "name": ""})
        # Never overwrite something known with nothing: startInfo carries a
        # seat and chips but no uid, and would blank a seat already named.
        if uid:
            seat["uid"] = uid
        if label:
            seat["name"] = label

        key = (table_id, seat_id, seat["uid"], seat["name"])
        if seat["uid"] and key not in self._announced:
            self._announced.add(key)
            if self.verbose:
                shown = seat["name"] or "(no name)"
                print(f"  seat {seat_id}  uid {seat['uid']:<12} {shown}")

    # ---------------------------------------------------------------- deal
    def _deal(self, table_id: int, pkt: dict) -> None:
        """Hero is whoever was dealt cards the client can actually see."""
        for entry in pkt.get("handCards") or ():
            entry = entry or {}
            self._note(table_id, entry)
            if self.hero_uid is not None:
                continue

            cards = entry.get("cards") or ()
            if not cards:
                continue
            uid = _as_int(entry.get("uid"), 0)
            if not uid:
                # The deal is hero's but the entry did not name them; fall
                # back to the seat, which the roster has probably filled in.
                seat = self.seats.get((table_id, _as_int(entry.get("seatId"), -1)))
                uid = seat["uid"] if seat else 0
            if not uid:
                if self.verbose:
                    print("  a deal arrived with cards but no uid — waiting "
                          "for the next hand")
                continue

            self.hero_uid = uid
            if self.verbose:
                texts = wire_list_to_text(cards)
                shown = " ".join(t for t in texts if t) or f"{len(cards)} cards"
                print("")
                print(f"  YOUR UID IS {uid}")
                print(f"  (seat {_as_int(entry.get('seatId'), -1)} was dealt "
                      f"{shown} — nobody else's cards are ever sent to your "
                      "client, so this is definitive)")
            if self.on_hero is not None:
                self.on_hero(uid)


__all__ = ["Discoverer"]
