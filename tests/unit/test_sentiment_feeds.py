"""Unit tests for the sentiment feed layer — the REAL GLD holdings (ETF flow)
parser and its proxy fallback. All offline: a synthetic SPDR-shaped XLSX is built
in-memory and the network is monkeypatched, so no State Street call is made."""
import io
import zipfile

import pytest

from src.sentiment import feeds

_NS = 'xmlns="http://schemas.openxmlformats.org/spreadsheetml/2006/main"'


def _xlsx(col_i_rows):
    """Build a minimal SPDR-shaped .xlsx. ``col_i_rows`` is a list of
    (value, is_string) for column I, row 1..N — mirroring the real file where
    row 1 is a header string and 'US Holiday' rows carry a string in col I."""
    rows = []
    for i, (val, is_str) in enumerate(col_i_rows, start=1):
        if is_str:
            rows.append(f'<row r="{i}"><c r="I{i}" t="s"><v>{val}</v></c></row>')
        else:
            rows.append(f'<row r="{i}"><c r="I{i}"><v>{val}</v></c></row>')
    sheet = f'<worksheet {_NS}><sheetData>{"".join(rows)}</sheetData></worksheet>'
    # A small disclaimer sheet + the larger data sheet: the parser must pick the
    # bigger one, exactly as it does with the real two-sheet workbook.
    cover = f'<worksheet {_NS}><sheetData><row r="1"><c r="A1" t="s"><v>0</v></c></row></sheetData></worksheet>'
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w") as z:
        z.writestr("xl/sharedStrings.xml",
                   f'<sst {_NS} uniqueCount="1"><si><t>US Holiday</t></si></sst>')
        z.writestr("xl/worksheets/sheet1.xml", cover)
        z.writestr("xl/worksheets/sheet2.xml", sheet)
    return buf.getvalue()


def test_parser_extracts_ounces_skipping_header_and_holidays():
    xlsx = _xlsx([
        (0, True),         # row 1: header (shared string) → skip
        (1000.0, False),
        (1002.0, False),
        (0, True),         # holiday (shared string 'US Holiday') → skip
        (1005.0, False),
        (1010.0, False),
    ])
    assert feeds._parse_gld_ounces_series(xlsx) == [1000.0, 1002.0, 1005.0, 1010.0]


def test_parser_is_failsafe_on_garbage_bytes():
    assert feeds._parse_gld_ounces_series(b"not a zip file") == []
    assert feeds._parse_gld_ounces_series(b"") == []


@pytest.mark.parametrize("ounces,expected", [
    ([1000.0, 1000.0, 1000.0, 1010.0], "inflow"),    # +1.0%  → real creation
    ([1010.0, 1005.0, 1002.0, 1000.0], "outflow"),   # -0.99% → real redemption
    ([1000.0, 1000.0, 1000.0, 1001.0], "flat"),      # +0.1%  → inside the band
])
def test_real_tonnes_flow_label(monkeypatch, ounces, expected):
    xlsx = _xlsx([(0, True)] + [(v, False) for v in ounces])
    monkeypatch.setattr(feeds, "_get_bytes", lambda *a, **k: xlsx)
    assert feeds._fetch_gld_tonnes_flow_raw() == expected


def test_short_series_returns_none_not_a_direction():
    assert feeds._flow_label([1000.0, 1001.0]) is None       # <4 points
    assert feeds._flow_label([0.0, 0.0, 0.0, 5.0]) is None   # zero baseline


def test_prefers_real_tonnes_over_proxy(monkeypatch):
    """When holdings are reachable, the real feed wins and is labeled 'tonnes'."""
    xlsx = _xlsx([(0, True)] + [(v, False) for v in (1000.0, 1000.0, 1000.0, 1010.0)])
    monkeypatch.setattr(feeds, "_get_bytes", lambda *a, **k: xlsx)
    monkeypatch.setattr(feeds, "_fetch_etf_flow_raw", lambda: "outflow")  # proxy disagrees
    monkeypatch.setattr(feeds, "_cached", lambda key, ttl, prod: prod())  # bypass disk cache
    assert feeds.fetch_etf_flow_3d() == "inflow"
    assert feeds.fetch_etf_flow_source() == "tonnes"


def test_falls_back_to_proxy_when_holdings_unreachable(monkeypatch):
    """State Street down → use the GLD-price proxy, labeled 'proxy' (honest)."""
    monkeypatch.setattr(feeds, "_get_bytes", lambda *a, **k: None)
    monkeypatch.setattr(feeds, "_fetch_etf_flow_raw", lambda: "inflow")
    monkeypatch.setattr(feeds, "_cached", lambda key, ttl, prod: prod())
    assert feeds.fetch_etf_flow_3d() == "inflow"
    assert feeds.fetch_etf_flow_source() == "proxy"


def test_both_sources_down_is_none(monkeypatch):
    monkeypatch.setattr(feeds, "_get_bytes", lambda *a, **k: None)
    monkeypatch.setattr(feeds, "_fetch_etf_flow_raw", lambda: None)
    monkeypatch.setattr(feeds, "_cached", lambda key, ttl, prod: prod())
    assert feeds.fetch_etf_flow_3d() is None
    assert feeds.fetch_etf_flow_source() is None
