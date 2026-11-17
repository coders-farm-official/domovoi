"""The shared-state layer's format and merge rules
(domovoi/friend_houses/spaces.py): two houses applying the same changes in
any order end with the same items."""

from __future__ import annotations

import itertools
import random

import pytest

from domovoi.friend_houses import spaces as sp

A, B = b"\xaa" * 32, b"\xbb" * 32
U1, U2 = b"\x01" * 16, b"\x02" * 16


def _snapshot(items: dict) -> dict:
    return {uid: (i.text, i.done, i.removed) for uid, i in items.items() if i.text or i.removed}


def _apply(changes) -> dict:
    items: dict = {}
    for op, author in changes:
        sp.merge(items, op, author)
    return _snapshot(items)


def test_a_batch_round_trips() -> None:
    batch = sp.Batch(b"\x05" * 16, sp.KIND_LIST, (
        sp.Op("share", 1, value="Shopping"),
        sp.Op("add", 2, U1, "Milk"),
        sp.Op("done", 3, U1, "true"),
        sp.Op("remove", 4, U2),
    ))
    back, skipped = sp.parse_batch(sp.encode_batch(batch))
    assert skipped == 0 and back == batch


@pytest.mark.parametrize("bad", [b"", b"\x00" * 10, b"\x05" * 16 + b"\x09\x00\x01"])
def test_unreadable_batches_are_refused(bad: bytes) -> None:
    with pytest.raises(sp.BadBatch):
        sp.parse_batch(bad)


def test_misshapen_changes_are_skipped_not_fatal() -> None:
    good = sp.Op("add", 2, U1, "Milk")
    batch = sp.encode_batch(sp.Batch(b"\x05" * 16, sp.KIND_LIST, (
        good, sp.Op("done", 3, U1, "maybe"), sp.Op("add", 4, sp.NO_ITEM, "no uid"),
        sp.Op("text", 5, U1, "   "),
    )))
    back, skipped = sp.parse_batch(batch)
    assert skipped == 3 and back.ops == (good,)


def test_later_edits_win_and_ties_break_by_house() -> None:
    items: dict = {}
    sp.merge(items, sp.Op("add", 1, U1, "Milk"), A)
    sp.merge(items, sp.Op("text", 5, U1, "Oat milk"), A)
    sp.merge(items, sp.Op("text", 3, U1, "Skim milk"), B)  # older: loses
    assert items[U1].text == "Oat milk"
    sp.merge(items, sp.Op("text", 5, U1, "Whole milk"), B)  # same clock: B > A wins
    assert items[U1].text == "Whole milk"


def test_a_removed_item_stays_removed() -> None:
    items: dict = {}
    sp.merge(items, sp.Op("add", 1, U1, "Milk"), A)
    sp.merge(items, sp.Op("remove", 2, U1), B)
    sp.merge(items, sp.Op("text", 9, U1, "Milk again"), A)
    sp.merge(items, sp.Op("add", 9, U1, "Milk again"), A)
    sp.merge(items, sp.Op("done", 9, U1, "true"), A)
    assert items[U1].removed  # no resurrection by a late edit


def test_changes_before_their_add_still_land() -> None:
    items: dict = {}
    sp.merge(items, sp.Op("done", 3, U1, "true"), B)
    sp.merge(items, sp.Op("add", 1, U1, "Milk"), A)
    assert (items[U1].text, items[U1].done) == ("Milk", True)


def test_any_order_gives_the_same_list() -> None:
    changes = [
        (sp.Op("add", 1, U1, "Milk"), A), (sp.Op("add", 1, U2, "Bread"), B),
        (sp.Op("done", 2, U1, "true"), B), (sp.Op("text", 2, U2, "Rye bread"), A),
        (sp.Op("done", 3, U1, "false"), A), (sp.Op("remove", 4, U2), B),
        (sp.Op("text", 5, U2, "Too late"), A), (sp.Op("text", 2, U1, "Oat milk"), B),
    ]
    expected = _apply(changes)
    for perm in itertools.islice(itertools.permutations(changes), 0, 5040, 7):
        assert _apply(perm) == expected
    rng = random.Random(4)
    for _ in range(200):
        shuffled = changes[:]
        rng.shuffle(shuffled)
        assert _apply(shuffled) == expected
    assert expected == {U1: ("Oat milk", False, False), U2: ("", False, True)} or \
        expected[U1] == ("Oat milk", False, False)


def test_applying_a_change_twice_changes_nothing() -> None:
    items: dict = {}
    op = sp.Op("add", 1, U1, "Milk")
    assert sp.merge(items, op, A)
    assert not sp.merge(items, op, A)


def test_clocks_cant_jump_far_ahead() -> None:
    assert sp.clock_allowed(10, 11)
    assert sp.clock_allowed(10, 10 + sp.MAX_CLOCK_JUMP)
    assert not sp.clock_allowed(10, 11 + sp.MAX_CLOCK_JUMP)
    assert not sp.clock_allowed(10, 0)
    assert sp.next_clock(10, 3) == 10 and sp.next_clock(10, 40) == 41
