"""Logic tests (no windows, no sound): python -m pytest tests"""

import datetime as dt
from datetime import date as datetime_date

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


def test_first_run_is_a_day_but_not_a_morning():
    assert Tracker({}).tick(T0, 0, CFG) == ["first_day"]


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


def test_bible_search_is_one_book_and_chapters_open():
    lib = library.Library()
    hits = lib.bible_search("John", "In the beginning")
    assert hits and hits[0] == {"chapter": 1, "verse": 1, "text": hits[0]["text"]}
    assert hits[0]["text"].startswith("In the beginning")
    assert all(h["chapter"] <= 21 for h in hits)
    assert lib.bible_search("John", "a") == []
    assert lib.bible_search("No Such Book", "the") == []
    openings = lib.bible_openings("John")
    assert len(openings) == 21 and openings[0]["n"] == 1 and openings[0]["first"].startswith("In the beginning")
    if not lib.psalter:
        psalms = lib.bible_search("Psalms", "Have mercy upon me")
        assert psalms and psalms[0]["chapter"] == 51 and psalms[0]["verse"] == 1


def test_waking_up_after_six_hours():
    """Six hours or more away (asleep, the PC off or sleeping) is a new morning; less is a rest."""
    cfg = dict(CFG, sleep_hours=6)
    tr = Tracker({})
    _, t = run(tr, T0, 600)
    assert tr.tick(t + 5.9 * 3600, 0, cfg) == ["rested"]
    _, t = run(tr, t + 5.9 * 3600, 60)
    assert "new_day" in tr.tick(t + 6 * 3600 + 1, 0, cfg)


def test_waking_up_is_measured_across_restarts():
    """The last input is saved, so logging on in the morning (the app freshly started by
    Windows) still sees the night: a new Tracker on yesterday's state wakes up."""
    cfg = dict(CFG, sleep_hours=6)
    state = {}
    tr = Tracker(state)
    _, t = run(tr, T0, 600)
    morning = Tracker(dict(state))  # what the app loads at logon
    assert "new_day" in morning.tick(t + 8 * 3600, 0, cfg)


def test_morning_is_the_days_readings_and_lives():
    lib = library.Library()
    cfg = {"prayers": True, "content": "saints", "hours": False, "morning_prayers": False}
    segments, closing = lib.plan("morning", cfg, 30, civil=datetime_date(2026, 10, 14))
    kinds = [s.kind for s in segments]
    assert kinds[0] == "prayer" and kinds.count("prayer") == 1          # a short beginning only
    assert "scripture" in kinds and "life" in kinds and closing is not None
    assert segments[-1].kind == "life"                                   # nothing padded on after


def test_breaks_are_lives_of_the_saints_by_default():
    from reader import settings
    assert settings.DEFAULTS["content"] == "saints" and settings.DEFAULTS["hours"] is False
    assert settings.DEFAULTS["sleep_hours"] == 6 and settings.DEFAULTS["autostart"] is True
    lib = library.Library()
    segments, _ = lib.plan("break", dict(settings.DEFAULTS), 15)
    assert all(s.kind in ("prayer", "life") for s in segments)


class _Stub:
    """Just enough of App for stop_session/_on, without Tk."""

    def __init__(self, strict: bool, enabled: bool = True):
        from reader import settings
        self.cfg = dict(settings.DEFAULTS, enabled=enabled)
        self.strict = strict
        self.stopped = False
        self.notes = []
        stub = self

        class S:
            def stop(self):
                stub.stopped = True

        self.session = S()
        self.root = type("R", (), {"after": lambda _, ms, fn: stub.notes.append(fn)})()

    def _ended(self, reason):
        self.session = None

    def _fetch_today(self):
        pass

    def _heads_up(self, text, kind):
        self.notes.append(kind)


def test_strict_reading_cannot_be_stopped():
    from reader.app import App
    app = _Stub(strict=True)
    App.stop_session(app)
    assert not app.stopped and app.session is not None
    app = _Stub(strict=False)
    App.stop_session(app)
    assert app.stopped


def test_automatic_off_means_no_fast_day_note():
    import threading
    from reader.app import App
    app = _Stub(strict=False, enabled=False)
    app.session = None
    real = threading.Thread
    threading.Thread = lambda *a, **k: type("T", (), {"start": lambda self: None})()
    try:
        App._on(app, "new_day")
    finally:
        threading.Thread = real
    assert app.notes == []


def test_extreme_holds_its_break_settings(tmp_path, monkeypatch):
    from reader import settings
    monkeypatch.setattr(settings, "SETTINGS", tmp_path / "settings.json")
    settings.save(dict(settings.DEFAULTS, extreme=True, strict=False, enabled=False, emergency_exit=True))
    cfg = settings.load()
    assert cfg["extreme"] and cfg["strict"] and cfg["enabled"] and cfg["mode"] == "lock" and not cfg["emergency_exit"]


def test_extreme_freezes_what_could_skip_a_break(monkeypatch):
    from reader import settings
    from reader.app import App
    app = _Stub(strict=True)
    app.cfg["extreme"] = True
    monkeypatch.setattr(settings, "save", lambda cfg: None)
    for key, value in (("enabled", False), ("strict", False), ("mode", "listen"),
                       ("break_minutes", 1), ("work_minutes", 480), ("autostart", False)):
        before = app.cfg[key]
        App._set(app, key, value)
        assert app.cfg[key] == before, key
    App._set(app, "theme", "dark")  # the look is still yours
    assert app.cfg["theme"] == "dark"
    App.set_enabled(app, False)
    assert app.cfg["enabled"]
    App._set(app, "extreme", False)  # not in the middle of a break
    assert app.cfg["extreme"]
    app.session = None
    app._tray_update = lambda: None
    App._set(app, "extreme", False)  # between breaks: a normal switch
    assert not app.cfg["extreme"]


def test_announcement_counts_the_days():
    from reader.app import announcement

    ahead = {"feast": {"name": "Nativity of Christ", "days": 12},
             "season": {"name": "Nativity Fast", "days": 1}}
    today = {"feast_level": 6, "feasts": ["Protection of the Most-Holy Theotokos"]}
    assert announcement(ahead, today) == ["Today is the feast: Protection of the Most-Holy Theotokos.",
                                          "12 days until the Nativity of Christ.",
                                          "The Nativity Fast begins tomorrow."]
    assert announcement({"feast": None, "season": None}, {"feast_level": 2, "feasts": ["St X"]}) == []
