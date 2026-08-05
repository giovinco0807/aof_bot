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
from ofc.state import Table, apply_packet                          # noqa: E402

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


def main() -> None:
    print("OFC package tests")
    test_cards()
    test_evaluator()
    test_evaluator_against_pineapple()
    test_actions()
    test_state()
    test_solver_contract()
    test_pipeline()

    print(f"\n{len(PASSED)} passed, {len(FAILED)} failed")
    if FAILED:
        for name in FAILED:
            print(f"  FAILED: {name}")
        sys.exit(1)


if __name__ == "__main__":
    main()
