"""The calendar against the real old calendar (orthocal.info, cached on disk after the first run)."""

import calendar
import datetime as dt

import pytest

from reader import library

lib = library.Library()


@pytest.mark.parametrize("year,month", [(y, m) for y in (2026, 2027, 2028) for m in range(1, 13)])
def test_every_civil_month_is_complete(year, month):
    """Each civil month has every day, though it is stitched from two old-style months
    (and in January from two years)."""
    days = lib.civil_month(year, month)
    assert sorted(days) == list(range(1, calendar.monthrange(year, month)[1] + 1))
    for day, data in days.items():
        civil = dt.date(year, month, day)
        assert dt.date(data["year"], data["month"], data["day"]) + dt.timedelta(days=13) == civil
        assert library.summary(data)["fast"] in ("none", "relaxed", "strict")


def test_known_days():
    """Fixed feasts land on their new-style dates; Wednesdays and Fridays are fasts."""
    def info(y, m, d):
        return library.summary(lib.civil_month(y, m)[d])
    assert info(2027, 1, 7)["feast"] == "great"          # Nativity, Dec 25 old style
    assert info(2027, 1, 19)["feast"] == "great"         # Theophany, Jan 6 old style
    assert info(2026, 10, 14)["feast"] == "great"        # Protection, Oct 1 old style
    assert info(2026, 9, 27)["feast"] == "great"         # Exaltation of the Cross, Sep 14 old style
    assert info(2026, 9, 27)["fast"] != "none"           # the Exaltation is a fast day (wine and oil)
    assert info(2026, 10, 30)["fast"] != "none"          # a Friday
    assert info(2026, 11, 28)["fast"] != "none"          # the Nativity Fast begins
    assert info(2027, 1, 7)["fast"] == "none"            # no fast on the Nativity


def test_pascha_2027_and_lent():
    """Pascha 2027 is May 2 (Julian Apr 19): no fast in Bright Week; Great Lent before it."""
    may = lib.civil_month(2027, 5)
    assert "Pascha" in " ".join(may[2].get("titles") or []) or may[2].get("pascha_distance") == 0
    assert library.summary(may[7])["fast"] == "none"      # Bright Friday is fast-free
    assert library.summary(lib.civil_month(2027, 4)[7])["fast"] != "none"  # a Wednesday in Lent


def test_scripture_spans_read_lectionary_citations():
    assert library.scripture_spans("Luke 5.1-11") == ("Luke", [(5, 1, 5, 11)])
    assert library.scripture_spans("Mark 6.54-7.8") == ("Mark", [(6, 54, 7, 8)])
    assert library.scripture_spans("2 Corinthians 11.31-12.9") == ("2 Corinthians", [(11, 31, 12, 9)])
    assert library.scripture_spans("") is None


def test_commemorations_list_every_name_once():
    """Feasts, then saints. A repeated name is kept once. A Sunday title is not a saint."""
    assert library.commemorations({
        "feasts": ["Ven. Chariton the Confessor", ""],
        "saints": ["Ven. Chariton the Confessor", "Holy Prophet Baruch"],
        "titles": ["19th Sunday after Pentecost"],
    }) == ["Ven. Chariton the Confessor", "Holy Prophet Baruch"]


def test_october_11_names_everyone_and_opens_the_gospels():
    """Civil 11 October 2026: Chariton, Baruch, and Vatslav, and each Gospel can be read."""
    civil = dt.date(2026, 10, 11)
    data = lib.day(civil, fetch=False)
    assert data is not None
    names = library.commemorations(data)
    assert names == [
        "Ven. Chariton the Confessor",
        "Holy Prophet Baruch (6th c. BC)",
        "Holy Martyr Vatslav (Wenceslas), King of the Czechs (929)",
    ]
    assert "19th Sunday after Pentecost" not in names
    gospels = [r for r in library.reading_links(data) if r["gospel"]]
    assert [r["display"] for r in gospels] == ["John 20.11-18", "Luke 5.1-11", "Luke 6.17-23"]
    holy = lib.passage_page(civil, next(r["i"] for r in gospels if r["display"] == "Luke 5.1-11"))
    assert holy["title"] == "The Reading of the Holy Gospel"
    assert holy["verses"][0].startswith("1. ")
    assert "Gennesaret" in holy["verses"][0]
    matins = lib.passage_page(civil, next(r["i"] for r in gospels if "Matins" in r["source"]))
    assert matins["title"] == "Matins Gospel"
    assert "sepulchre" in matins["verses"][0]
    chariton = lib.passage_page(civil, next(r["i"] for r in gospels if r["display"] == "Luke 6.17-23"))
    assert "St Chariton" in chariton["citation"]


def test_month_summary_carries_the_names_and_the_gospel_link():
    info = library.summary(lib.civil_month(2026, 10)[11])
    assert info["commemorations"][:3] == [
        "Ven. Chariton the Confessor",
        "Holy Prophet Baruch (6th c. BC)",
        "Holy Martyr Vatslav (Wenceslas), King of the Czechs (929)",
    ]
    assert any(r["gospel"] and r["display"] == "Luke 5.1-11" for r in info["readings"])
    assert all(isinstance(r, dict) and "display" in r for r in info["readings"])


def test_day_plan_reads_that_day():
    cfg = {"prayers": True, "content": "mixed", "hours": False}
    segments, closing = lib.plan("day", cfg, 30, civil=dt.date(2026, 10, 14))
    titles = [s.title for s in segments]
    assert "The Day on the Old Calendar" in titles and closing is not None
    assert any(s.kind == "scripture" for s in segments)
