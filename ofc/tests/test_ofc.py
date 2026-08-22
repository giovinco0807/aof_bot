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


def test_who_is_in_the_hand():
    """Occupying a chair is not the same as contesting the hand.

    This decides whether a hand counts as heads-up, and the only solver worth
    playing is heads-up only — so a spectator counted as an opponent costs
    the whole session.
    """
    print("\nwho is in the hand")
    hero_uid = 1001
    deal = [_wire(c) for c in ("As", "Ad", "Kh", "7c", "2d")]

    def table(seats, start_info=None, deal_hand=True):
        t = Table(table_id=1, hero_uid=hero_uid)
        apply_packet(t, "PineRoomStatusBRC", {"players": seats})
        if start_info is not None:
            apply_packet(t, "PineGameStartBRC",
                         {"gameId": "g", "dealerSeatId": 0, "startInfo": start_info})
        if deal_hand:
            apply_packet(t, "PineHandCardBRC", {"actionSeatId": 0, "handCards": [
                {"uid": hero_uid, "seatId": 0, "cards": deal, "round": 0}]})
        return t

    def third(**extra):
        return [{"uid": hero_uid, "seatId": 0, "name": "hero"},
                {"uid": 2002, "seatId": 1, "name": "villain"},
                dict({"uid": 3003, "seatId": 2, "name": "third"}, **extra)]

    two_in = [{"seatId": 0, "chips": 500}, {"seatId": 1, "chips": 500}]
    three_in = two_in + [{"seatId": 2, "chips": 500}]

    t = table([{"uid": hero_uid, "seatId": 0, "name": "hero"},
               {"uid": 2002, "seatId": 1, "name": "villain"}], two_in)
    check("two players at a three-seat table is heads-up",
          len(t.build_request().opponents) == 1)

    t = table(third(sittingOut=True), two_in)
    check("a seat sitting out is not an opponent",
          len(t.build_request().opponents) == 1)
    check("but it is still a seat at the table", len(t.seated()) == 2)

    t = table(third(), two_in)
    check("a seat missing from startInfo is not an opponent",
          len(t.build_request().opponents) == 1)

    t = table(third(), three_in)
    check("three players actually dealt in is three-handed",
          len(t.build_request().opponents) == 2)

    # Attaching mid-hand, with no game start ever seen.
    t = table(third(sittingOut=True), None)
    check("without a game start, sitting out still excludes",
          len(t.build_request().opponents) == 1)
    t = table(third(), None)
    check("without a game start, an active seat counts",
          len(t.build_request().opponents) == 2)

    # Taking a seat mid-hand does not join that hand.
    def pine_card(top=(), mid=(), bot=()):
        return {"headCard": [_wire(c) for c in top],
                "middleCard": [_wire(c) for c in mid],
                "tailCard": [_wire(c) for c in bot], "abandonCard": []}

    t = table([{"uid": hero_uid, "seatId": 0, "name": "hero"},
               {"uid": 2002, "seatId": 1, "name": "villain"}], two_in)
    apply_packet(t, "PineActionBRC", {
        "uid": hero_uid, "seatId": 0,
        "card": pine_card(top=["2d"], mid=["Kh", "7c"], bot=["As", "Ad"])})
    apply_packet(t, "PineActionBRC", {
        "uid": 2002, "seatId": 1,
        "card": pine_card(top=["3c"], mid=["9s", "9d"], bot=["Qh", "Jh"])})
    apply_packet(t, "PineSitDownBRC",
                 {"player": {"uid": 3003, "seatId": 2, "name": "latecomer"}})
    apply_packet(t, "PineHandCardBRC", {"actionSeatId": 0, "handCards": [
        {"uid": hero_uid, "seatId": 0,
         "cards": [_wire("Ac"), _wire("5s"), _wire("3h")], "round": 1}]})
    check("sitting down mid-hand does not join that hand",
          len(t.build_request().opponents) == 1)

    apply_packet(t, "PineResultBRC", {"playerResults": []})
    apply_packet(t, "PineGameStartBRC",
                 {"gameId": "g2", "dealerSeatId": 1, "startInfo": three_in})
    apply_packet(t, "PineHandCardBRC", {"actionSeatId": 0, "handCards": [
        {"uid": hero_uid, "seatId": 0, "cards": deal, "round": 0}]})
    check("and does join the next one",
          len(t.build_request().opponents) == 2)

    # The snapshot has to carry the distinction so the GUI can show it.
    snapshot = t.snapshot()
    check("the snapshot reports who is in the hand",
          all("in_hand" in p for p in snapshot["players"])
          and snapshot["num_in_hand"] == 3)


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
    import json
    import types
    from ofc import placer as placer_module
    from ofc.placer import Layout, Placer
    from ofc.solver import Advice, Candidate
    from ofc.actions import Action

    layout = Layout(
        window_size=(1064, 970),
        rows={TOP: [(10, 10), (20, 10), (30, 10)],
              MIDDLE: [(10, 20), (20, 20), (30, 20), (40, 20), (50, 20)],
              BOTTOM: [(10, 30), (20, 30), (30, 30), (40, 30), (50, 30)]},
        hands={5: [(10, 40), (20, 40), (30, 40), (40, 40), (50, 40)],
               3: [(15, 40), (25, 40), (35, 40)]},
        confirm=(90, 90))
    check("a fully measured layout reports no gaps", layout.missing() == [],
          str(layout.missing()))
    check("a layout with no three-card strip is incomplete",
          Layout(window_size=(1, 1), rows=layout.rows,
                 hands={5: layout.hands[5]}).missing() != [])
    check("a layout with no window size is incomplete",
          Layout(rows=layout.rows, hands=layout.hands).missing() != [])

    # The three-card strip is measured separately because the client does not
    # draw three cards where it draws five.
    check("each deal size has its own strip",
          layout.strip(5) != layout.strip(3) and len(layout.strip(3)) == 3)

    # Coordinates survive a round trip through disk.
    restored = Layout.from_dict(json.loads(json.dumps(layout.to_dict())))
    check("a saved layout comes back the same",
          restored.strip(3) == layout.strip(3)
          and restored.window_size == layout.window_size
          and restored.rows[TOP] == layout.rows[TOP])

    ac, five, three = text_to_code("Ac"), text_to_code("5s"), text_to_code("3h")
    action = Action(((ac, BOTTOM), (five, MIDDLE)), discard=three)
    advice = Advice(candidates=[Candidate(action=action, ev=1.0)])
    board = Board(top=C("2h"), middle=C("Kh", "7c"), bottom=C("As", "Ad"))
    request = SolveRequest(board=board, dealt=[ac, five, three], street=1,
                           opponents=[OpponentView(seat_id=1, board=Board())])

    placer = Placer(layout, verbose=False)
    check("a disabled placer places nothing",
          placer.execute(advice, request) is False)
    check("a placer with no request refuses",
          Placer(layout, verbose=False).execute(advice, None) is False)

    # Slots must be counted from the first free position, not from zero.
    moves = placer.plan(advice, hand_order=[ac, five, three], board=board)
    slots = {m["row"]: m["slot"] for m in moves}
    check("a partly filled row is placed at its next free slot",
          slots[BOTTOM] == 2 and slots[MIDDLE] == 2, f"got {slots}")

    # The hand strip is indexed by where the cards are, not where they go,
    # and a three-card deal uses the three-card strip.
    sources = {m["card"]: m["from"] for m in moves}
    check("drags pick the card up from its own position on the strip",
          sources["Ac"] == layout.strip(3)[0] and sources["5s"] == layout.strip(3)[1],
          f"got {sources}")

    opening = SolveRequest(board=Board(), dealt=C("As", "Ad", "Kh", "7c", "2d"),
                           opponents=[OpponentView(seat_id=1, board=Board())])
    five_card = placer.plan(solve(opening, "baseline"), list(opening.dealt), Board())
    check("a five-card deal uses the five-card strip",
          {m["from"] for m in five_card} <= set(layout.strip(5)),
          str([m["from"] for m in five_card]))

    # Drags go rightmost-strip-card first, so a strip that closes up as cards
    # leave it does not shift the positions still to be used.
    indices = [m["strip_index"] for m in five_card]
    check("drags run from the right of the strip inwards",
          indices == sorted(indices, reverse=True), str(indices))

    unknown = placer.plan(advice, hand_order=[five, three], board=Board())
    check("a card missing from the hand order is flagged, not guessed at",
          any(m["unknown_source"] for m in unknown))

    # Fantasyland is refused outright rather than failing per-card on a strip
    # that was never measured for thirteen.
    fl_request = SolveRequest(
        board=Board(), in_fantasyland=True,
        dealt=C("As", "Ad", "Ac", "Kh", "Kd", "Ks", "Qh", "Qd", "7s", "8s",
                "9s", "Ts", "Js"),
        opponents=[OpponentView(seat_id=1, board=Board())])
    fl_placer = Placer(layout, verbose=False, dry_run=True)
    fl_placer.enabled = True
    check("fantasyland is refused", fl_placer.execute(advice, fl_request) is False)

    # A resized window means the stored points describe a different layout.
    stale = Placer(Layout(window_size=(800, 600), rows=layout.rows,
                          hands=layout.hands), verbose=False)
    stale.enabled = True
    check("a layout measured at another window size is caught",
          any("recalibrate" in p or "not found" in p
              for p in stale.window_problems() or ["not found"]))

    # A drag has to land on the client and nowhere else. Both halves of that
    # — the point being inside the window, and the window actually being in
    # front — are checked here without a real win32, because the machine that
    # runs these tests is not the machine that plays.
    check("a point inside the client is inside",
          placer._in_window((500, 400), (0, 0, 1000, 800)))
    check("a point past the right edge is outside",
          not placer._in_window((1200, 400), (0, 0, 1000, 800)))
    check("a point above the top edge is outside",
          not placer._in_window((500, 50), (0, 100, 1000, 800)))
    check("bounds follow the window when it moves",
          placer._in_window((1100, 400), (900, 100, 1000, 800)))

    # execute() reaches its own checks only past the platform ones, which on
    # anything but Windows refuse first. Standing those two down leaves the
    # part that is the same everywhere.
    def _armed(stub_rect, focus_reason=None):
        armed = Placer(layout, verbose=False)
        armed.enabled = True
        armed.input_problems = lambda: []
        armed.window_problems = lambda: []
        armed._focus = lambda: focus_reason
        armed.drags = []
        armed.drag = lambda start, end, steps=24: (armed.drags.append((start, end))
                                                   or True)
        armed._controller = types.SimpleNamespace(tap=lambda *a, **k: None)
        placer_module.client_rect = lambda title="PPPoker": stub_rect
        return armed

    real_client_rect = placer_module.client_rect
    try:
        # A window too short for the stored coordinates: the hand strip at
        # y=40 falls below the bottom edge, so every drag would start on
        # whatever is behind the client.
        cramped = _armed((0, 0, 1064, 35))
        check("a drag that would land outside the client is refused",
              cramped.execute(advice, request) is False)
        check("and no drag is attempted at all", cramped.drags == [],
              str(cramped.drags))

        # Windows refuses foreground steals silently, so a refusal has to
        # stop the placement rather than be dragged through.
        covered = _armed((0, 0, 1064, 970), focus_reason="the client would not "
                         "come to the front")
        check("a client that will not come forward is refused",
              covered.execute(advice, request) is False)
        check("and nothing is dragged across whatever covers it",
              covered.drags == [], str(covered.drags))

        # With both satisfied, the same position does place.
        clear = _armed((0, 0, 1064, 970))
        check("a focused client with in-bounds points does place",
              clear.execute(advice, request) is True)
        check("every planned card was dragged", len(clear.drags) == 2,
              str(clear.drags))
    finally:
        placer_module.client_rect = real_client_rect

    # A dry run plans without needing the mouse, so a calibration can be
    # reviewed from anywhere; it must still refuse to report success.
    dry = Placer(layout, verbose=False, dry_run=True)
    dry.enabled = True
    check("a dry run needs only the layout", dry.readiness() == [],
          str(dry.readiness()))
    check("and still reports that nothing was placed",
          dry.execute(advice, request) is False)
    check("an uncalibrated dry run is still refused",
          Placer(Layout(), verbose=False, dry_run=True).readiness() != [])

    # The advice has to reach the placer at all — the wiring that carries the
    # request alongside it is the part that was broken.
    from ofc.advisor import Advisor

    seen = []
    advisor = Advisor(hero_uid=1001, solver="baseline", verbose=False, record=False,
                      on_advice=lambda a, r: seen.append((a, r)))
    advisor.start()
    try:
        advisor.feed("PineRoomStatusBRC", 1, {"players": [
            {"uid": 1001, "seatId": 0, "name": "hero"},
            {"uid": 2002, "seatId": 1, "name": "villain"}]})
        advisor.feed("PineGameStartBRC", 1, {
            "gameId": "g", "dealerSeatId": 0,
            "startInfo": [{"seatId": 0}, {"seatId": 1}]})
        advisor.feed("PineHandCardBRC", 1, {"actionSeatId": 0, "handCards": [
            {"uid": 1001, "seatId": 0,
             "cards": [_wire(c) for c in ("As", "Ad", "Kh", "7c", "2d")],
             "round": 0}]})
        time.sleep(0.6)
    finally:
        advisor.stop()

    check("a decision reaches the placement callback", len(seen) == 1)
    if seen:
        advice_out, request_out = seen[0]
        check("and it arrives with the position it belongs to",
              request_out is not None and len(request_out.dealt) == 5)
        plan = placer.plan(advice_out, list(request_out.dealt), request_out.board)
        check("which is enough to plan every drag",
              len(plan) == 5 and all(m["from"] and m["to"] for m in plan))


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


def test_m3_engine():
    """The pineapple m3 engine plug-in, when it is available.

    Skipped unless the regular-OFC project is checked out and built — set
    OFC_REGULAR_ROOT. The decoding test below runs either way, because it is
    pure arithmetic on the engine's key format and a change there would
    otherwise only show up as silently missing candidates.
    """
    print("\nm3 engine plug-in")
    from ofc.solvers.m3engine import M3Engine, decode_action_key

    # The action-key format: rak1:<top>:<middle>:<bottom>:<discard>, four
    # 52-bit masks, bit = suit*13 + rank over suits "hdcs", ranks "2".."A".
    def mask(*texts):
        value = 0
        for t in texts:
            value |= 1 << ("hdcs".index(t[1]) * 13 + "23456789TJQKA".index(t[0]))
        return f"{value:013x}"

    token = f"rak1:{mask()}:{mask()}:{mask('Ac', '5s')}:{mask('3h')}"
    action = decode_action_key(token)
    check("an action key decodes to the right placements",
          action is not None
          and sorted((code_to_text(c), r) for c, r in action.placements)
              == [("5s", BOTTOM), ("Ac", BOTTOM)],
          str(action))
    check("an action key decodes the discard",
          action is not None and action.mucked == (text_to_code("3h"),))

    spread = f"rak1:{mask('Kh')}:{mask('As', 'Ad')}:{mask('7c', '2d')}:{mask()}"
    action = decode_action_key(spread)
    check("a key spanning every row decodes",
          action is not None and len(action.placements) == 5 and not action.mucked)

    for bad in ("", "rak1:x", "nope:0:0:0:0", "rak1:0:0:0:0",
                f"rak1:{mask()}:{mask()}:{mask()}"):
        if decode_action_key(bad) is not None:
            check(f"a malformed key {bad[:18]!r} is rejected", False)
            break
    else:
        check("malformed action keys are rejected rather than raising", True)

    engine = M3Engine()
    if engine.load() is None:
        print(f"  -- engine unavailable, live tests skipped ({engine.error})")
        return

    def spot(street, hero, opponent, dealt, discards=()):
        return SolveRequest(
            board=hero, dealt=texts_to_codes(dealt), street=street,
            discards=texts_to_codes(discards),
            opponents=[OpponentView(seat_id=1, name="v", board=opponent)])

    live = [
        ("T0 first", spot(0, Board(), Board(), ["As", "Ad", "Kh", "7c", "2d"])),
        ("T0 second", spot(0, Board(),
                           Board.from_texts(top=["2h"], middle=["9s", "8s"],
                                            bottom=["Qc", "Jc"]),
                           ["As", "Ad", "Kh", "7c", "2d"])),
        ("T1 first", spot(1, Board.from_texts(top=["2d"], middle=["Kh", "7c"],
                                              bottom=["As", "Ad"]),
                          Board.from_texts(top=["3c"], middle=["9s", "9d"],
                                           bottom=["Qh", "Jh"]),
                          ["Ac", "5s", "3h"])),
        ("T2 first", spot(2, Board.from_texts(top=["2d"], middle=["Kh", "7c", "5s"],
                                              bottom=["As", "Ad", "Ac"]),
                          Board.from_texts(top=["3c"], middle=["9s", "9d", "8h"],
                                           bottom=["Qh", "Jh", "Th"]),
                          ["Kd", "6c", "4h"], ["2c"])),
        ("T3 first", spot(3, Board.from_texts(top=["2d", "4c"],
                                              middle=["Kh", "7c", "5s", "6d"],
                                              bottom=["As", "Ad", "Ac"]),
                          Board.from_texts(top=["3c", "5h"],
                                           middle=["9s", "9d", "8h", "7h"],
                                           bottom=["Qh", "Jh", "Th"]),
                          ["Qs", "8d", "3d"], ["2c", "4d"])),
    ]
    for label, request in live:
        advice = solve(request, "m3")
        if advice.best is None:
            check(f"{label} is answered", False, advice.note)
            continue
        result = validate(request, advice.best.action)
        check(f"{label} is answered and legal", result.ok,
              f"{advice.note} / {result.errors}")
        # Latency is reported rather than asserted: it depends on the machine,
        # and pinning this box's numbers as a correctness bound would make the
        # suite fail on a slower one for no good reason. The ceiling below is
        # only a "something is badly wrong" guard.
        print(f"       {label}: {advice.elapsed * 1000:6.0f} ms, "
              f"{len(advice.candidates)} candidate(s)")
        check(f"{label} finishes at all", advice.elapsed < 60.0,
              f"{advice.elapsed:.1f}s")

    # A ranked street must return the whole fan, not a single pick.
    ranked = solve(live[2][1], "m3")
    check("a ranked street returns every legal candidate",
          len(ranked.candidates) == len(live[2][1].legal_actions()),
          f"{len(ranked.candidates)} vs {len(live[2][1].legal_actions())}")
    check("and ranks them best first",
          all(ranked.candidates[i].ev >= ranked.candidates[i + 1].ev
              for i in range(len(ranked.candidates) - 1)))

    # Positions the engine cannot serve must decline with a reason, never
    # raise and never answer wrongly.
    refusals = [
        ("three-handed", SolveRequest(
            board=Board(), dealt=texts_to_codes(["As", "Ad", "Kh", "7c", "2d"]),
            opponents=[OpponentView(seat_id=1, board=Board()),
                       OpponentView(seat_id=2, board=Board())])),
        ("no opponent", SolveRequest(
            board=Board(), dealt=texts_to_codes(["As", "Ad", "Kh", "7c", "2d"]))),
        ("fantasyland", SolveRequest(
            board=Board(), in_fantasyland=True,
            dealt=texts_to_codes(["As", "Ad", "Kh", "7c", "2d", "3c", "4d", "5h",
                                  "6s", "7d", "8c", "9h", "Ts"]),
            opponents=[OpponentView(seat_id=1, board=Board())])),
        ("board does not match the street", spot(
            1, Board.from_texts(top=["2d"], middle=["Kh"], bottom=["As"]),
            Board.from_texts(top=["3c"], middle=["9s"], bottom=["Qh"]),
            ["Ac", "5s", "3h"])),
        ("hero discards missing", spot(
            2, Board.from_texts(top=["2d"], middle=["Kh", "7c", "5s"],
                                bottom=["As", "Ad", "Ac"]),
            Board.from_texts(top=["3c"], middle=["9s", "9d", "8h"],
                             bottom=["Qh", "Jh", "Th"]),
            ["Kd", "6c", "4h"])),
    ]
    for label, request in refusals:
        advice = solve(request, "m3")
        check(f"{label} is declined with a reason",
              advice.best is None and bool(advice.note), advice.note)


def test_engine_identity():
    """A study log has to say which opponent graded it.

    The engine project moves its own weight pin as models are promoted, so
    "the m3 engine" is not one thing over time. Every decision records which
    build answered it, and an older database gains that column without
    losing what it already holds.
    """
    print("\nengine identity")
    import sqlite3
    import tempfile
    from ofc.actions import Action
    from ofc.recorder import Recorder, SCHEMA
    from ofc.solver import Advice, Candidate
    from ofc.solvers.m3engine import M3Engine

    # The fingerprint stands for the whole set, so any slot changing changes it.
    base = {"t2_second": ("t2_model_v2.bin", "aaaa"),
            "t4": ("t4_model_v6.bin", "bbbb")}
    moved = {"t2_second": ("t2_model_v3x16.bin", "cccc"),
             "t4": ("t4_model_v6.bin", "bbbb")}
    renamed = {"t2_second": ("renamed.bin", "aaaa"),
               "t4": ("t4_model_v6.bin", "bbbb")}

    check("a weight set has a fingerprint", M3Engine._fingerprint(base) != "")
    check("the same set fingerprints the same",
          M3Engine._fingerprint(base) == M3Engine._fingerprint(dict(base)))
    check("a promoted model changes it",
          M3Engine._fingerprint(base) != M3Engine._fingerprint(moved))
    # Content, not filename: a renamed file is the same opponent.
    check("a renamed but identical file does not",
          M3Engine._fingerprint(base) == M3Engine._fingerprint(renamed))
    check("no weights means no fingerprint", M3Engine._fingerprint({}) == "")

    # An older database predates the column. Opening it must add the column
    # and keep every row already in the file.
    older = Path(tempfile.mkdtemp()) / "old.db"
    stripped = SCHEMA
    for line in SCHEMA.splitlines(keepends=True):
        if "engine" in line:
            stripped = stripped.replace(line, "")
    connection = sqlite3.connect(older)
    connection.executescript(stripped)
    connection.execute("INSERT INTO decisions (recorded, solver, note)"
                       " VALUES ('2026-01-01', 'old', 'kept')")
    connection.commit()
    had_column = any(row[1] == "engine"
                     for row in connection.execute("PRAGMA table_info(decisions)"))
    connection.close()
    check("the older database really lacks the column", not had_column)

    request = SolveRequest(board=Board(), dealt=C("As", "Ad", "Kh", "7c", "2d"),
                           opponents=[OpponentView(seat_id=1, board=Board())])
    action = Action(((text_to_code("As"), BOTTOM),))

    recorder = Recorder(db_path=older, verbose=False)
    recorder.start()
    try:
        for fingerprint in ("m3:aaaaaaaaaaaa", "m3:bbbbbbbbbbbb"):
            recorder.record_decision(
                request,
                Advice(candidates=[Candidate(action=action, ev=1.0)],
                       solver="m3", engine=fingerprint),
                {"game_id": "g"})
    finally:
        recorder.stop()          # joins the writer, so this flushes

    connection = sqlite3.connect(older)
    columns = [row[1] for row in connection.execute("PRAGMA table_info(decisions)")]
    rows = connection.execute("SELECT solver, engine, note FROM decisions"
                              " ORDER BY id").fetchall()
    counted = connection.execute(
        "SELECT engine, COUNT(*) FROM decisions"
        " WHERE engine IS NOT NULL AND engine != '' GROUP BY engine").fetchall()
    connection.close()

    check("opening it adds the column", "engine" in columns)
    check("and the rows already there survive",
          rows and rows[0][2] == "kept", str(rows[:1]))
    check("new decisions carry the fingerprint", len(counted) == 2, str(counted))
    check("so a log can be split by which build graded it",
          {c for _, c in counted} == {1}, str(counted))

    # Re-opening must not double-apply.
    again = Recorder(db_path=older, verbose=False)
    again.start()
    again.stop()
    connection = sqlite3.connect(older)
    total = connection.execute("SELECT COUNT(*) FROM decisions").fetchone()[0]
    connection.close()
    check("re-opening changes nothing", total == 3, f"{total} rows")


def test_recorder():
    """Every hand is written down, including the ones no solver will touch."""
    print("\nrecording")
    import json
    import sqlite3
    import tempfile
    from ofc.advisor import Advisor
    from ofc.recorder import Recorder, mistakes, summarise

    hero_uid = 1001

    def pine_card(top=(), mid=(), bot=()):
        return {"headCard": [_wire(c) for c in top],
                "middleCard": [_wire(c) for c in mid],
                "tailCard": [_wire(c) for c in bot], "abandonCard": []}

    with tempfile.TemporaryDirectory() as folder:
        db = Path(folder) / "ofc.db"
        recorder = Recorder(db_path=db, verbose=False)
        advisor = Advisor(hero_uid=hero_uid, solver="baseline", verbose=False,
                          recorder=recorder)
        advisor.start()

        def play(table_id, seats, start_info):
            advisor.feed("PineRoomStatusBRC", table_id, {"players": seats})
            advisor.feed("PineGameStartBRC", table_id, {
                "gameId": f"g{table_id}", "dealerSeatId": 0, "startInfo": start_info})
            advisor.feed("PineHandCardBRC", table_id, {"actionSeatId": 0, "handCards": [
                {"uid": hero_uid, "seatId": 0,
                 "cards": [_wire(c) for c in ("As", "Ad", "Kh", "7c", "2d")],
                 "round": 0}]})
            time.sleep(0.5)
            advisor.feed("PineActionBRC", table_id, {
                "uid": hero_uid, "seatId": 0,
                "card": pine_card(top=["2d"], mid=["Kh", "7c"], bot=["As", "Ad"])})
            time.sleep(0.2)
            advisor.feed("PineResultBRC", table_id, {"playerResults": [
                {"uid": hero_uid, "seatId": 0, "name": "hero", "card": pine_card(
                    top=["2d", "3d", "4d"], mid=["Kh", "7c", "5s", "8d", "9c"],
                    bot=["As", "Ad", "Ac", "Ts", "Jd"])}]})
            time.sleep(0.3)

        two = [{"uid": hero_uid, "seatId": 0, "name": "hero"},
               {"uid": 2002, "seatId": 1, "name": "villain"}]
        in2 = [{"seatId": 0, "chips": 500}, {"seatId": 1, "chips": 500}]
        play(1, two, in2)
        play(2, two + [{"uid": 3003, "seatId": 2, "name": "third"}],
             in2 + [{"seatId": 2, "chips": 500}])

        advisor.stop()
        time.sleep(0.4)

        connection = sqlite3.connect(str(db))
        try:
            by_seats = dict(connection.execute(
                "SELECT seats, COUNT(*) FROM decisions GROUP BY seats"))
            check("a heads-up decision is recorded", by_seats.get(2) == 1, str(by_seats))
            check("a three-handed decision is recorded too",
                  by_seats.get(3) == 1, str(by_seats))

            hands = dict(connection.execute(
                "SELECT seats, COUNT(*) FROM hands GROUP BY seats"))
            check("both hands are recorded", hands.get(2) == 1 and hands.get(3) == 1,
                  str(hands))

            played, rank, loss = connection.execute(
                "SELECT played, played_rank, ev_loss FROM decisions"
                " WHERE seats = 2").fetchone()
            check("hero's actual placement is recorded", played is not None)
            if played:
                moves = json.loads(played)["placements"]
                check("and it is the placement hero really made",
                      sorted((m["card"], m["row"]) for m in moves)
                      == sorted([("2d", TOP), ("Kh", MIDDLE), ("7c", MIDDLE),
                                 ("As", BOTTOM), ("Ad", BOTTOM)]))
            check("it is graded against the ranking", rank is not None and loss is not None,
                  f"rank={rank} loss={loss}")

        finally:
            connection.close()

        report = summarise(db)
        check("the summary splits by table size",
              set(report["by_table_size"]) == {"2", "3"},
              str(report["by_table_size"]))
        check("the summary counts the hands", report["hands"]["recorded"] == 2)
        check("the costliest decisions can be listed", isinstance(mistakes(db), list))

    # A solver that declines has to leave its reason in the record, or a spot
    # with no advice is indistinguishable from one nobody looked at.
    from ofc.solver import Advice as _Advice, register as _register

    _register("declines-for-test",
              lambda r: _Advice(solver="declines-for-test",
                                note="not playing this one"), replace=True)
    with tempfile.TemporaryDirectory() as folder:
        db = Path(folder) / "ofc.db"
        recorder = Recorder(db_path=db, verbose=False)
        advisor = Advisor(hero_uid=hero_uid, solver="declines-for-test",
                          verbose=False, recorder=recorder)
        advisor.start()
        advisor.feed("PineRoomStatusBRC", 5, {"players": [
            {"uid": hero_uid, "seatId": 0, "name": "hero"},
            {"uid": 2002, "seatId": 1, "name": "villain"}]})
        advisor.feed("PineGameStartBRC", 5, {
            "gameId": "g5", "dealerSeatId": 0,
            "startInfo": [{"seatId": 0}, {"seatId": 1}]})
        advisor.feed("PineHandCardBRC", 5, {"actionSeatId": 0, "handCards": [
            {"uid": hero_uid, "seatId": 0,
             "cards": [_wire(c) for c in ("As", "Ad", "Kh", "7c", "2d")],
             "round": 0}]})
        time.sleep(0.5)
        advisor.stop()
        time.sleep(0.3)

        connection = sqlite3.connect(str(db))
        try:
            note, candidates = connection.execute(
                "SELECT note, candidates FROM decisions").fetchone()
            check("a declined spot is still recorded", candidates == "[]")
            check("and the record says why", note == "not playing this one", repr(note))
        finally:
            connection.close()

    check("a missing database summarises without raising",
          "error" in summarise(Path(folder) / "gone.db"))


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

        # Following a live hand: the window has to keep up between hero's
        # turns, not only when advice arrives.
        from ofc.advisor import Advisor

        gui._clear_manual()
        hero_uid = 1001
        advisor = Advisor(hero_uid=hero_uid, solver="baseline",
                          event_queue=gui.events, verbose=False)
        advisor.start()

        def pine_card(top=(), mid=(), bot=()):
            return {"headCard": [_wire(c) for c in top],
                    "middleCard": [_wire(c) for c in mid],
                    "tailCard": [_wire(c) for c in bot], "abandonCard": []}

        def settle():
            time.sleep(0.3)
            gui._drain()
            gui.root.update_idletasks()

        try:
            advisor.feed("PineRoomStatusBRC", 7, {"players": [
                {"uid": hero_uid, "seatId": 0, "name": "hero"},
                {"uid": 2002, "seatId": 1, "name": "villain"}]})
            advisor.feed("PineGameStartBRC", 7, {"gameId": "g1", "dealerSeatId": 0})
            settle()
            check("a seated opponent appears on screen", len(gui.opp_canvases) == 1)

            # The opponent acting is not hero's turn, so nothing would be
            # rendered at all if the window only followed advice.
            advisor.feed("PineActionBRC", 7, {
                "uid": 2002, "seatId": 1,
                "card": pine_card(top=["3c"], mid=["9s", "9d"], bot=["Qh", "Jh"])})
            settle()
            check("an opponent's placement shows without any advice",
                  bool(gui.var_opp.get()), repr(gui.var_opp.get()))

            advisor.feed("PineHandCardBRC", 7, {"actionSeatId": 0, "handCards": [
                {"uid": hero_uid, "seatId": 0,
                 "cards": [_wire(c) for c in ("As", "Ad", "Kh", "7c", "2d")],
                 "round": 0}]})
            settle()
            check("hero's deal reaches the window",
                  gui.var_dealt.get().split() == ["As", "Ad", "Kh", "7c", "2d"],
                  repr(gui.var_dealt.get()))

            advisor.feed("PineActionBRC", 7, {
                "uid": hero_uid, "seatId": 0,
                "card": pine_card(top=["2d"], mid=["Kh", "7c"], bot=["As", "Ad"])})
            settle()
            check("hero's own placement follows",
                  (gui.var_top.get(), gui.var_mid.get(), gui.var_bot.get())
                  == ("2d", "Kh 7c", "As Ad"))
            check("and the hand empties once placed", gui.var_dealt.get() == "")
        finally:
            advisor.stop()

        for state in ("waiting for the client…", "attached — following the table"):
            gui.events.put({"type": "ofc_status", "state": state})
            gui._drain()
            check(f"the status label shows {state.split()[0]!r}",
                  gui.var_status.get() == state)
    finally:
        gui.root.destroy()


def test_uid_discovery():
    """Hero's UID is answerable from the wire, and must be answered exactly.

    Only hero's entry in a deal carries cards — the client is never told
    what anyone else holds. That asymmetry is the whole mechanism, so the
    tests that matter are the ones where a weaker rule would pick wrong.
    """
    print("\nuid discovery")
    from ofc.discover import Discoverer

    def wire(text):
        suits = {"d": 1, "c": 2, "h": 3, "s": 4}
        ranks = {"T": 10, "J": 11, "Q": 12, "K": 13, "A": 14}
        rank = ranks.get(text[0], int(text[0]) if text[0].isdigit() else 0)
        return (suits[text[1]] << 8) | rank

    opening = [wire(t) for t in ("As", "Ad", "Kh", "7c", "2d")]

    # Three seats, one deal: the seat holding cards is hero. Seat order and
    # who acts first are both irrelevant, and neither is consulted.
    found = Discoverer(verbose=False)
    found.feed("PineRoomStatusBRC", 7, {"players": [
        {"uid": 111, "seatId": 0, "name": "alice"},
        {"uid": 222, "seatId": 1, "name": "bob"},
        {"uid": 333, "seatId": 2, "name": "carol"}]})
    found.feed("PineHandCardBRC", 7, {"actionSeatId": 0, "handCards": [
        {"uid": 111, "seatId": 0, "cards": []},
        {"uid": 222, "seatId": 1, "cards": opening},
        {"uid": 333, "seatId": 2, "cards": []}]})
    check("hero is the seat that was dealt real cards", found.hero_uid == 222,
          f"got {found.hero_uid}")

    # A deal that names the seat but not the uid still resolves, because the
    # roster already carries it.
    by_seat = Discoverer(verbose=False)
    by_seat.feed("PineRoomStatusBRC", 7, {"players": [
        {"uid": 999, "seatId": 3, "name": "me"}]})
    by_seat.feed("PineHandCardBRC", 7, {"handCards": [
        {"seatId": 3, "cards": opening}]})
    check("a deal with no uid falls back to the seat", by_seat.hero_uid == 999,
          f"got {by_seat.hero_uid}")

    # PineGameStartBRC's startInfo has seats and chips but no uid. Letting it
    # write through would blank a seat the roster had already named.
    kept = Discoverer(verbose=False)
    kept.feed("PineRoomStatusBRC", 7, {"players": [
        {"uid": 555, "seatId": 0, "name": "x"}]})
    kept.feed("PineGameStartBRC", 7, {"gameId": "g",
                                      "startInfo": [{"seatId": 0, "chips": 100}]})
    check("a uid-less packet does not blank a known seat",
          kept.seats[(7, 0)]["uid"] == 555, str(kept.seats))

    # Nobody dealt means nobody identified. Guessing here would attach the
    # bot to the wrong seat for the whole session.
    empty = Discoverer(verbose=False)
    empty.feed("PineHandCardBRC", 7, {"handCards": [
        {"uid": 1, "seatId": 0, "cards": []},
        {"uid": 2, "seatId": 1, "cards": []}]})
    check("a deal nobody can see does not identify anyone",
          empty.hero_uid is None, f"got {empty.hero_uid}")

    # Later streets deal to hero too; the first answer stands.
    stable = Discoverer(verbose=False)
    stable.feed("PineHandCardBRC", 7, {"handCards": [
        {"uid": 42, "seatId": 0, "cards": opening}]})
    stable.feed("PineHandCardBRC", 7, {"handCards": [
        {"uid": 77, "seatId": 1,
         "cards": [wire(t) for t in ("2c", "3c", "4c")]}]})
    check("a later deal does not overwrite the answer", stable.hero_uid == 42,
          f"got {stable.hero_uid}")

    # OfcCapture calls advisor.feed(name, table, pkt) and nothing else, so
    # that signature is the whole contract discovery has to satisfy.
    check("unrelated packets are ignored",
          stable.feed("PineActionBRC", 7, {}) is False)


def main() -> None:
    print("OFC package tests")
    test_cards()
    test_evaluator()
    test_evaluator_against_pineapple()
    test_actions()
    test_state()
    test_packet_shapes()
    test_turn_tracking()
    test_who_is_in_the_hand()
    test_solver_contract()
    test_validation_edges()
    test_board_rules()
    test_placer_safety()
    test_advisor()
    test_m3_engine()
    test_engine_identity()
    test_recorder()
    test_time_budget()
    test_uid_discovery()
    test_gui_picker()
    test_pipeline()

    print(f"\n{len(PASSED)} passed, {len(FAILED)} failed")
    if FAILED:
        for name in FAILED:
            print(f"  FAILED: {name}")
        sys.exit(1)


if __name__ == "__main__":
    main()
