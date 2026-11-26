import time
from datetime import datetime, timedelta
from zoneinfo import ZoneInfo

import pytest

from momentum.full_report import FullReport
from momentum.service import Application
from momentum.settings import DEFAULTS

ZONE = "America/Los_Angeles"


def stamp(days):
    return (datetime.now(ZoneInfo(ZONE)) - timedelta(days=days)).strftime("%Y-%m-%d %H:%M:%S")


def row(pid, name="Ann Lee", person="Other Person", last=5, created=10, status="Active"):
    return [pid, name, person, stamp(last) if last is not None else "", "Text", stamp(created), status]


def test_one_entry_per_person_and_both_counts():
    r = FullReport([row("1"), row("1", person="Me"), row("2", person="Me"), row("2"), row(""), row("")], "Me", ZONE)
    out = r.search()
    assert (out["report_rows"], out["people"]) == (6, 4)
    assert {p["id"]: p["mine"] for p in out["rows"] if p["id"]} == {"1": True, "2": True}


def test_search_filters_sort_and_paging():
    r = FullReport([row("1", "Ann Lee", "Me", last=2, created=3), row("2", "Bob Stone", last=40, created=2, status="Lost"), row("3", "Cy Lee", last=None, created=1)], "Me", ZONE)
    ids = lambda **k: [p["id"] for p in r.search(**k)["rows"]]
    assert ids() == ["3", "2", "1"]
    assert ids(query="LEE ann") == ["1"]
    assert ids(query="2") == ["2"]
    assert ids(salesperson="Me") == ["1"] and ids(status="Lost") == ["2"]
    assert ids(min_days=30) == ["2"]  # an unknown date is not "long ago"
    assert ids(sort="last") == ["2", "1", "3"] and ids(sort="name") == ["1", "2", "3"]
    page = r.search(size=2, page=9)
    assert (page["page"], page["pages"], page["matches"], len(page["rows"])) == (2, 2, 3, 1)
    for bad in ({"query": 5}, {"page": "1"}, {"size": True}, {"query": "x" * 201}):
        with pytest.raises(ValueError):
            r.search(**bad)


def test_people_without_a_salesperson_can_be_filtered():
    rows = [["1", "A", "", "", "", "", "Active", []], ["2", "B", "Taylor Lee", "", "", "", "Active", []]]
    report = FullReport(rows, "Me", ZONE)
    assert report.salespeople == ["Taylor Lee", "(No salesperson)"]
    assert [r["name"] for r in report.search(salesperson="(No salesperson)")["rows"]] == ["A"]


def test_the_size_of_the_real_report():
    r = FullReport([row(str(i % 65015), f"Person {i}", "Me" if i % 133 == 0 else "Other") for i in range(65041)], "Me", ZONE)
    started = time.monotonic()
    out = r.search(query="person 64")
    assert time.monotonic() - started < 2
    assert (out["people"], out["report_rows"], len(out["rows"])) == (65015, 65041, 50)


class Crm:
    full_rows = [row("1", person="Me"), row("2")]
    report_counts = {"report_total": 2, "account_rows": 1, "leads": 1}

    def __init__(self, config):
        pass

    def __enter__(self):
        return self

    def __exit__(self, *args):
        pass

    def leads(self):
        return [{"id": "1", "name": "Ann Lee"}]

    def templates(self, leads):
        return [{"id": "t", "name": "T"}]


def app(tmp_path, **extra):
    return Application({**DEFAULTS, "data_dir": str(tmp_path), "account_name": "Me", "timezone": ZONE, **extra}, factory=Crm)


def test_the_full_report_can_be_viewed_but_not_selected(tmp_path):
    a = app(tmp_path)
    assert a.full_report({}) == {"ready": False}
    loaded = a.load("tab")
    assert loaded["counts"]["report_people"] == 2 and [x["id"] for x in loaded["leads"]] == ["1"]
    assert a.full_report({})["people"] == 2
    with pytest.raises(ValueError, match="outside the loaded report"):
        a.preview("tab", ["2"], "t")


def test_an_older_extension_leaves_no_stale_full_report(tmp_path, monkeypatch):
    a = app(tmp_path)
    a.load("tab")
    monkeypatch.setattr(Crm, "full_rows", None)
    a.load("tab")
    assert a.full_report({}) == {"ready": False}


META = {"columns": ["personid", "Lead Source"], "sourceColumns": ["Source Type", "Lead Source"], "momentumTotal": 4}


def sourced(pid, source, **k):
    return row(pid, **k) + [["Internet", source]]


def test_sources_are_combined_counted_and_unioned_per_person():
    r = FullReport([sourced("1", "Capital One"), sourced("2", "KBB", status="Lost"), sourced("3", " kbb"), sourced("3", "Lendward", person="Me"), sourced("4", ""), sourced("4", "")], "Me", ZONE, META)
    out = r.search()
    assert out["source_column"] == "Lead Source" and out["momentum_total"] == out["people"] == 4
    assert [(s["name"], s["count"]) for s in out["sources"]] == [("Capital One", 1), ("KBB", 2), ("Lendward", 1), ("(No source)", 1)]
    ids = lambda *s, **k: sorted(p["id"] for p in r.search(sources=list(s), **k)["rows"])
    assert ids("KBB") == ["2", "3"] and ids("Lendward") == ["3"] and ids("KBB", "Lendward", "Capital One") == ["1", "2", "3"]
    assert ids("(No source)") == ["4"] and ids("KBB", status="Lost") == ["2"] and ids(query="lendward") == ["3"]
    assert {s["name"]: s["count"] for s in r.search(status="Lost", sources=["KBB"])["sources"]}["Capital One"] == 0
    gone = r.search(sources=["Closed Vendor"])
    assert gone["chosen"] == [] and gone["matches"] == 4
    for bad in ("KBB", [1], {"a": 1}):
        with pytest.raises(ValueError):
            r.search(sources=bad)


def test_the_source_column_is_chosen_openly_and_never_invented():
    rows = [sourced("1", "KBB")]
    assert FullReport(rows, "Me", ZONE, META, "Source Type").source_column == "Source Type"
    assert FullReport(rows, "Me", ZONE, META, "Missing").source_column is None
    assert FullReport([sourced("1", "")], "Me", ZONE, META).source_column == "Source Type"  # the better name is empty for everyone
    none = FullReport([row("1")], "Me", ZONE, {"columns": ["b", "A"]}).search()
    assert none["source_column"] is None and none["sources"] == [] and none["columns"] == ["A", "b"]


def test_a_failed_sync_says_the_report_is_from_earlier(tmp_path, monkeypatch):
    from momentum.service import short_error
    app = Application({**DEFAULTS, "data_dir": str(tmp_path)})
    try:
        app.full, app.full_current = FullReport([row("1")], "Me", ZONE), True
        assert app.full_report({})["current"] is True

        def broken(config):
            raise RuntimeError("Frame with ID 0 is showing error page")

        app.factory = broken
        with pytest.raises(RuntimeError) as caught:
            app.load("owner-failed-sync-00")
        assert app.full_report({})["current"] is False
        assert "internet connection" in short_error(caught.value)
    finally:
        app.ledger.close()


def test_everyone_can_be_selected_when_the_scope_is_all(tmp_path):
    a = app(tmp_path, lead_scope="all")
    try:
        a.load("tab")
        assert a.reports["tab"]["ids"] == {"1", "2"}
        assert a.full_report({})["can_select"] is True and sorted(a.full_report({"ids_only": True})["ids"]) == ["1", "2"]
        assert a.full_report({"ids_only": True, "salesperson": "Other Person"})["ids"] == ["2"]
        with pytest.raises(ValueError, match="outside the loaded report"):
            a.preview("tab", ["3"], "t")
        b = app(tmp_path / "mine")
        b.load("tab")
        assert b.full_report({})["can_select"] is False and b.full_report({"ids_only": True})["ids"] == []
        b.ledger.close()
        assert a.verification_key.startswith("test:")
    finally:
        a.ledger.close()


def test_a_very_long_run_sends_the_dashboard_only_part_of_it(tmp_path):
    a = app(tmp_path)
    try:
        batch = a.ledger.create_batch([{"id": str(i), "name": f"P{i}", "email": ""} for i in range(2000)], "t", 1)
        for i in range(1200):
            a.ledger.outcome(batch, str(i), "dry-run", "ok")
        a.active_batch = batch
        snap = a.snapshot()
        assert len(snap["results"]) == 500 and snap["results"][0]["id"] == "1199"
        assert snap["result_counts"] == {"dry-run": 1200, "pending": 800} and len(a.ledger.results(batch)) == 2000
    finally:
        a.ledger.close()
