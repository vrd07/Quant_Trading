"""Unit tests for the DOM-probe verdict classifier — no MT5."""
from scripts.check_dom_probe import classify_snapshots


def _snap(levels):
    return {"ts": "2026.07.16 10:00:00", "symbol": "XAUUSDs", "levels": levels}


def test_no_book_when_levels_always_empty():
    assert classify_snapshots([_snap([]) for _ in range(10)]) == "NO BOOK"


def test_no_book_when_no_snapshots():
    assert classify_snapshots([]) == "NO BOOK"


def test_top_of_book_when_two_static_levels():
    lv = [{"type": 1, "price": 3300.2, "volume": 1.0},
          {"type": 2, "price": 3300.0, "volume": 1.0}]
    assert classify_snapshots([_snap(lv) for _ in range(10)]) == "TOP-OF-BOOK ONLY"


def test_real_depth_when_many_levels_changing():
    snaps = []
    for i in range(10):
        snaps.append(_snap([
            {"type": 1, "price": 3300.2 + 0.1 * j, "volume": 1.0 + i + j}
            for j in range(5)
        ] + [
            {"type": 2, "price": 3300.0 - 0.1 * j, "volume": 2.0 + i}
            for j in range(5)
        ]))
    assert classify_snapshots(snaps) == "REAL DEPTH"
