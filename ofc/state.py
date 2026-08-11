"""Table state, rebuilt from the OFC packet stream.

The Frida hook already decodes every packet the OFC tables send; what it does
not do is remember them. This module is that memory. Feed it the ``Pine*``
packets as they arrive and it maintains, per table, who is sitting where,
what each player has placed, what hero is holding, and — derived, because the
wire never says it outright — which cards hero has thrown away.

Everything it stores is either public at the table or hero's own, so the
picture it builds is exactly the one a human player has, arriving faster and
without transcription errors.

The packet field names and shapes mirror ``hook/packet_capture.py``'s handlers
so both readers of the same wire format stay in step:

    PineSitDownBRC     {player: PinePlayingStatus}
    PineStandUpBRC     {seatId}
    PineRoomStatusBRC  {players: [PinePlayingStatus]}
    PineGameStartBRC   {gameId, dealerSeatId, startInfo: [{seatId, chips}]}
    PineHandCardBRC    {handCards: [{uid, seatId, cards: [int], round, fantasy,
                        actionLeftTime}], actionSeatId}
    PineActionBRC      {uid, seatId, card: PineCard,
                        headCard: [int], middleCard: [int], tailCard: [int]}
    PineResultBRC      {playerResults: [{uid, seatId, name, chips, fantasy,
                        card: PineCard, scores}]}

where the shared shapes are

    PinePlayingStatus  {uid, seatId, name, chips, fantasy, sittingOut,
                        actionLeftTime, ready, card: PineCard}
    PineCard           {headCard: [int], middleCard: [int], tailCard: [int],
                        abandonCard: [int], handCard: [int], bust, round, ...}

``PineCard`` is the important one. It carries a player's **whole** board, and
it rides along on the sit-down, room-status, action and result packets alike.
Preferring it over the loose ``headCard``/``middleCard``/``tailCard`` arrays
means the board is read from the client's own cumulative view rather than
assembled from updates, so a missed packet corrects itself on the next one.
It also carries ``abandonCard``, which is the discard stated outright instead
of inferred.
"""

from dataclasses import dataclass, field
from typing import Dict, List, Optional, Sequence

from .board import Board
from .cards import code_to_text, is_playable, text_to_code, wire_list_to_text
from .solver import OpponentView, SolveRequest, STREET_INITIAL

# Rows are called head/middle/tail on the wire and top/middle/bottom everywhere
# else in this package.
_WIRE_ROWS = (("headCard", "top"), ("middleCard", "middle"), ("tailCard", "bottom"))


def _as_int(value, default: int = 0) -> int:
    """Coerce a wire field to int, falling back rather than raising.

    Everything here comes out of memory reads in the hook, so a field can
    arrive as ``None`` or as a string when a struct offset drifts. Comparing
    one of those against an int raises, and an exception on the Frida thread
    is far more disruptive than a wrong seat number.
    """
    if isinstance(value, bool):
        return int(value)
    if isinstance(value, int):
        return value
    try:
        return int(value)
    except (TypeError, ValueError):
        return default


def _merge_row(existing: Sequence[int], incoming: Sequence[int]) -> List[int]:
    """Combine a row we already know with one the wire just sent.

    If the incoming list contains everything already recorded, it is the full
    row and replaces it. Otherwise it is treated as an addition, and only the
    genuinely new cards are appended. That way the same code is right whether
    the client sends whole rows or only what changed.
    """
    incoming = list(incoming)
    if not existing:
        return incoming
    if set(existing).issubset(incoming):
        return incoming
    return list(existing) + [c for c in incoming if c not in existing]


@dataclass
class Player:
    """One seat, as far as it is publicly visible."""
    seat_id: int
    uid: int = 0
    name: str = ""
    chips: int = 0
    board: Board = field(default_factory=Board)
    in_fantasyland: bool = False

    # Hero only: the wire shows nobody else's hand or discards.
    holding: List[int] = field(default_factory=list)
    discards: List[int] = field(default_factory=list)
    unknown_cards: bool = False

    #: Seconds this seat has left to act, as the table last reported them.
    #: ``None`` when it has not said. The field's unit is not documented, so
    #: nothing relies on it without a plausibility check first.
    action_left: Optional[float] = None

    def reset_hand(self) -> None:
        self.board = Board()
        self.holding = []
        self.discards = []
        self.in_fantasyland = False
        self.unknown_cards = False
        self.action_left = None


@dataclass
class Table:
    """One OFC table, tracked across a hand."""
    table_id: int
    hero_uid: int = 0
    game_id: str = ""
    dealer_seat: int = -1
    street: int = STREET_INITIAL
    action_seat: int = -1
    hand_complete: bool = False
    players: Dict[int, Player] = field(default_factory=dict)

    # ------------------------------------------------------------- lookups
    @property
    def hero(self) -> Optional[Player]:
        if not self.hero_uid:
            return None
        for player in self.players.values():
            if player.uid == self.hero_uid:
                return player
        return None

    @property
    def hero_seat(self) -> int:
        hero = self.hero
        return hero.seat_id if hero else -1

    def opponents(self) -> List[Player]:
        hero_seat = self.hero_seat
        return [p for sid, p in sorted(self.players.items()) if sid != hero_seat]

    def hero_has_decision(self) -> bool:
        """True when hero is holding cards that are not on the board yet.

        This, not :meth:`hero_to_act`, is what advice keys off. Knowing the
        cards is enough to work out the answer, and having it ready before
        the turn arrives is strictly better than having it after.
        """
        hero = self.hero
        return bool(hero and hero.holding)

    def hero_to_act(self) -> bool:
        """True when the table is, as far as can be told, waiting on hero.

        A seat that has not been named is treated as possibly hero's: the
        turn signal is not sent on every packet, and refusing to act on an
        unknown turn would mean never acting at all in a hand where the
        signal was missed.
        """
        hero = self.hero
        if not (hero and hero.holding):
            return False
        return self.action_seat in (hero.seat_id, -1)

    def _next_seat(self, seat_id: int) -> int:
        """The seat that acts after ``seat_id``, or -1 if there is nobody."""
        seats = sorted(self.players)
        if not seats:
            return -1
        for candidate in seats:
            if candidate > seat_id:
                return candidate
        return seats[0]

    def _player(self, seat_id: int) -> Player:
        if seat_id not in self.players:
            self.players[seat_id] = Player(seat_id=seat_id)
        return self.players[seat_id]

    # ------------------------------------------------------- packet intake
    def _absorb_status(self, info: dict) -> None:
        """Take in one ``PinePlayingStatus``, board and all.

        Reading the board here is what makes attaching mid-hand work: the
        status packets restate every seat's placed cards, so a session that
        starts in the middle of a hand reconstructs the table instead of
        believing every board is empty.
        """
        seat_id = _as_int(info.get("seatId"), -1)
        if seat_id < 0:
            return
        uid = _as_int(info.get("uid"), 0)

        player = self.players.get(seat_id)
        if player is None or (uid and player.uid and player.uid != uid):
            # A different player in the seat: start their state clean rather
            # than leaving the previous occupant's cards sitting there.
            player = Player(seat_id=seat_id)
            self.players[seat_id] = player

        player.uid = uid or player.uid
        player.name = info.get("name") or player.name
        player.chips = _as_int(info.get("chips"), player.chips)
        if "fantasy" in info:
            player.in_fantasyland = bool(_as_int(info.get("fantasy"), 0))

        layout = info.get("card") or {}
        if layout:
            self._absorb_pine_card(player, layout)

        left = _as_int(info.get("actionLeftTime"), 0) or _as_int(
            layout.get("actionLeftTime"), 0)
        if left > 0:
            self.action_seat = seat_id
            player.action_left = float(left)

    def _absorb_pine_card(self, player: "Player", layout: dict) -> None:
        """Read a ``PineCard`` onto a player: rows, discards and hand."""
        board = Board()
        saw_row = False
        for wire_row, row_name in _WIRE_ROWS:
            texts = wire_list_to_text(layout.get(wire_row) or ())
            if not texts:
                continue
            saw_row = True
            if not is_playable(texts):
                player.unknown_cards = True
                continue
            setattr(board, row_name, [text_to_code(t) for t in texts])
        if saw_row:
            player.board = board

        # abandonCard is the muck, stated rather than inferred.
        abandoned = wire_list_to_text(layout.get("abandonCard") or ())
        if abandoned and is_playable(abandoned):
            player.discards = [text_to_code(t) for t in abandoned]

        held = wire_list_to_text(layout.get("handCard") or ())
        if held and is_playable(held):
            placed = set(player.board.all_cards())
            outstanding = [text_to_code(t) for t in held
                           if text_to_code(t) not in placed]
            player.holding = outstanding

    def on_sit_down(self, pkt: dict) -> None:
        self._absorb_status(pkt.get("player") or {})

    def on_stand_up(self, pkt: dict) -> None:
        seat_id = _as_int(pkt.get("seatId"), -1)
        self.players.pop(seat_id, None)
        if self.action_seat == seat_id:
            self.action_seat = -1

    def on_room_status(self, pkt: dict) -> None:
        for info in pkt.get("players") or ():
            self._absorb_status(info or {})

    def on_game_start(self, pkt: dict) -> None:
        self.game_id = pkt.get("gameId", "")
        self.dealer_seat = pkt.get("dealerSeatId", -1)
        self.street = STREET_INITIAL
        self.action_seat = -1
        self.hand_complete = False
        for player in self.players.values():
            player.reset_hand()
        for info in pkt.get("startInfo") or ():
            seat_id = info.get("seatId", -1)
            if seat_id in self.players:
                self.players[seat_id].chips = info.get("chips", 0)

    def on_hand_card(self, pkt: dict) -> None:
        """Record a deal.

        Only hero's own entry carries real cards; the others arrive empty
        because the client is not told what anybody else is holding.
        """
        if "actionSeatId" in pkt:
            self.action_seat = _as_int(pkt.get("actionSeatId"), -1)

        for entry in pkt.get("handCards") or ():
            entry = entry or {}
            seat_id = _as_int(entry.get("seatId"), -1)
            if seat_id < 0:
                continue
            player = self._player(seat_id)
            uid = _as_int(entry.get("uid"), 0)
            if uid:
                player.uid = uid
            player.in_fantasyland = bool(_as_int(entry.get("fantasy"), 0))
            left = _as_int(entry.get("actionLeftTime"), 0)
            if left > 0:
                self.action_seat = seat_id
                player.action_left = float(left)

            texts = wire_list_to_text(entry.get("cards") or ())
            if not texts:
                continue
            if not is_playable(texts):
                # A card the decoder does not model — a joker, most likely.
                # Flag it so the advisor refuses to solve rather than guessing.
                player.unknown_cards = True
                continue

            # The street belongs to whoever was actually dealt to. Taking it
            # from the last entry in the packet would let a seat sitting out,
            # or one in Fantasyland with its own round counter, rewrite it.
            self.street = _as_int(entry.get("round"), self.street)

            dealt = [text_to_code(t) for t in texts]
            placed = set(player.board.all_cards())
            outstanding = [c for c in dealt if c not in placed]
            if len(dealt) == 5 and player.board.card_count() >= 13:
                # An opening deal onto a finished board means the start of a
                # new hand was missed. Clear rather than stack the two.
                player.reset_hand()
                outstanding = dealt
            player.holding = outstanding

    def on_action(self, pkt: dict) -> None:
        """Record a placement.

        The packet's ``card`` field is the acting player's whole board as the
        client sees it, so it is preferred; the loose row arrays are a
        fallback for a build that does not send it, and are merged rather
        than assigned because whether they carry the full row or only the
        change is not something the wire makes explicit.
        """
        seat_id = _as_int(pkt.get("seatId"), -1)
        if seat_id < 0:
            return
        player = self._player(seat_id)
        uid = _as_int(pkt.get("uid"), 0)
        if uid:
            player.uid = uid

        before = set(player.board.all_cards())
        layout = pkt.get("card") or {}

        if any(layout.get(wire_row) for wire_row, _ in _WIRE_ROWS):
            self._absorb_pine_card(player, layout)
        else:
            board = Board(list(player.board.top), list(player.board.middle),
                          list(player.board.bottom))
            for wire_row, row_name in _WIRE_ROWS:
                texts = wire_list_to_text(pkt.get(wire_row) or ())
                if not texts:
                    continue
                if not is_playable(texts):
                    player.unknown_cards = True
                    continue
                incoming = [text_to_code(t) for t in texts]
                setattr(board, row_name,
                        _merge_row(getattr(board, row_name), incoming))
            player.board = board

        if player.holding:
            placed = set(player.board.all_cards())
            leftover = [c for c in player.holding if c not in placed]
            landed = [c for c in player.holding if c in placed and c not in before]
            if landed or not leftover:
                # Cards from this hand actually reached the board, so the
                # rest of what was held went in the muck. Recorded only when
                # the wire did not already state it via abandonCard.
                for card in leftover:
                    if card not in player.discards:
                        player.discards.append(card)
                player.holding = []

        # The turn moves on. Left pointing at the seat that just acted, it
        # would never come round to hero in a multi-way hand.
        if self.action_seat == seat_id:
            self.action_seat = self._next_seat(seat_id)

    def on_result(self, pkt: dict) -> None:
        """Record the showdown, where every hand becomes visible."""
        self.hand_complete = True
        self.action_seat = -1
        for entry in pkt.get("playerResults") or ():
            entry = entry or {}
            seat_id = _as_int(entry.get("seatId"), -1)
            if seat_id < 0:
                continue
            player = self._player(seat_id)
            uid = _as_int(entry.get("uid"), 0)
            if uid:
                player.uid = uid
            if entry.get("name"):
                player.name = entry["name"]
            player.chips = _as_int(entry.get("chips"), player.chips)
            player.in_fantasyland = bool(_as_int(entry.get("fantasy"), 0))

            layout = entry.get("card") or {}
            if layout:
                self._absorb_pine_card(player, layout)
            player.holding = []

    # ------------------------------------------------------------- request
    def build_request(self, time_budget=None, seed: int = 0) -> Optional[SolveRequest]:
        """The decision hero currently faces, or ``None`` if there isn't one.

        Returns nothing when hero is unknown or holds nothing, when hero's own
        cards failed to decode, or when the position does not add up — advice
        from a misread board is worse than no advice. An opponent's unreadable
        card does not block hero: it costs some dead-card accuracy, which is
        much cheaper than silence for the rest of the hand.

        ``time_budget`` may be a :class:`~ofc.budget.TimeBudget`, which picks
        the seconds for this street and trims them to whatever the table's own
        clock allows, or a plain number to use as-is.
        """
        hero = self.hero
        if hero is None or not hero.holding or hero.unknown_cards:
            return None

        # The board has to be a size this deal could actually follow. That is
        # what catches attaching to a hand already in progress: the placed
        # cards are only restated by a status packet, so without one hero's
        # board reads as empty and a three-card street would be solved — and
        # under auto-place, clicked — against a position that does not exist.
        held = len(hero.holding)
        placed = hero.board.card_count()
        if hero.in_fantasyland:
            if held < 13 or placed:
                return None
        elif held == 5:
            if placed:                         # an opening deal onto a live board
                return None
        elif held == 3:
            # Boards go 5, 7, 9, 11 and finish at 13; nothing else can be
            # sitting there when three cards arrive. Counting cards rather
            # than trusting the round number keeps this independent of how
            # the client numbers its streets.
            if placed not in (5, 7, 9, 11):
                return None
        else:
            # A short or oversized deal means a packet was misread; the hook
            # cannot tell a dropped element from a genuinely short array.
            return None

        # Every card in view must be distinct, or the position is not real.
        visible = list(hero.board.all_cards()) + list(hero.holding) + list(hero.discards)
        for other in self.players.values():
            if other is not hero:
                visible += other.board.all_cards()
        if len(visible) != len(set(visible)):
            return None

        opponents = [
            OpponentView(seat_id=p.seat_id, name=p.name, board=p.board,
                         in_fantasyland=p.in_fantasyland)
            for p in self.opponents()
        ]

        if time_budget is None:
            seconds = 4.0
        elif hasattr(time_budget, "for_street"):
            seconds = time_budget.for_street(
                street=self.street, in_fantasyland=hero.in_fantasyland,
                action_left=hero.action_left)
        else:
            seconds = float(time_budget)

        return SolveRequest(
            board=hero.board,
            dealt=list(hero.holding),
            street=self.street,
            discards=list(hero.discards),
            opponents=opponents,
            in_fantasyland=hero.in_fantasyland,
            time_budget=seconds,
            seed=seed,
            table_id=self.table_id,
            hero_seat=hero.seat_id,
            action_left=hero.action_left,
        )

    def snapshot(self) -> dict:
        """The whole table in readable form, for the GUI and the log."""
        hero_seat = self.hero_seat
        return {
            "table_id": self.table_id,
            "game_id": self.game_id,
            "street": self.street,
            "dealer_seat": self.dealer_seat,
            "action_seat": self.action_seat,
            "hero_seat": hero_seat,
            "hero_to_act": self.hero_to_act(),
            "hero_has_decision": self.hero_has_decision(),
            "hand_complete": self.hand_complete,
            "players": [
                {
                    "seat_id": p.seat_id,
                    "uid": str(p.uid),
                    "name": p.name,
                    "chips": p.chips,
                    "is_hero": p.seat_id == hero_seat,
                    "in_fantasyland": p.in_fantasyland,
                    "board": p.board.to_texts(),
                    "holding": [code_to_text(c) for c in p.holding],
                    "discards": [code_to_text(c) for c in p.discards],
                }
                for _, p in sorted(self.players.items())
            ],
        }


class Tables:
    """Every OFC table this session has seen, keyed by table id."""

    def __init__(self, hero_uid: int = 0):
        self.hero_uid = hero_uid
        self._tables: Dict[int, Table] = {}

    def get(self, table_id: int) -> Table:
        table = self._tables.get(table_id)
        if table is None:
            table = Table(table_id=table_id, hero_uid=self.hero_uid)
            self._tables[table_id] = table
        return table

    def __iter__(self):
        return iter(self._tables.values())

    def __len__(self) -> int:
        return len(self._tables)


#: Packet name -> the ``Table`` method that consumes it. The advisor and the
#: replay tool both dispatch through this, so they cannot drift apart.
HANDLERS = {
    "PineSitDownBRC": Table.on_sit_down,
    "PineStandUpBRC": Table.on_stand_up,
    "PineRoomStatusBRC": Table.on_room_status,
    "PineGameStartBRC": Table.on_game_start,
    "PineHandCardBRC": Table.on_hand_card,
    "PineActionBRC": Table.on_action,
    "PineResultBRC": Table.on_result,
}


def apply_packet(table: Table, name: str, pkt: dict) -> bool:
    """Feed one packet to a table. False when the packet is not an OFC one."""
    handler = HANDLERS.get(name)
    if handler is None:
        return False
    handler(table, pkt or {})
    return True


__all__ = ["Player", "Table", "Tables", "HANDLERS", "apply_packet"]
