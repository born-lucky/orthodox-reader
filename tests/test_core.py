"""Logic tests (no windows, no sound): python -m pytest tests"""

import datetime as dt

from reader import library, speech
from reader.activity import Tracker

CFG = {"work_minutes": 120, "sleep_hours": 5, "away_minutes": 15, "warn_seconds": 60}
T0 = dt.datetime(2026, 10, 3, 7, 0).timestamp()


def run(tracker, start, seconds, idle=0.0, step=5):
    events, t = [], start
    while t < start + seconds:
        t += step
        events += tracker.tick(t, idle, CFG)
    return events, t


def test_first_run_is_a_new_day():
    assert "new_day" in Tracker({}).tick(T0, 0, CFG)


def test_two_hours_of_use_brings_a_warning_then_a_break():
    tr = Tracker({})
    events, t = run(tr, T0, 120 * 60 - 61)
    assert "warn" not in events and "break" not in events
    events, t = run(tr, t, 5)
    assert events == ["warn"]
    events, t = run(tr, t, 60)
    assert "break" in events


def test_a_short_absence_is_a_break_and_a_long_one_a_new_day():
    tr = Tracker({})
    _, t = run(tr, T0, 3600)
    assert tr.state["active"] > 3500
    # 20 minutes away: the counter starts over
    assert tr.tick(t + 1200, 0, CFG) == ["rested"]
    assert tr.state["active"] == 0
    # 7 hours away (asleep, or the PC was off): a new day
    assert "new_day" in tr.tick(t + 1200 + 7 * 3600, 0, CFG)


def test_suspend_is_not_counted_as_use():
    tr = Tracker({})
    _, t = run(tr, T0, 600)
    before = tr.state["active"]
    tr.tick(t + 300, 0, CFG)  # a 5-minute jump with no idle: the machine slept
    assert tr.state["active"] == before


def test_snooze_delays_the_break_and_warns_again():
    tr = Tracker({})
    events, t = run(tr, T0, 120 * 60 + 10)
    assert "break" in events
    tr.snooze(t, 10)
    events, t = run(tr, t, 9 * 60 - 5)
    assert "break" not in events and "warn" not in events
    events, t = run(tr, t, 70)
    assert "warn" in events
    events, t = run(tr, t, 60)
    assert "break" in events


def test_chunks_keep_sentences_and_limits():
    text = "Have mercy on me, O God. " * 40 + "A" * 900
    out = speech.chunks(text)
    assert all(len(c) <= speech.CHUNK * 1.5 for c in out)
    assert "".join(out).replace(" ", "") == text.replace(" ", "")


def test_speakable_references():
    assert speech.speakable("John 8.21-30") == "John 8, verses 21 to 30"
    assert speech.speakable("St. Basil") == "Saint Basil"


def test_lxx_numbering():
    assert library.lxx(51) == 50 and library.lxx(23) == 22 and library.lxx(150) == 150


def test_hour_of_the_day():
    at = lambda h: library.hour_for(dt.datetime(2026, 10, 3, h))
    assert [at(h) for h in (7, 10, 13, 16, 20, 3)] == ["first", "third", "sixth", "ninth", "compline", "midnight"]


def test_weekday_parts():
    secs = [{"title": "Psalm 5", "text": ["x"]}, {"title": "On Saturday", "text": ["x"]},
            {"title": "On Wednesday and Friday", "text": ["x"]}]
    assert [s["title"] for s in library.for_today(secs, 4)] == ["Psalm 5", "On Wednesday and Friday"]
    assert [s["title"] for s in library.for_today(secs, 5)] == ["Psalm 5", "On Saturday"]


def test_plan_always_has_content_and_a_closing():
    lib = library.Library()
    cfg = {"prayers": True, "content": "mixed", "hours": False}
    segments, closing = lib.plan("break", cfg, 15)
    assert sum(library.speaking_time(s) for s in segments) <= 15 * 60  # whole works that fit
    assert len(segments) >= 3 and closing is not None
    assert all(seg.lines for seg in segments)


def test_bundled_bible_and_prayers():
    lib = library.Library()
    assert len(lib.kjv) == 66 and lib.kjv["Psalms"][50][0].startswith("Have mercy upon me")
    assert lib.chapter("Psalms", 51).title == "Psalm 51 (50 in the Septuagint)"
