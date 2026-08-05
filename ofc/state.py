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

    PineSitDownBRC     {player: {uid, seatId, name, chips}}
    PineStandUpBRC     {seatId}
    PineRoomStatusBRC  {players: [{uid, seatId, name, chips}]}
    PineGameStartBRC   {gameId, dealerSeatId, startInfo: [{seatId, chips}]}
    PineHandCardBRC    {handCards: [{uid, seatId, cards: [int], round, fantasy}],
                        actionSeatId}
    PineActionBRC      {uid, seatId, headCard: [int], middleCard: [int],
                        tailCard: [int]}
    PineResultBRC      {playerResults: [{uid, seatId, name, chips, fantasy,
                        card: {headCard, middleCard, tailCard, bust}, scores}]}
"""

from dataclasses import dataclass, field
from typing import Dict, List, Optional

from .board import Board
from .cards import code_to_text, is_playable, text_to_code, wire_list_to_text
from .solver import OpponentView, SolveRequest, STREET_INITIAL

# Rows are called head/middle/tail on the wire and top/middle/bottom everywhere
# else in this package.
_WIRE_ROWS = (("headCard", "top"), ("middleCard", "middle"), ("tailCard", "bottom"))


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

    def reset_hand(self) -> None:
        self.board = Board()
        self.holding = []
        self.discards = []
        self.in_fantasyland = False
        self.unknown_cards = False


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

    def hero_to_act(self) -> bool:
        """True when the table is waiting on hero specifically.

        Cards arriving is not the same as hero being on turn — the deal packet
        fires for every seat — so this insists on both an identified hero and
        the table naming that seat.
        """
        hero = self.hero
        return bool(hero and hero.holding and self.action_seat == hero.seat_id)

    def _player(self, seat_id: int) -> Player:
        if seat_id not in self.players:
            self.players[seat_id] = Player(seat_id=seat_id)
        return self.players[seat_id]

    # ------------------------------------------------------- packet intake
    def on_sit_down(self, pkt: dict) -> None:
        info = pkt.get("player") or {}
        seat_id = info.get("seatId", -1)
        if seat_id < 0:
            return
        player = self._player(seat_id)
        player.uid = info.get("uid", 0)
        player.name = info.get("name", "")
        player.chips = info.get("chips", 0)

    def on_stand_up(self, pkt: dict) -> None:
        self.players.pop(pkt.get("seatId", -1), None)

    def on_room_status(self, pkt: dict) -> None:
        for info in pkt.get("players") or ():
            seat_id = info.get("seatId", -1)
            if seat_id < 0:
                continue
            player = self._player(seat_id)
            player.uid = info.get("uid", 0)
            player.name = info.get("name", "")
            player.chips = info.get("chips", 0)

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
        self.action_seat = pkt.get("actionSeatId", -1)
        for entry in pkt.get("handCards") or ():
            seat_id = entry.get("seatId", -1)
            if seat_id < 0:
                continue
            player = self._player(seat_id)
            if entry.get("uid"):
                player.uid = entry["uid"]
            player.in_fantasyland = bool(entry.get("fantasy", 0))
            self.street = entry.get("round", self.street)

            texts = wire_list_to_text(entry.get("cards") or ())
            if not texts:
                continue
            if not is_playable(texts):
                # A card the decoder does not model — a joker, most likely.
                # Flag it so the advisor refuses to solve rather than guessing.
                player.unknown_cards = True
                continue
            player.holding = [text_to_code(t) for t in texts]

    def on_action(self, pkt: dict) -> None:
        """Record a placement.

        Each of these packets carries a row's full contents rather than the
        change, so rows are replaced, not appended to. For hero, whatever was
        held and did not land on the board is the discard — the wire never
        says so directly, but it follows.
        """
        seat_id = pkt.get("seatId", -1)
        if seat_id < 0:
            return
        player = self._player(seat_id)
        if pkt.get("uid"):
            player.uid = pkt["uid"]

        board = Board()
        board.top = list(player.board.top)
        board.middle = list(player.board.middle)
        board.bottom = list(player.board.bottom)

        for wire_row, row_name in _WIRE_ROWS:
            texts = wire_list_to_text(pkt.get(wire_row) or ())
            if not texts:
                continue
            if not is_playable(texts):
                player.unknown_cards = True
                continue
            setattr(board, row_name, [text_to_code(t) for t in texts])
        player.board = board

        if player.holding:
            placed = set(board.all_cards())
            leftover = [c for c in player.holding if c not in placed]
            # One card per pineapple street goes in the muck; the opening
            # street places all five and leaves nothing behind.
            if len(player.holding) == 3 and len(leftover) == 1:
                player.discards.extend(leftover)
            player.holding = []

    def on_result(self, pkt: dict) -> None:
        """Record the showdown, where every hand becomes visible."""
        self.hand_complete = True
        self.action_seat = -1
        for entry in pkt.get("playerResults") or ():
            seat_id = entry.get("seatId", -1)
            if seat_id < 0:
                continue
            player = self._player(seat_id)
            if entry.get("uid"):
                player.uid = entry["uid"]
            if entry.get("name"):
                player.name = entry["name"]
            player.chips = entry.get("chips", player.chips)
            player.in_fantasyland = bool(entry.get("fantasy", 0))

            layout = entry.get("card") or {}
            board = Board()
            for wire_row, row_name in _WIRE_ROWS:
                texts = wire_list_to_text(layout.get(wire_row) or ())
                if texts and is_playable(texts):
                    setattr(board, row_name, [text_to_code(t) for t in texts])
            if board.card_count():
                player.board = board
            player.holding = []

    # ------------------------------------------------------------- request
    def build_request(self, time_budget: float = 4.0, seed: int = 0) -> Optional[SolveRequest]:
        """The decision hero currently faces, or ``None`` if there isn't one.

        Returns nothing when hero is unknown, is not on turn, holds nothing,
        or when any card in play failed to decode — advice from a misread
        board is worse than no advice.
        """
        hero = self.hero
        if hero is None or not hero.holding:
            return None
        if hero.unknown_cards or any(p.unknown_cards for p in self.players.values()):
            return None

        opponents = [
            OpponentView(seat_id=p.seat_id, name=p.name, board=p.board,
                         in_fantasyland=p.in_fantasyland)
            for p in self.opponents()
        ]
        return SolveRequest(
            board=hero.board,
            dealt=list(hero.holding),
            street=self.street,
            discards=list(hero.discards),
            opponents=opponents,
            in_fantasyland=hero.in_fantasyland,
            time_budget=time_budget,
            seed=seed,
            table_id=self.table_id,
            hero_seat=hero.seat_id,
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
