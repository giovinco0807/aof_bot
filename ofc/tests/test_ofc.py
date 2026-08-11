"""Tests for the OFC package.

    python -m ofc.tests.test_ofc

Plain asserts and no test framework, matching the rest of this repository.
The evaluator tests cross-check against the pineapple project's independent
implementation when it is present, which is the strongest check available:
two implementations written from different starting points agreeing on tens
of thousands of random hands.
"""

import random
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent.parent))

from ofc import evaluator as ev                                    # noqa: E402
from ofc.actions import initial_actions, street_actions            # noqa: E402
from ofc.board import BOTTOM, Board, MIDDLE, TOP                   # noqa: E402
from ofc.cards import (                                            # noqa: E402
    FULL_DECK, code_to_text, text_to_code, texts_to_codes,
    wire_list_to_text, wire_to_text,
)
from ofc.solver import OpponentView, SolveRequest, solve, validate  # noqa: E402
from ofc.state import HANDLERS, Table, apply_packet                          # noqa: E402

PASSED = []
FAILED = []


def check(name: str, condition: bool, detail: str = "") -> None:
    (PASSED if condition else FAILED).append(name)
    mark = "ok  " if condition else "FAIL"
    print(f"  {mark} {name}{('  ' + detail) if detail and not condition else ''}")


def C(*texts):
    return texts_to_codes(texts)


# ------------------------------------------------------------------- cards

def test_cards():
    print("\ncards")
    # PPPoker wire format: (suit << 8) | rank, suits 1=d 2=c 3=h 4=s.
    check("wire decodes a card", wire_to_text((4 << 8) | 14) == "As")
    check("wire decodes a ten", wire_to_text((1 << 8) | 10) == "Td")
    check("wire rejects zero", wire_to_text(0) == "")
    check("wire rejects a joker rank", wire_to_text((4 << 8) | 15) == "")
    check("wire list drops padding", wire_list_to_text([(4 << 8) | 14, 0, 0]) == ["As"])
    check("wire list flags undecodable",
          wire_list_to_text([(9 << 8) | 3]) == ["??"])

    check("code round trip", all(text_to_code(code_to_text(c)) == c for c in FULL_DECK))
    check("codes are unique", len({code_to_text(c) for c in FULL_DECK}) == 52)
    check("rank is code >> 2", text_to_code("As") >> 2 == 12)

    for bad in ("", "A", "Ax", "Zh", "10h", None, 5):
        try:
            text_to_code(bad)
            check(f"rejects {bad!r}", False)
            break
        except (ValueError, AttributeError, TypeError):
            continue
    else:
        check("rejects malformed card text", True)


# --------------------------------------------------------------- evaluator

def test_evaluator():
    print("\nevaluator")
    order = [
        C("2h", "4d", "6c", "8s", "Th"),                 # high card
        C("2h", "2d", "6c", "8s", "Th"),                 # pair
        C("2h", "2d", "6c", "6s", "Th"),                 # two pair
        C("2h", "2d", "2c", "6s", "Th"),                 # trips
        C("5h", "6d", "7c", "8s", "9h"),                 # straight
        C("2h", "5h", "9h", "Jh", "Kh"),                 # flush
        C("2h", "2d", "2c", "6s", "6h"),                 # full house
        C("2h", "2d", "2c", "2s", "6h"),                 # quads
        C("5h", "6h", "7h", "8h", "9h"),                 # straight flush
    ]
    check("categories rank in order",
          all(ev.eval5(order[i]) < ev.eval5(order[i + 1]) for i in range(len(order) - 1)))

    check("wheel is the lowest straight",
          ev.eval5(C("Ah", "2d", "3c", "4s", "5h")) < ev.eval5(C("2h", "3d", "4c", "5s", "6h")))
    check("broadway is the highest straight",
          ev.eval5(C("Ah", "Kd", "Qc", "Js", "Th")) > ev.eval5(C("9h", "Kd", "Qc", "Js", "Th")))

    check("top trips beat top pair", ev.eval3(C("2h", "2d", "2c")) > ev.eval3(C("Ah", "Ad", "Kc")))
    check("top pair ranks by rank", ev.eval3(C("Ah", "Ad", "2c")) > ev.eval3(C("Kh", "Kd", "Qc")))

    # A top row is compared against the middle on the five-card scale, so a
    # higher trips beats a lower one instead of every 5-card trips winning.
    check("top AAA beats middle 222",
          ev.eval3(C("Ah", "Ad", "Ac")) > ev.eval5(C("2h", "2d", "2c", "Ks", "Qh")))
    check("top 222 loses to middle AAA",
          ev.eval3(C("2h", "2d", "2c")) < ev.eval5(C("Ah", "Ad", "Ac", "Ks", "Qh")))
    check("top trips beat middle two pair",
          ev.eval3(C("2h", "2d", "2c")) > ev.eval5(C("Ah", "Ad", "Kc", "Ks", "Qh")))

    royalties = [
        ("66 top", ev.top_royalty(C("6h", "6d", "2c")), 1),
        ("AA top", ev.top_royalty(C("Ah", "Ad", "2c")), 9),
        ("55 top scores nothing", ev.top_royalty(C("5h", "5d", "2c")), 0),
        ("222 top", ev.top_royalty(C("2h", "2d", "2c")), 10),
        ("AAA top", ev.top_royalty(C("Ah", "Ad", "Ac")), 22),
        ("middle trips", ev.middle_royalty(C("9h", "9d", "9c", "2s", "3h")), 2),
        ("middle straight", ev.middle_royalty(C("5h", "6d", "7c", "8s", "9h")), 4),
        ("middle flush", ev.middle_royalty(C("Ah", "Kh", "Qh", "Jh", "9h")), 8),
        ("middle boat", ev.middle_royalty(C("9h", "9d", "9c", "2s", "2h")), 12),
        ("middle quads", ev.middle_royalty(C("9h", "9d", "9c", "9s", "2h")), 20),
        ("middle straight flush", ev.middle_royalty(C("5h", "6h", "7h", "8h", "9h")), 30),
        ("middle royal", ev.middle_royalty(C("Ah", "Kh", "Qh", "Jh", "Th")), 50),
        ("bottom straight", ev.bottom_royalty(C("5h", "6d", "7c", "8s", "9h")), 2),
        ("bottom flush", ev.bottom_royalty(C("Ah", "Kh", "Qh", "Jh", "9h")), 4),
        ("bottom boat", ev.bottom_royalty(C("9h", "9d", "9c", "2s", "2h")), 6),
        ("bottom quads", ev.bottom_royalty(C("9h", "9d", "9c", "9s", "2h")), 10),
        ("bottom steel wheel", ev.bottom_royalty(C("Ah", "2h", "3h", "4h", "5h")), 15),
        ("bottom royal", ev.bottom_royalty(C("Ah", "Kh", "Qh", "Jh", "Th")), 25),
        ("bottom trips score nothing", ev.bottom_royalty(C("9h", "9d", "9c", "2s", "3h")), 0),
    ]
    for name, got, want in royalties:
        check(f"royalty: {name}", got == want, f"got {got} want {want}")

    fl = [
        ("QQ enters fantasyland with 14", ev.fantasyland_entry(C("Qh", "Qd", "2c")), 14),
        ("KK with 15", ev.fantasyland_entry(C("Kh", "Kd", "2c")), 15),
        ("AA with 16", ev.fantasyland_entry(C("Ah", "Ad", "2c")), 16),
        ("trips with 17", ev.fantasyland_entry(C("2h", "2d", "2c")), 17),
        ("JJ does not enter", ev.fantasyland_entry(C("Jh", "Jd", "2c")), 0),
    ]
    for name, got, want in fl:
        check(name, got == want, f"got {got} want {want}")

    check("top trips keep fantasyland",
          ev.fantasyland_stay(C("2h", "2d", "2c"), C("Ah", "Kd", "Qc", "Js", "9h")))
    check("bottom quads keep fantasyland",
          ev.fantasyland_stay(C("2h", "3d", "4c"), C("9h", "9d", "9c", "9s", "2h")))
    check("a bottom flush does not keep fantasyland",
          not ev.fantasyland_stay(C("2h", "3d", "4c"), C("Ah", "Kh", "Qh", "Jh", "9h")))

    check("a legal board does not foul",
          not ev.is_foul(C("2h", "3d", "4c"), C("Kh", "Kd", "Qc", "Qs", "2c"),
                         C("9h", "9d", "9c", "9s", "7c")))
    check("top above middle fouls",
          ev.is_foul(C("Ah", "Ad", "Ac"), C("Kh", "Kd", "Qc", "Qs", "2h"),
                     C("2c", "3d", "4h", "5s", "7c")))
    check("an incomplete board cannot foul yet",
          not ev.is_foul(C("Ah", "Ad"), C("2c", "3d", "4h", "5s", "7c"), []))
    check("a fouled board scores no royalties",
          ev.total_royalty(C("Ah", "Ad", "Ac"), C("2c", "3d", "4h", "5s", "7c"),
                           C("2s", "3s", "4s", "5c", "8d")) == 0)

    # Scoring is symmetric: what one board wins the other loses.
    rng = random.Random(5)
    asymmetric = 0
    for _ in range(400):
        cards = rng.sample(FULL_DECK, 26)
        a = (cards[0:3], cards[3:8], cards[8:13])
        b = (cards[13:16], cards[16:21], cards[21:26])
        if ev.compare_boards(*a, *b) != -ev.compare_boards(*b, *a):
            asymmetric += 1
    check("scoring is symmetric", asymmetric == 0, f"{asymmetric} asymmetric results")


def test_evaluator_against_pineapple():
    """Cross-check against the sibling project, when it is checked out."""
    print("\nevaluator vs pineapple (skipped if the repo is absent)")
    for candidate in (Path("/home/user/pineapple"),
                      Path(__file__).resolve().parents[3] / "pineapple"):
        if (candidate / "game" / "hand_evaluator.py").is_file():
            sys.path.insert(0, str(candidate))
            break
    else:
        print("  -- pineapple not found, skipped")
        return

    try:
        from game.card import Card
        from game.hand_evaluator import evaluate_3_card_hand, evaluate_5_card_hand
        from game.royalty import (get_bottom_royalty, get_middle_royalty, get_top_royalty)
    except ImportError as exc:
        print(f"  -- pineapple import failed ({exc}), skipped")
        return

    from ofc.cards import RANKS, SUITS

    def to_pineapple(codes):
        return [Card(RANKS[c >> 2], SUITS[c & 3]) for c in codes]

    def sign(x):
        return (x > 0) - (x < 0)

    rng = random.Random(7)
    mismatched5 = mismatched3 = 0
    for _ in range(20000):
        a, b = rng.sample(FULL_DECK, 5), rng.sample(FULL_DECK, 5)
        mine = sign(ev.eval5(a) - ev.eval5(b))
        ra, ka = evaluate_5_card_hand(to_pineapple(a))
        rb, kb = evaluate_5_card_hand(to_pineapple(b))
        theirs = sign(((int(ra), tuple(ka)) > (int(rb), tuple(kb)))
                      - ((int(ra), tuple(ka)) < (int(rb), tuple(kb))))
        mismatched5 += mine != theirs

        a, b = rng.sample(FULL_DECK, 3), rng.sample(FULL_DECK, 3)
        mine = sign(ev.eval3(a) - ev.eval3(b))
        ra, ka = evaluate_3_card_hand(to_pineapple(a))
        rb, kb = evaluate_3_card_hand(to_pineapple(b))
        theirs = sign(((int(ra), tuple(ka)) > (int(rb), tuple(kb)))
                      - ((int(ra), tuple(ka)) < (int(rb), tuple(kb))))
        mismatched3 += mine != theirs

    check("5-card ordering matches pineapple", mismatched5 == 0, f"{mismatched5} differ")
    check("3-card ordering matches pineapple", mismatched3 == 0, f"{mismatched3} differ")

    bad = 0
    for _ in range(10000):
        three = rng.sample(FULL_DECK, 3)
        five = rng.sample(FULL_DECK, 5)
        bad += ev.top_royalty(three) != get_top_royalty(to_pineapple(three))
        bad += ev.middle_royalty(five) != get_middle_royalty(to_pineapple(five))
        bad += ev.bottom_royalty(five) != get_bottom_royalty(to_pineapple(five))
    check("royalties match pineapple", bad == 0, f"{bad} differ")


# ----------------------------------------------------------------- actions

def test_actions():
    print("\nactions")
    empty = Board()
    opening = initial_actions(C("As", "Ks", "Qh", "7d", "2c"), empty, prune_fouled=False)
    check("232 opening placements", len(opening) == 232, f"got {len(opening)}")
    boards = {(tuple(sorted(a.apply(empty).top)), tuple(sorted(a.apply(empty).middle)),
               tuple(sorted(a.apply(empty).bottom))) for a in opening}
    check("opening placements are all distinct", len(boards) == 232)

    street = street_actions(C("Qs", "7h", "3c"), empty, prune_fouled=False)
    check("27 placements on an open board", len(street) == 27, f"got {len(street)}")
    check("every street action discards one card",
          all(a.discard is not None and len(a.placements) == 2 for a in street))

    full_top = Board.from_texts(top=["2h", "3d", "4c"], middle=["Ts", "9s", "8h", "7c"],
                                bottom=["As", "Ks"])
    limited = street_actions(C("Qs", "7d", "3s"), full_top, prune_fouled=False)
    check("a full row is never offered",
          all(row != TOP for a in limited for _, row in a.placements))

    # Pruning must drop exactly the fouling lines, no more and no less.
    rng = random.Random(3)
    wrong = 0
    for _ in range(300):
        deck = FULL_DECK[:]
        rng.shuffle(deck)
        board = Board(deck[0:2], deck[2:7], deck[7:11])
        dealt = deck[11:14]
        raw = street_actions(dealt, board, prune_fouled=False)
        kept = set(street_actions(dealt, board, prune_fouled=True))
        for action in raw:
            if action.apply(board).is_foul() != (action not in kept):
                wrong += 1
    check("pruning drops exactly the fouling lines", wrong == 0, f"{wrong} wrong")


# ------------------------------------------------------------------- state

def _wire(text: str) -> int:
    suits = {"d": 1, "c": 2, "h": 3, "s": 4}
    ranks = {"T": 10, "J": 11, "Q": 12, "K": 13, "A": 14}
    rank = ranks.get(text[0], 0) or int(text[0])
    return (suits[text[1]] << 8) | rank


def test_state():
    print("\nstate")
    hero_uid = 1001
    table = Table(table_id=7, hero_uid=hero_uid)
    apply_packet(table, "PineRoomStatusBRC", {"players": [
        {"uid": hero_uid, "seatId": 0, "name": "hero", "chips": 500},
        {"uid": 2002, "seatId": 1, "name": "villain", "chips": 500}]})
    apply_packet(table, "PineGameStartBRC", {
        "gameId": "g1", "dealerSeatId": 0,
        "startInfo": [{"seatId": 0, "chips": 500}, {"seatId": 1, "chips": 500}]})

    check("hero is identified by uid", table.hero_seat == 0)
    check("the other seat is an opponent", [p.name for p in table.opponents()] == ["villain"])

    apply_packet(table, "PineHandCardBRC", {"actionSeatId": 0, "handCards": [
        {"uid": hero_uid, "seatId": 0,
         "cards": [_wire(c) for c in ("As", "Ad", "Kh", "7c", "2d")],
         "round": 0, "fantasy": 0},
        {"uid": 2002, "seatId": 1, "cards": [], "round": 0, "fantasy": 0}]})
    check("hero is on turn", table.hero_to_act())

    request = table.build_request()
    check("a request is built", request is not None)
    check("the deal is carried through",
          request.texts()["dealt"] == ["As", "Ad", "Kh", "7c", "2d"])
    check("dead cards start with hero's own", len(request.dead_cards) == 5)
    check("the deck is the rest of the pack", len(request.deck) == 47)

    apply_packet(table, "PineActionBRC", {
        "uid": hero_uid, "seatId": 0, "headCard": [_wire("2d")],
        "middleCard": [_wire("Kh"), _wire("7c")],
        "tailCard": [_wire("As"), _wire("Ad")]})
    apply_packet(table, "PineActionBRC", {
        "uid": 2002, "seatId": 1, "headCard": [_wire("3c")],
        "middleCard": [_wire("9s"), _wire("9d")],
        "tailCard": [_wire("Qh"), _wire("Jh")]})
    check("hero's board is recorded", table.hero.board.card_count() == 5)
    check("the opponent's board is recorded", table.players[1].board.card_count() == 5)
    check("hero is no longer holding cards", not table.hero.holding)

    apply_packet(table, "PineHandCardBRC", {"actionSeatId": 0, "handCards": [
        {"uid": hero_uid, "seatId": 0,
         "cards": [_wire("Ac"), _wire("5s"), _wire("3h")], "round": 1, "fantasy": 0}]})
    request = table.build_request()
    check("opponent cards count as dead",
          all(c in request.dead_cards for c in table.players[1].board.all_cards()))
    check("the deck shrinks by every visible card", len(request.deck) == 39,
          f"got {len(request.deck)}")

    apply_packet(table, "PineActionBRC", {
        "uid": hero_uid, "seatId": 0, "headCard": [_wire("2d")],
        "middleCard": [_wire("Kh"), _wire("7c"), _wire("5s")],
        "tailCard": [_wire("As"), _wire("Ad"), _wire("Ac")]})
    check("the discard is derived from what was not placed",
          [code_to_text(c) for c in table.hero.discards] == ["3h"])

    # A card the decoder cannot read must stop the solver, not be guessed at.
    blocked = Table(table_id=9, hero_uid=hero_uid)
    apply_packet(blocked, "PineRoomStatusBRC",
                 {"players": [{"uid": hero_uid, "seatId": 0, "name": "hero"}]})
    apply_packet(blocked, "PineHandCardBRC", {"actionSeatId": 0, "handCards": [
        {"uid": hero_uid, "seatId": 0, "cards": [(9 << 8) | 3], "round": 0}]})
    check("an unreadable card blocks the request", blocked.build_request() is None)

    # Nobody's turn means no decision, even with cards in hand.
    idle = Table(table_id=11, hero_uid=hero_uid)
    apply_packet(idle, "PineRoomStatusBRC",
                 {"players": [{"uid": hero_uid, "seatId": 0, "name": "hero"}]})
    apply_packet(idle, "PineHandCardBRC", {"actionSeatId": 1, "handCards": [
        {"uid": hero_uid, "seatId": 0,
         "cards": [_wire(c) for c in ("As", "Ad", "Kh", "7c", "2d")], "round": 0}]})
    check("hero is not on turn when another seat is", not idle.hero_to_act())


# ------------------------------------------------------------------ solver

def test_solver_contract():
    print("\nsolver contract")
    from ofc.solver import Advice, Candidate, available, register

    check("the baseline solver is registered", "baseline" in available())

    request = SolveRequest(board=Board(), dealt=C("As", "Ad", "Kh", "7c", "2d"))
    advice = solve(request, "baseline")
    check("the baseline answers", advice.best is not None)
    check("its answer validates", validate(request, advice.best.action).ok)
    check("candidates come back best first",
          all(advice.candidates[i].ev >= advice.candidates[i + 1].ev
              for i in range(len(advice.candidates) - 1)))

    # A solver that explodes must cost one street of advice, not the session.
    def broken(_request):
        raise RuntimeError("boom")

    register("broken-for-test", broken, replace=True)
    result = solve(request, "broken-for-test")
    check("a raising solver is contained", result.best is None and "boom" in result.note)

    def silent(_request):
        return None

    register("silent-for-test", silent, replace=True)
    check("a solver returning nothing is contained", solve(request, "silent-for-test").best is None)

    # Validation has to catch answers that would become misclicks.
    from ofc.actions import Action

    cheat = Action(((text_to_code("2s"), BOTTOM), (text_to_code("3s"), BOTTOM)),
                   discard=text_to_code("4s"))
    street = SolveRequest(board=Board(), dealt=C("As", "Ad", "Kh"))
    check("a card that was never dealt is rejected",
          not validate(street, cheat).ok)

    overfill = Action(tuple((c, TOP) for c in C("As", "Ad", "Kh", "7c", "2d")))
    opening = SolveRequest(board=Board(), dealt=C("As", "Ad", "Kh", "7c", "2d"))
    check("overfilling a row is rejected", not validate(opening, overfill).ok)

    fouling = Action(
        tuple([(c, TOP) for c in C("Ah", "Ad", "Ac")]
              + [(c, MIDDLE) for c in C("2c", "3d", "4h", "5s", "7c")]
              + [(c, BOTTOM) for c in C("2s", "3s", "4s", "5c", "8d")]))
    fl = SolveRequest(board=Board(), in_fantasyland=True,
                      dealt=C("Ah", "Ad", "Ac", "2c", "3d", "4h", "5s", "7c",
                              "2s", "3s", "4s", "5c", "8d"))
    result = validate(fl, fouling)
    check("a fouling line is a warning, not an error",
          result.ok and result.warnings)


def test_pipeline():
    print("\npipeline")
    rng = random.Random(11)
    errors = 0
    incomplete = 0
    for _ in range(120):
        deck = FULL_DECK[:]
        rng.shuffle(deck)
        cursor = 0
        board = Board()
        request = SolveRequest(board=board, dealt=deck[cursor:cursor + 5])
        cursor += 5
        advice = solve(request, "baseline")
        errors += not validate(request, advice.best.action).ok
        board = advice.best.action.apply(board)
        for street in range(4):
            request = SolveRequest(board=board, dealt=deck[cursor:cursor + 3], street=street + 1)
            cursor += 3
            advice = solve(request, "baseline")
            if advice.best is None:
                errors += 1
                break
            errors += not validate(request, advice.best.action).ok
            board = advice.best.action.apply(board)
        incomplete += not board.is_complete()
    check("120 hands play to completion", incomplete == 0, f"{incomplete} unfinished")
    check("no invalid action in 120 hands", errors == 0, f"{errors} invalid")

    # Every request the pipeline builds must describe a possible position.
    from ofc.replay import synthetic_spots

    inconsistent = 0
    for request in synthetic_spots(300, seed=13, opponents=2):
        seen = (request.board.all_cards() + list(request.dealt)
                + [c for o in request.opponents for c in o.board.all_cards()])
        if len(seen) != len(set(seen)):
            inconsistent += 1
        elif len(request.deck) + len(set(request.dead_cards)) != 52:
            inconsistent += 1
    check("300 generated spots are internally consistent", inconsistent == 0,
          f"{inconsistent} bad")


def test_packet_shapes():
    """The PineCard layout the hook actually sends, not the loose arrays.

    ``PineActionBRC`` carries both a cumulative ``card`` and separate row
    arrays, and the status packets carry a board too. Reading only the loose
    arrays lost cards and made a mid-hand attach believe every board was
    empty, so these pin the behaviour down.
    """
    print("\npacket shapes")
    hero_uid = 1001

    def pine_card(top=(), middle=(), bottom=(), abandon=(), hand=()):
        return {
            "headCard": [_wire(c) for c in top],
            "middleCard": [_wire(c) for c in middle],
            "tailCard": [_wire(c) for c in bottom],
            "abandonCard": [_wire(c) for c in abandon],
            "handCard": [_wire(c) for c in hand],
        }

    # The cumulative card field wins over the row arrays.
    table = Table(table_id=1, hero_uid=hero_uid)
    apply_packet(table, "PineRoomStatusBRC",
                 {"players": [{"uid": hero_uid, "seatId": 0, "name": "hero"}]})
    apply_packet(table, "PineActionBRC", {
        "uid": hero_uid, "seatId": 0,
        "card": pine_card(top=["2d"], middle=["Kh", "7c"], bottom=["As", "Ad"]),
        "middleCard": [_wire("5s")]})
    check("the cumulative card field is preferred over the row arrays",
          table.hero.board.card_count() == 5,
          f"got {table.hero.board}")

    # A row array that only names the new card must extend the row, not
    # replace it — otherwise earlier cards vanish from the board and from
    # the dead-card set.
    delta = Table(table_id=2, hero_uid=hero_uid)
    apply_packet(delta, "PineRoomStatusBRC",
                 {"players": [{"uid": hero_uid, "seatId": 0, "name": "hero"}]})
    apply_packet(delta, "PineActionBRC", {
        "uid": hero_uid, "seatId": 0, "headCard": [_wire("2d")],
        "middleCard": [_wire("Kh"), _wire("7c")],
        "tailCard": [_wire("As"), _wire("Ad")]})
    apply_packet(delta, "PineActionBRC", {
        "uid": hero_uid, "seatId": 0, "middleCard": [_wire("5s")]})
    check("a partial row array extends rather than replaces",
          sorted(code_to_text(c) for c in delta.hero.board.middle) == ["5s", "7c", "Kh"],
          f"got {delta.hero.board}")

    # abandonCard states the discard outright.
    muck = Table(table_id=3, hero_uid=hero_uid)
    apply_packet(muck, "PineRoomStatusBRC",
                 {"players": [{"uid": hero_uid, "seatId": 0, "name": "hero"}]})
    apply_packet(muck, "PineActionBRC", {
        "uid": hero_uid, "seatId": 0,
        "card": pine_card(top=["2d"], middle=["Kh"], bottom=["As"], abandon=["9c"])})
    check("abandonCard is read as the discard",
          [code_to_text(c) for c in muck.hero.discards] == ["9c"])

    # Attaching mid-hand: the status packet restates every board.
    late = Table(table_id=4, hero_uid=hero_uid)
    apply_packet(late, "PineRoomStatusBRC", {"players": [
        {"uid": hero_uid, "seatId": 0, "name": "hero",
         "card": pine_card(top=["2d"], middle=["Kh", "7c"], bottom=["As", "Ad"])},
        {"uid": 2002, "seatId": 1, "name": "villain",
         "card": pine_card(top=["3c"], middle=["9s", "9d"], bottom=["Qh", "Jh"])}]})
    check("a mid-hand attach recovers hero's board",
          late.hero.board.card_count() == 5)
    check("a mid-hand attach recovers the opponent's board",
          late.players[1].board.card_count() == 5)

    apply_packet(late, "PineHandCardBRC", {"actionSeatId": 0, "handCards": [
        {"uid": hero_uid, "seatId": 0,
         "cards": [_wire("Ac"), _wire("5s"), _wire("3h")], "round": 1}]})
    request = late.build_request()
    check("a mid-hand attach counts the recovered cards as dead",
          request is not None and len(request.deck) == 39,
          f"deck {len(request.deck) if request else 'no request'}")

    # Attaching with no status packet at all leaves the board unknown. That
    # must produce silence, not confident advice on a board of nothing.
    blind = Table(table_id=8, hero_uid=hero_uid)
    apply_packet(blind, "PineSitDownBRC",
                 {"player": {"uid": hero_uid, "seatId": 0, "name": "hero"}})
    apply_packet(blind, "PineHandCardBRC", {"actionSeatId": 0, "handCards": [
        {"uid": hero_uid, "seatId": 0,
         "cards": [_wire("Ac"), _wire("5s"), _wire("4s")], "round": 2}]})
    check("a three-card street onto an empty board is refused",
          blind.build_request() is None)

    # Every board size a pineapple street can legitimately follow.
    for count, allowed in ((0, False), (4, False), (5, True), (6, False),
                           (7, True), (9, True), (11, True), (13, False)):
        spot = Table(table_id=9, hero_uid=hero_uid)
        apply_packet(spot, "PineRoomStatusBRC",
                     {"players": [{"uid": hero_uid, "seatId": 0, "name": "hero"}]})
        deck = list(FULL_DECK)
        layout = {"tailCard": [], "middleCard": [], "headCard": []}
        for index in range(count):
            key = ("tailCard" if index < 5 else
                   "middleCard" if index < 10 else "headCard")
            layout[key].append(_wire(code_to_text(deck[index])))
        apply_packet(spot, "PineRoomStatusBRC", {"players": [
            {"uid": hero_uid, "seatId": 0, "name": "hero", "card": layout}]})
        apply_packet(spot, "PineHandCardBRC", {"actionSeatId": 0, "handCards": [
            {"uid": hero_uid, "seatId": 0,
             "cards": [_wire(code_to_text(c)) for c in deck[20:23]], "round": 2}]})
        got = spot.build_request() is not None
        check(f"a three-card street with {count} cards placed is "
              f"{'accepted' if allowed else 'refused'}", got == allowed)


def test_turn_tracking():
    """Hero must get their turn in a multi-way hand."""
    print("\nturn tracking")
    hero_uid = 1001
    table = Table(table_id=5, hero_uid=hero_uid)
    apply_packet(table, "PineRoomStatusBRC", {"players": [
        {"uid": 3003, "seatId": 0, "name": "early"},
        {"uid": hero_uid, "seatId": 1, "name": "hero"},
        {"uid": 2002, "seatId": 2, "name": "late"}]})
    apply_packet(table, "PineGameStartBRC", {"gameId": "g1", "dealerSeatId": 2})

    # Seat 0 acts first; hero is second.
    apply_packet(table, "PineHandCardBRC", {"actionSeatId": 0, "handCards": [
        {"uid": hero_uid, "seatId": 1,
         "cards": [_wire(c) for c in ("As", "Ad", "Kh", "7c", "2d")], "round": 0}]})
    check("hero is not on turn while an earlier seat acts", not table.hero_to_act())
    check("hero still has a decision to prepare", table.hero_has_decision())

    apply_packet(table, "PineActionBRC", {
        "uid": 3003, "seatId": 0, "headCard": [_wire("3c")],
        "middleCard": [_wire("9s"), _wire("9d")],
        "tailCard": [_wire("Qh"), _wire("Jh")]})
    check("the turn advances to hero once the earlier seat acts",
          table.hero_to_act(), f"action_seat is {table.action_seat}")

    # A hand that starts without its game-start packet must not stack boards.
    stale = Table(table_id=6, hero_uid=hero_uid)
    apply_packet(stale, "PineRoomStatusBRC",
                 {"players": [{"uid": hero_uid, "seatId": 0, "name": "hero"}]})
    apply_packet(stale, "PineResultBRC", {"playerResults": [
        {"uid": hero_uid, "seatId": 0, "card": {
            "headCard": [_wire(c) for c in ("2d", "3c", "4h")],
            "middleCard": [_wire(c) for c in ("Kh", "7c", "5s", "8d", "9c")],
            "tailCard": [_wire(c) for c in ("As", "Ad", "Ac", "Ts", "Jd")]}}]})
    check("the finished board is recorded", stale.hero.board.card_count() == 13)
    apply_packet(stale, "PineHandCardBRC", {"actionSeatId": 0, "handCards": [
        {"uid": hero_uid, "seatId": 0,
         "cards": [_wire(c) for c in ("2h", "3d", "4c", "5h", "6d")], "round": 0}]})
    check("an opening deal onto a finished board starts the hand over",
          stale.hero.board.card_count() == 0)
    check("and the request that follows is playable",
          stale.build_request() is not None)


def test_validation_edges():
    print("\nvalidation edges")
    from ofc.actions import Action, actions_for
    from ofc.solver import Advice, Candidate, register

    board = Board()
    street = SolveRequest(board=Board(top=C("2h"), middle=C("Kh", "7c"),
                                      bottom=C("As", "Ad")),
                          dealt=C("Ac", "5s", "3h"))

    bad_row = Action(((text_to_code("Ac"), "Top"), (text_to_code("5s"), MIDDLE)),
                     discard=text_to_code("3h"))
    result = validate(street, bad_row)
    check("an unknown row name is an error, not an exception", not result.ok)

    no_discard = Action(((text_to_code("Ac"), BOTTOM), (text_to_code("5s"), MIDDLE)))
    check("a pineapple street without a discard is rejected",
          not validate(street, no_discard).ok)

    # Fantasyland onto a board that is not empty must not pass validation and
    # then blow up when the action is applied.
    fl = SolveRequest(board=Board(top=C("2s")), in_fantasyland=True,
                      dealt=C("Ah", "Ad", "Ac", "Kh", "Kd", "Ks", "Qh", "Qd",
                              "7s", "8s", "9s", "Ts", "Js"))
    overfull = Action(tuple([(c, TOP) for c in C("Ah", "Ad", "Ac")]
                            + [(c, MIDDLE) for c in C("Kh", "Kd", "Ks", "Qh", "Qd")]
                            + [(c, BOTTOM) for c in C("7s", "8s", "9s", "Ts", "Js")]))
    result = validate(fl, overfull)
    check("fantasyland validation respects the board it is given", not result.ok)

    # A solver returning the wrong type must not escape solve().
    register("wrong-type-for-test", lambda r: [Candidate(action=no_discard)], replace=True)
    result = solve(street, "wrong-type-for-test")
    check("a solver returning the wrong type is contained",
          result.best is None and "expected Advice" in result.note)
    check("an unregistered solver name is contained",
          solve(street, "no-such-solver-anywhere").best is None)

    # Detail keys must never overwrite the placement the caller reads back.
    candidate = Candidate(action=no_discard, ev=3.5,
                          detail={"ev": 0.0, "discard": 1.0, "foul_rate": 0.2})
    data = candidate.to_dict()
    check("a detail key cannot overwrite ev", data["ev"] == 3.5)
    check("a detail key cannot overwrite discard", data["discard"] is None)
    check("the shadowed detail is still reported", data["detail_ev"] == 0.0)
    check("ordinary detail keys pass through", data["foul_rate"] == 0.2)

    # Dead cards are a set: a card seen twice is still one card gone.
    doubled = SolveRequest(
        board=Board(top=C("As")), dealt=C("Kh", "Qd", "7c"),
        opponents=[OpponentView(seat_id=1, board=Board(bottom=C("As", "2c")))])
    check("dead cards are deduplicated",
          len(doubled.dead_cards) + len(doubled.deck) == 52,
          f"{len(doubled.dead_cards)} + {len(doubled.deck)}")

    for size in (0, 1, 2, 4, 6, 13):
        try:
            actions_for(list(range(size)), Board())
            check(f"actions_for rejects a {size}-card deal", False)
            break
        except ValueError:
            continue
    else:
        check("actions_for rejects deals that are not 5 or 3 cards", True)

    # Fantasyland mucks more than one card, and the action has to say so.
    fl_clean = SolveRequest(board=Board(), in_fantasyland=True,
                            dealt=C("Ah", "Ad", "Ac", "Kh", "Kd", "Ks", "Qh", "Qd",
                                    "7s", "8s", "9s", "Ts", "Js", "2c"))
    advice = solve(fl_clean, "baseline")
    check("a fantasyland answer names every mucked card",
          advice.best is not None and len(advice.best.action.mucked) == 1,
          f"mucked {advice.best.action.mucked if advice.best else None}")
    check("and it validates", advice.best is not None and validate(fl_clean, advice.best.action).ok)


def test_board_rules():
    print("\nboard rules")
    fouled = Board.from_texts(top=["Ah", "Ad", "Ac"],
                              middle=["2c", "3d", "4h", "5s", "7c"],
                              bottom=["2s", "3s", "4s", "5c", "8d"])
    check("a fouled board wins no fantasyland", fouled.fantasyland_entry() == 0)
    check("a fouled board keeps no fantasyland", not fouled.fantasyland_stay())

    partial = Board.from_texts(top=["Ah", "Ad", "Ac"])
    check("an unfinished board wins no fantasyland", partial.fantasyland_entry() == 0)
    check("an unfinished board keeps no fantasyland", not partial.fantasyland_stay())

    good = Board.from_texts(top=["Qh", "Qd", "2c"],
                            middle=["Kh", "Kd", "Ks", "3d", "4h"],
                            bottom=["9h", "9d", "9c", "9s", "7c"])
    check("a clean board still wins fantasyland", good.fantasyland_entry() == 14)
    check("bottom quads still keep fantasyland", good.fantasyland_stay())


def test_placer_safety():
    print("\nplacer safety")
    from ofc.placer import Layout, Placer
    from ofc.solver import Advice, Candidate
    from ofc.actions import Action

    layout = Layout(
        rows={TOP: [(10, 10), (20, 10), (30, 10)],
              MIDDLE: [(10, 20), (20, 20), (30, 20), (40, 20), (50, 20)],
              BOTTOM: [(10, 30), (20, 30), (30, 30), (40, 30), (50, 30)]},
        hand=[(10, 40), (20, 40), (30, 40), (40, 40), (50, 40)],
        confirm=(90, 90))
    check("a fully measured layout reports no gaps", layout.missing() == [])
    check("a three-position hand strip is not enough for the opening street",
          Layout(rows=layout.rows, hand=layout.hand[:3]).missing() != [])

    ac, five, three = text_to_code("Ac"), text_to_code("5s"), text_to_code("3h")
    action = Action(((ac, BOTTOM), (five, MIDDLE)), discard=three)
    advice = Advice(candidates=[Candidate(action=action, ev=1.0)])

    placer = Placer(layout, verbose=False)
    check("a disabled placer places nothing",
          placer.execute(advice, hand_order=[ac, five, three]) is False)
    check("a placer with no hand order refuses",
          Placer(layout, verbose=False).execute(advice) is False)

    # Slots must be counted from the first free position, not from zero.
    board = Board(top=C("2h"), middle=C("Kh", "7c"), bottom=C("As", "Ad"))
    moves = placer.plan(advice, hand_order=[ac, five, three], board=board)
    slots = {m["row"]: m["slot"] for m in moves}
    check("a partly filled row is placed at its next free slot",
          slots[BOTTOM] == 2 and slots[MIDDLE] == 2, f"got {slots}")

    # The hand strip is indexed by where the cards are, not where they go.
    sources = {m["card"]: m["from"] for m in moves}
    check("drags pick the card up from its own position on the strip",
          sources["Ac"] == layout.hand[0] and sources["5s"] == layout.hand[1],
          f"got {sources}")

    unknown = placer.plan(advice, hand_order=[five, three], board=Board())
    check("a card missing from the hand order is flagged, not guessed at",
          any(m["unknown_source"] for m in unknown))


def test_advisor():
    print("\nadvisor")
    import queue as _queue
    from ofc.advisor import Advisor

    hero_uid = 1001
    events: "_queue.Queue" = _queue.Queue()
    advisor = Advisor(hero_uid=hero_uid, solver="baseline", event_queue=events,
                      verbose=False)
    advisor.start()

    advisor.feed("PineRoomStatusBRC", 1, {"players": [
        {"uid": hero_uid, "seatId": 0, "name": "hero"},
        {"uid": 2002, "seatId": 1, "name": "villain"}]})
    advisor.feed("PineGameStartBRC", 1, {"gameId": "g1", "dealerSeatId": 0})
    advisor.feed("PineHandCardBRC", 1, {"actionSeatId": 0, "handCards": [
        {"uid": hero_uid, "seatId": 0,
         "cards": [_wire(c) for c in ("As", "Ad", "Kh", "7c", "2d")], "round": 0}]})

    deadline = time.time() + 5
    advice_events = []
    while time.time() < deadline and not advice_events:
        try:
            event = events.get(timeout=0.2)
        except _queue.Empty:
            continue
        if event.get("type") == "ofc_advice":
            advice_events.append(event)
    check("an advice event reaches the queue", bool(advice_events))
    if advice_events:
        payload = advice_events[0]
        check("the advice event carries the request and the answer",
              "request" in payload and payload["advice"].get("best"))
        check("errors and warnings are reported separately",
              "errors" in payload and "warnings" in payload)

    # The same spot must not be solved twice.
    before = advisor.decisions
    advisor.feed("PineHandCardBRC", 1, {"actionSeatId": 0, "handCards": [
        {"uid": hero_uid, "seatId": 0,
         "cards": [_wire(c) for c in ("As", "Ad", "Kh", "7c", "2d")], "round": 0}]})
    time.sleep(0.4)
    check("a repeated packet does not re-solve the same spot",
          advisor.decisions == before, f"{before} -> {advisor.decisions}")

    advisor.stop()
    check("feeding a stopped advisor is refused",
          advisor.feed("PineHandCardBRC", 1, {"handCards": []}) is False)

    # Garbage from the wire must not escape into the caller.
    rough = Advisor(hero_uid=hero_uid, solver="baseline", verbose=False)
    rough.start()
    try:
        for pkt in ({"player": {"seatId": "x", "uid": None}},
                    {"player": None},
                    {"players": [None, {"seatId": 1}]},
                    {"handCards": [{"seatId": 0, "cards": [None, "x"]}]},
                    {"seatId": 0, "headCard": None}):
            for name in HANDLERS:
                rough.feed(name, 1, pkt)
        check("malformed packets do not raise", True)
    except Exception as exc:                       # noqa: BLE001
        check("malformed packets do not raise", False, f"{type(exc).__name__}: {exc}")
    finally:
        rough.stop()


def test_time_budget():
    print("\nthinking time")
    from ofc.budget import STREETS, TimeBudget

    budget = TimeBudget()
    check("the opening street gets the most time",
          budget.for_street(0) > budget.for_street(1),
          f"{budget.for_street(0)} vs {budget.for_street(1)}")
    check("fantasyland gets its own budget",
          budget.for_street(0, in_fantasyland=True) == budget.fantasyland)
    check("an unknown street falls back to the default",
          budget.for_street(99) == budget.default)

    budget.set_street(0, 12.0)
    check("a street can be set", budget.for_street(0) == 12.0)
    check("setting one street leaves the others alone", budget.for_street(1) == 3.0)
    budget.set_street(0, -5)
    check("a budget is never zero or negative", budget.for_street(0) > 0)

    uniform = TimeBudget.uniform(2.5)
    check("a uniform budget is the same everywhere",
          all(uniform.for_street(s) == 2.5 for s in STREETS)
          and uniform.for_street(0, in_fantasyland=True) == 2.5)

    # The table's own clock is the real limit: past it the client places the
    # cards itself, so a longer budget buys nothing.
    clocked = TimeBudget.uniform(20.0)
    clocked.reserve = 2.0
    check("the table clock shortens a longer budget",
          clocked.for_street(1, action_left=10.0) == 8.0,
          f"got {clocked.for_street(1, action_left=10.0)}")
    check("a generous clock does not lengthen the budget",
          clocked.for_street(1, action_left=600.0) == 20.0)
    ignoring = TimeBudget.uniform(20.0)
    ignoring.respect_table_clock = False
    check("ignoring the clock keeps the full budget",
          ignoring.for_street(1, action_left=10.0) == 20.0)

    # An implausible clock reading is not believed — capping to a misread
    # zero would mean never thinking at all.
    for nonsense in (0.0, -1.0, 100000.0):
        if clocked.for_street(1, action_left=nonsense) != 20.0:
            check(f"an implausible clock of {nonsense} is ignored", False)
            break
    else:
        check("an implausible clock reading is ignored", True)
    check("a clock shorter than the reserve still leaves time to think",
          clocked.for_street(1, action_left=1.5) > 0)

    # Round trip through disk.
    import tempfile
    with tempfile.TemporaryDirectory() as folder:
        path = Path(folder) / "budget.json"
        original = TimeBudget()
        original.set_street(0, 9.5)
        original.fantasyland = 42.0
        original.respect_table_clock = False
        original.save(path)
        loaded = TimeBudget.load(path)
        check("a saved budget comes back the same",
              loaded.for_street(0) == 9.5 and loaded.fantasyland == 42.0
              and loaded.respect_table_clock is False)
        (Path(folder) / "junk.json").write_text("not json at all")
        check("a corrupt budget file falls back to the defaults",
              TimeBudget.load(Path(folder) / "junk.json").for_street(0) == 6.0)
        check("a missing budget file falls back to the defaults",
              TimeBudget.load(Path(folder) / "absent.json").for_street(0) == 6.0)

    # The request carries the chosen budget and a deadline a solver can use.
    request = SolveRequest(board=Board(), dealt=C("As", "Ad", "Kh", "7c", "2d"),
                           time_budget=1.0)
    check("a request exposes a deadline", request.deadline > request.created)
    check("and the time left counts down from the budget",
          0 < request.time_left() <= 1.0)

    # A table that reports its clock has the budget trimmed to fit.
    hero_uid = 1001
    table = Table(table_id=12, hero_uid=hero_uid)
    apply_packet(table, "PineRoomStatusBRC",
                 {"players": [{"uid": hero_uid, "seatId": 0, "name": "hero"}]})
    apply_packet(table, "PineGameStartBRC", {"gameId": "g", "dealerSeatId": 0})
    apply_packet(table, "PineHandCardBRC", {"actionSeatId": 0, "handCards": [
        {"uid": hero_uid, "seatId": 0,
         "cards": [_wire(c) for c in ("As", "Ad", "Kh", "7c", "2d")],
         "round": 0, "actionLeftTime": 9}]})
    built = table.build_request(time_budget=TimeBudget.uniform(20.0))
    check("the table's clock reaches the request",
          built is not None and built.action_left == 9.0)
    check("and trims the budget it was given",
          built is not None and built.time_budget == 7.0,
          f"got {built.time_budget if built else None}")

    plain = table.build_request(time_budget=2.0)
    check("a plain number is still accepted as a budget",
          plain is not None and plain.time_budget == 2.0)


def test_gui_picker():
    """The click-to-pick path, when a display is available.

    Skipped where Tk cannot open a window, which is most CI and this
    container; it runs on the machine the bot is actually used from, which
    is the one that matters for a GUI.
    """
    print("\ngui card picker (skipped without a display)")
    try:
        import tkinter
        tkinter.Tk().destroy()
    except Exception as exc:                       # noqa: BLE001
        print(f"  -- no usable Tk ({type(exc).__name__}), skipped")
        return

    from ofc.gui import OfcGui

    gui = OfcGui()
    try:
        gui._clear_manual()

        gui.var_destination.set("dealt")
        for card in ("As", "Ad", "Kh", "7c", "2d"):
            gui._picked(text_to_code(card), False)
        check("picking fills the chosen pile",
              gui.var_dealt.get().split() == ["As", "Ad", "Kh", "7c", "2d"])
        check("the deck count follows what has been picked",
              "deck 47" in gui.var_deck.get(), gui.var_deck.get())

        gui._picked(text_to_code("Kh"), True)
        check("clicking a card already in play takes it back",
              "Kh" not in gui.var_dealt.get() and "deck 48" in gui.var_deck.get())

        gui.var_destination.set("top")
        for card in ("2h", "3h", "4h", "5h"):
            gui._picked(text_to_code(card), False)
        check("a row cannot be filled past its capacity",
              len(gui.var_top.get().split()) == 3, gui.var_top.get())

        gui._board_slot_clicked(BOTTOM)
        check("clicking an empty slot aims the picker at that row",
              gui.var_destination.get() == BOTTOM)
        gui._picked(text_to_code("9s"), False)
        gui._board_card_clicked(BOTTOM, text_to_code("9s"))
        check("clicking a card on the board removes it",
              "9s" not in gui.var_bot.get())

        gui._undo_pick()
        check("undo walks back the last pick",
              len(gui.var_top.get().split()) == 2, gui.var_top.get())

        gui._refresh_destinations()
        check("the destination buttons show how full each pile is",
              "(2/3)" in gui.dest_buttons["top"].cget("text"),
              gui.dest_buttons["top"].cget("text"))

        # A duplicate cannot be produced by picking, but can be typed.
        gui._clear_manual()
        gui.var_top.set("As")
        gui.var_dealt.set("As Kd Qc")
        gui._solve_manual()
        check("a typed duplicate is refused",
              "twice" in gui.log.get("end-2l", "end"))

        gui._clear_manual()
        gui.var_destination.set("dealt")
        for card in ("Ac", "5s", "3h"):
            gui._picked(text_to_code(card), False)
        gui.var_destination.set("middle")
        for card in ("Kh", "7c"):
            gui._picked(text_to_code(card), False)
        gui.var_destination.set("bottom")
        for card in ("As", "Ad"):
            gui._picked(text_to_code(card), False)
        gui.var_destination.set("top")
        gui._picked(text_to_code("2d"), False)
        gui._solve_manual()
        check("a spot built by picking solves", bool(gui.tree.get_children()))

        # A live advice event must still paint after all of that.
        gui.events.put({
            "type": "ofc_advice", "table_id": 7,
            "request": {"street": 1,
                        "board": {"top": ["2d"], "middle": ["Kh", "7c"],
                                  "bottom": ["As", "Ad"]},
                        "dealt": ["Ac", "5s", "3h"], "deck_size": 39},
            "advice": {"best": {"placements": [{"card": "Ac", "row": "bottom"},
                                               {"card": "5s", "row": "middle"}],
                                "discard": "3h", "ev": 2.5},
                       "candidates": [{"placements": [{"card": "Ac", "row": "bottom"}],
                                       "discard": "3h", "ev": 2.5}]},
            "errors": [], "warnings": []})
        gui._drain()
        check("a live advice event renders", bool(gui.tree.get_children()))

        # A malformed event must not stop the pump.
        gui.events.put({"type": "ofc_advice"})
        gui._drain()
        check("a malformed event does not stop the event pump", True)
    finally:
        gui.root.destroy()


def main() -> None:
    print("OFC package tests")
    test_cards()
    test_evaluator()
    test_evaluator_against_pineapple()
    test_actions()
    test_state()
    test_packet_shapes()
    test_turn_tracking()
    test_solver_contract()
    test_validation_edges()
    test_board_rules()
    test_placer_safety()
    test_advisor()
    test_time_budget()
    test_gui_picker()
    test_pipeline()

    print(f"\n{len(PASSED)} passed, {len(FAILED)} failed")
    if FAILED:
        for name in FAILED:
            print(f"  FAILED: {name}")
        sys.exit(1)


if __name__ == "__main__":
    main()
