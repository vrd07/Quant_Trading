"""Unit tests for the DOM-probe verdict classifier — no MT5."""
import json

from scripts.check_dom_probe import classify_snapshots, read_snapshot


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


def test_read_snapshot_decodes_utf16le_with_bom(tmp_path):
    # The EA writes UTF-16LE with a BOM (MQL5 FileOpen FILE_TXT, no
    # FILE_ANSI). Regression: probe.read_text() (utf-8 default) raised
    # an uncaught UnicodeDecodeError on the BOM.
    snap = _snap([{"type": 1, "price": 3300.2, "volume": 1.0}])
    p = tmp_path / "mt5_dom_probe.json"
    p.write_bytes(json.dumps(snap).encode("utf-16"))
    assert read_snapshot(p) == snap


def test_read_snapshot_returns_none_on_truncated_file(tmp_path):
    p = tmp_path / "mt5_dom_probe.json"
    p.write_bytes(b'{"ts": "2026.07.16 10:00:00", "sym')
    assert read_snapshot(p) is None


def test_read_snapshot_returns_none_on_missing_file(tmp_path):
    assert read_snapshot(tmp_path / "does_not_exist.json") is None
