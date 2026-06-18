"""Route-table contract (Prompt 1.4).

The router split must NOT change any route. This test asserts the live route
table (method, path) matches the baseline captured BEFORE the split. If the
split adds/removes/renames any route, this fails.
"""
import json
import pathlib

_BASELINE = pathlib.Path(__file__).parent / "_route_baseline.json"


def _live_routes():
    from app.main import app
    return sorted([m, r.path] for r in app.routes
                  for m in (getattr(r, "methods", None) or {"WS"}))


def test_route_table_matches_baseline():
    assert _BASELINE.exists(), "route baseline missing — capture it before the split"
    expected = json.loads(_BASELINE.read_text())
    actual = _live_routes()
    # Compare as sets of (method, path) so ordering never matters.
    exp = {tuple(x) for x in expected}
    act = {tuple(x) for x in actual}
    missing = exp - act
    added = act - exp
    assert not missing and not added, (
        f"route table changed.\n  missing (were in baseline): {sorted(missing)}\n"
        f"  added (new, not in baseline): {sorted(added)}"
    )
