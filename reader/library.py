"""What gets read aloud: prayers, Scripture, and the lives of the saints.

Bundled with the app (public domain):
  data/prayers.json   traditional prayers (Hapgood 1906 / KJV wording)
  data/kjv.json       the King James Bible

Fetched (not bundled): the old-calendar day from orthocal.info, which gives the
day's appointed Scripture readings and the lives of the saints commemorated.
Every fetched day is kept in %APPDATA%, and a slow background prefetch fills
in the rest of the year, so the lives of the saints keep working offline.
"""

from __future__ import annotations

import datetime as dt
import html
import json
import random
import re
import threading
import urllib.request
from dataclasses import dataclass, field

from . import settings

API = "https://orthocal.info/api/julian/{y}/{m}/{d}/"
DAYS = settings.HOME / "orthocal"
DAYS.mkdir(exist_ok=True)
# Your own translations (tools/build_my_texts.py), used before the bundled ones.
MINE = settings.HOME / "my-texts"
WEEKDAYS = ["monday", "tuesday", "wednesday", "thursday", "friday", "saturday", "sunday"]
# The kathismata of the Psalter (Septuagint numbering).
KATHISMATA = [(1, 8), (9, 16), (17, 23), (24, 31), (32, 36), (37, 45), (46, 54), (55, 63), (64, 69), (70, 76),
              (77, 84), (85, 90), (91, 100), (101, 104), (105, 108), (109, 117), (118, 118), (119, 133),
              (134, 142), (143, 150)]
SEGMENT_CHARS = 6000  # a whole chapter or life, cut at a verse/paragraph if longer

# Psalms (KJV numbering) to draw from, and the weight of each part of the Bible
# when Scripture is picked at random.
SCRIPTURE_PARTS = [
    (5, ["Psalms"]),
    (3, ["Matthew", "Mark", "Luke", "John"]),
    (2, ["Acts", "Romans", "1 Corinthians", "2 Corinthians", "Galatians", "Ephesians", "Philippians",
         "Colossians", "1 Thessalonians", "Hebrews", "James", "1 Peter", "1 John"]),
    (1, ["Proverbs", "Ecclesiastes", "Isaiah", "Genesis", "Job", "Wisdom"]),
]


@dataclass
class Segment:
    """One thing read aloud: a prayer, a chapter, a life."""

    kind: str               # "prayer" | "scripture" | "life" | "calendar"
    title: str
    lines: list[str] = field(default_factory=list)  # read in order; captions follow them

    @property
    def chars(self) -> int:
        return sum(len(line) for line in self.lines)


def _load(name: str) -> dict:
    return json.loads((settings.DATA / name).read_text(encoding="utf-8"))


def _mine(name: str) -> dict:
    try:
        return json.loads((MINE / name).read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}


def hour_for(now: dt.datetime) -> str:
    """Which office of the Horologion fits this time of day."""
    h = now.hour
    if 5 <= h < 9:
        return "first"
    if 9 <= h < 12:
        return "third"
    if 12 <= h < 15:
        return "sixth"
    if 15 <= h < 18:
        return "ninth"
    if 18 <= h or h < 1:
        return "compline"
    return "midnight"


def for_today(sections: list[dict], weekday: int) -> list[dict]:
    """Drop the parts appointed for other days ("On Saturday", "On Sunday night")."""
    today = WEEKDAYS[weekday]
    out = []
    for sec in sections:
        title = sec["title"].lower()
        named = [d for d in WEEKDAYS if d in title]
        if title.startswith("on ") and named and today not in named:
            continue
        out.append(sec)
    return out


def strip_html(text: str) -> str:
    text = re.sub(r"(?i)<br\s*/?>|</p>|</h\d>", "\n", text or "")
    text = html.unescape(re.sub(r"<[^>]+>", "", text))
    return "\n".join(line.strip() for line in text.splitlines() if line.strip())


FEAST_MARKS = {6: "great", 5: "vigil", 4: "polyeleos", 3: "doxology", 2: "six"}


def fast_of(data: dict) -> tuple[str, str]:
    """("strict" | "relaxed" | "none", words) for one orthocal day."""
    level = data.get("fast_level") or 0
    name = (data.get("fast_level_desc") or "").strip()
    allowed = (data.get("fast_exception_desc") or "").strip()
    if not level:
        return "none", "No fast"
    name = "Fast day" if name.lower() == "fast" else name
    if allowed:
        return "relaxed", f"{name} · {allowed.lower()}"
    return "strict", f"{name} · strict"


def summary(data: dict) -> dict:
    """What the calendar shows for a day."""
    kind, words = fast_of(data)
    titles = [t for t in data.get("titles") or [] if t]
    return {
        "day": data.get("day"), "fast": kind, "fast_text": words,
        "feast": FEAST_MARKS.get(data.get("feast_level") or 0, ""),
        "title": titles[0] if titles else data.get("summary_title", ""),
        "feasts": [f for f in data.get("feasts") or [] if f],
        "saints": [s for s in data.get("saints") or [] if s],
        "readings": [r.get("display") for r in data.get("readings") or [] if r.get("display")],
        "tone": data.get("tone"),
    }


def lxx(psalm: int) -> int:
    """KJV (Hebrew) psalm number -> the Septuagint number Orthodox books use."""
    return psalm - 1 if 11 <= psalm <= 146 and psalm not in (115, 116) else psalm


GOSPELS = {"Matthew", "Mark", "Luke", "John"}
NUMBERED = {"1": "First", "2": "Second", "3": "Third"}


def book_phrase(book: str) -> str:
    """How a reading is announced: "the Holy Gospel according to Saint Luke"."""
    if book in GOSPELS:
        return f"the Holy Gospel according to Saint {book}"
    if book == "Acts":
        return "the Acts of the Apostles"
    if book == "Revelation":
        return "the Revelation of Saint John"
    if book == "Psalms":
        return "the Psalms"
    epistles = {"Romans", "Corinthians", "Galatians", "Ephesians", "Philippians", "Colossians", "Thessalonians",
                "Timothy", "Titus", "Hebrews", "James", "Peter", "John", "Jude"}
    num, _, name = book.partition(" ") if book[0].isdigit() else ("", "", book)
    if name in epistles:
        to = {"James": "of James", "Peter": "of Peter", "John": "of John", "Jude": "of Jude"}.get(name, f"to the {name}")
        return f"the {NUMBERED[num] + ' ' if num else ''}Epistle {to}"
    if book in ("Isaiah", "Jeremiah", "Ezekiel", "Daniel", "Micah", "Jonah", "Hosea", "Joel", "Amos"):
        return f"the Prophecy of {book}"
    if book in ("Wisdom of Solomon", "Lamentations"):
        return f"the {book}"
    if num:
        return f"the {NUMBERED[num]} Book of {name}"
    return f"the Book of {book}"


CHARS_PER_SECOND = 13.0  # the neural voice at a calm pace
GAP = 2.5                # silence between works


def speaking_time(seg: "Segment") -> float:
    return seg.chars / CHARS_PER_SECOND


def cut(lines: list[str], limit: int = SEGMENT_CHARS) -> list[str]:
    out, size = [], 0
    for line in lines:
        if out and size + len(line) > limit:
            break
        out.append(line)
        size += len(line)
    return out


class Library:
    def __init__(self) -> None:
        self.prayers = _load("prayers.json")
        mine = _mine("prayers.json")
        self.prayers.update({k: v for k, v in mine.items() if v and k != "source"})
        self.psalter = _mine("psalter.json").get("psalms", {})   # {"50": {"title", "verses"}} (LXX)
        self.hours = _mine("hours.json").get("hours", {})
        self.works = _mine("works.json")  # whole works from your own books: Prologue, Theophan, Chrysostom
        self.lxx = {b["name"]: b["chapters"] for b in _load("lxx.json")["books"]}  # Brenton: Wisdom, Sirach...
        self.pericopes = _load("pericopes.json")  # the great passages, each read whole
        self._kjv: dict | None = None
        self.rng = random.Random()
        self._lock = threading.Lock()

    # ------------------------------------------------------------ Scripture

    @property
    def kjv(self) -> dict:
        if self._kjv is None:
            self._kjv = {b["name"]: b["chapters"] for b in _load("kjv.json")["books"]}
        return self._kjv

    def chapter(self, book: str, number: int) -> Segment:
        verses = self.kjv[book][number - 1]
        if book == "Psalms":
            title = f"Psalm {number}" + (f" ({lxx(number)} in the Septuagint)" if lxx(number) != number else "")
        else:
            title = f"{book}, chapter {number}"
        return Segment("scripture", title, cut([title + "."] + verses))

    def random_scripture(self) -> Segment:
        parts = [books for weight, books in SCRIPTURE_PARTS for _ in range(weight)]
        books = [b for b in self.rng.choice(parts) if b in self.kjv]
        book = self.rng.choice(books)
        if book == "Psalms" and self.psalter:
            return self.psalter_psalm(self.rng.choice(list(self.psalter)))
        return self.chapter(book, self.rng.randint(1, len(self.kjv[book])))

    def psalm(self) -> Segment:
        kjv = self.rng.choice(self.prayers["psalms"])
        if self.psalter:
            return self.psalter_psalm(str(lxx(kjv)))
        return self.chapter("Psalms", kjv)

    def psalter_psalm(self, number: str) -> Segment:
        item = self.psalter[number]
        return Segment("scripture", item["title"], cut([item["title"] + "."] + item["verses"], SEGMENT_CHARS * 3))

    def kathisma(self) -> list[Segment]:
        """The next kathisma of the Psalter, in order; the place is kept between breaks."""
        state = settings.load_state()
        n = int(state.get("kathisma", 0)) % len(KATHISMATA)
        state["kathisma"] = n + 1
        settings.save_state(state)
        first, last = KATHISMATA[n]
        out = [Segment("prayer", f"Kathisma {n + 1}", [f"Kathisma {n + 1} of the Psalter."])]
        for num in range(first, last + 1):
            if self.psalter and str(num) in self.psalter:
                out.append(self.psalter_psalm(str(num)))
            else:
                kjv = num + 1 if 10 <= num <= 145 else num
                out.append(self.chapter("Psalms", kjv))
        return out

    def hour(self, key: str, weekday: int) -> list[Segment]:
        """An office of the Horologion from your texts, or nothing."""
        office = self.hours.get(key)
        if not office:
            return []
        return [Segment("prayer", sec["title"], list(sec["text"])) for sec in for_today(office["sections"], weekday)]

    def sequence(self, group: str) -> list[Segment]:
        """A whole group read in order (your morning or evening prayers)."""
        return [Segment("prayer", item["title"], list(item["text"])) for item in self.prayers.get(group) or []]

    # ------------------------------------------------------------ prayers

    def prayer(self, group: str) -> Segment:
        item = self.rng.choice(self.prayers[group])
        return Segment("prayer", item["title"], list(item["text"]))

    # ------------------------------------------------------------ the old calendar

    def day(self, civil: dt.date, fetch: bool = True) -> dict | None:
        """orthocal's old-calendar day for a civil date, from disk or the web."""
        path = DAYS / f"{civil.isoformat()}.json"
        if path.is_file():
            try:
                return json.loads(path.read_text(encoding="utf-8"))
            except ValueError:
                path.unlink(missing_ok=True)
        if not fetch:
            return None
        url = API.format(y=civil.year, m=civil.month, d=civil.day)
        request = urllib.request.Request(url, headers={"User-Agent": "OrthodoxReader/1.0"})
        try:
            with urllib.request.urlopen(request, timeout=20) as response:
                data = json.loads(response.read().decode("utf-8"))
        except (OSError, ValueError):
            return None
        path.write_text(json.dumps(data), encoding="utf-8")
        return data

    def month(self, year: int, month: int) -> list[dict]:
        """A whole civil month of the old calendar (one request), cached apart from the days."""
        folder = DAYS / "months"
        folder.mkdir(exist_ok=True)
        path = folder / f"{year:04d}-{month:02d}.json"
        if path.is_file():
            try:
                return json.loads(path.read_text(encoding="utf-8"))
            except ValueError:
                path.unlink(missing_ok=True)
        url = f"https://orthocal.info/api/julian/{year}/{month}/"
        request = urllib.request.Request(url, headers={"User-Agent": "OrthodoxReader/1.0"})
        try:
            with urllib.request.urlopen(request, timeout=20) as response:
                days = json.loads(response.read().decode("utf-8"))
        except (OSError, ValueError):
            return []
        days.sort(key=lambda d: d.get("day", 0))
        path.write_text(json.dumps(days), encoding="utf-8")
        return days

    def civil_month(self, year: int, month: int) -> dict[int, dict]:
        """{civil day: orthocal day} for a civil month. orthocal's month endpoint counts in
        Julian months, so the civil month is put together from the two that cover it
        (old style + 13 days = new style, 1900-2099)."""
        prev = (year, month - 1) if month > 1 else (year - 1, 12)
        out: dict[int, dict] = {}
        for y, m in (prev, (year, month)):
            for item in self.month(y, m):
                try:
                    civil = dt.date(item["year"], item["month"], item["day"]) + dt.timedelta(days=13)
                except (KeyError, ValueError):
                    continue
                if (civil.year, civil.month) == (year, month):
                    out[civil.day] = item
        return out

    def headline(self, civil: dt.date) -> str:
        data = self.day(civil, fetch=False)
        if not data:
            return ""
        titles = [t for t in data.get("titles") or [] if t]
        return titles[0] if titles else data.get("summary_title", "")

    def calendar_segment(self, data: dict) -> Segment:
        lines = [t + "." for t in data.get("titles") or [] if t]
        feasts = [f for f in data.get("feasts") or [] if f]
        if feasts:
            lines.append("Today we keep " + "; ".join(feasts) + ".")
        if data.get("fast_level_desc"):
            fast = data["fast_level_desc"]
            if data.get("fast_exception_desc"):
                fast += ", " + data["fast_exception_desc"]
            lines.append(f"Fasting: {fast}.")
        saints = [s for s in data.get("saints") or [] if s]
        if saints:
            lines.append("Today the Church commemorates " + "; ".join(saints) + ".")
        return Segment("calendar", "The Day on the Old Calendar", lines)

    def readings(self, data: dict) -> list[Segment]:
        out = []
        for reading in data.get("readings") or []:
            verses = [strip_html(v.get("content", "")) for v in reading.get("passage") or []]
            if verses:
                title = reading.get("display") or "Scripture"
                about = reading.get("description") or ""
                head = f"The reading from {title}" + (f", {about}" if about else "") + "."
                out.append(Segment("scripture", title, cut([head] + verses, SEGMENT_CHARS * 2)))
        return out

    def lives(self, data: dict) -> list[Segment]:
        out = []
        for story in data.get("stories") or []:
            text = strip_html(story.get("story", ""))
            if text:
                title = strip_html(story.get("title", "")) or "The Life of a Saint"
                out.append(Segment("life", title, cut([title + "."] + text.splitlines())))
        return out

    def random_life(self) -> Segment | None:
        """A life from any day already on disk; fetch one random day if none are."""
        files = list(DAYS.glob("*.json"))
        self.rng.shuffle(files)
        for path in files[:40]:
            try:
                lives = self.lives(json.loads(path.read_text(encoding="utf-8")))
            except ValueError:
                continue
            if lives:
                return self.rng.choice(lives)
        start = dt.date(dt.date.today().year, 1, 1)
        data = self.day(start + dt.timedelta(days=self.rng.randrange(365)))
        lives = self.lives(data) if data else []
        return self.rng.choice(lives) if lives else None

    def prefetch(self, stop: threading.Event, pause: float = 20.0) -> None:
        """Slowly fetch the rest of the year so the lives work offline (one day per pause)."""
        today = dt.date.today()
        days = [today + dt.timedelta(days=n) for n in range(-180, 186)]
        self.rng.shuffle(days)
        days = [today, today + dt.timedelta(days=1)] + days
        for day in days:
            if stop.is_set():
                return
            if (DAYS / f"{day.isoformat()}.json").is_file():
                continue
            if self.day(day) is None:
                stop.wait(600)  # offline: try again later
                continue
            stop.wait(pause)

    # ------------------------------------------------------------ works

    def work(self, kind: str) -> Segment | None:
        """One whole work of a kind, or None if there is none to be had."""
        mine = self.works
        pick = self.rng.choice
        if kind == "prologue" and mine.get("prologue_lives"):
            w = pick(mine["prologue_lives"])
            return Segment("life", w["title"], [f"From the Prologue of Ohrid: {w['title']}."] + w["text"])
        if kind == "homily" and (mine.get("prologue_homilies") or mine.get("prologue_reflections")):
            w = pick((mine.get("prologue_homilies") or []) + (mine.get("prologue_reflections") or []))
            return Segment("homily", w["title"], [w["title"] + "."] + w["text"])
        if kind == "theophan" and mine.get("theophan"):
            w = pick(mine["theophan"])
            return Segment("homily", w["title"], [w["title"] + "."] + w["text"])
        if kind == "chrysostom" and mine.get("chrysostom"):
            w = pick(mine["chrysostom"])
            return Segment("homily", w["title"], [w["title"] + "."] + w["text"])
        if kind == "life":
            return self.random_life()
        if kind in ("gospel", "apostle", "prophets"):
            return self.pericope(pick(self.pericopes[kind]))
        if kind == "lectionary":
            return self.lectionary()
        if kind == "wisdom":
            book = pick(["Wisdom of Solomon"] * 5 + ["Sirach"] * 4 + ["Tobit", "Baruch"])
            return self.lxx_chapter(book, self.rng.randint(1, len(self.lxx[book])))
        if kind == "psalm":
            kjv = pick(self.pericopes["psalms"])
            return self.psalter_psalm(str(lxx(kjv))) if self.psalter and str(lxx(kjv)) in self.psalter else self.chapter("Psalms", kjv)
        return None

    def pericope(self, item: list) -> Segment:
        """One of the great passages, read whole: [title, book, chapter, first, last]."""
        title, book, chapter, first, last = item
        verses = (self.lxx.get(book) or self.kjv[book])[chapter - 1]
        verses = verses[first - 1: last or None]
        ref = f"{book} {chapter}:{first}" + (f"-{last}" if last else "")
        return Segment("scripture", f"{title} ({ref})", [f"From {book_phrase(book)}. {title}."] + verses)

    def lectionary(self) -> Segment | None:
        """A Gospel or Epistle the Church appoints for some day of the year (from the
        old-calendar days on disk), with the day it belongs to."""
        files = list(DAYS.glob("*.json"))
        self.rng.shuffle(files)
        for path in files[:30]:
            try:
                data = json.loads(path.read_text(encoding="utf-8"))
            except ValueError:
                continue
            readings = [r for r in data.get("readings") or [] if r.get("source") in ("Gospel", "Epistle") and r.get("passage")]
            if not readings:
                continue
            r = self.rng.choice(readings)
            day = (data.get("titles") or [data.get("summary_title") or ""])[0]
            kind = "Gospel" if r["source"] == "Gospel" else "Epistle"
            verses = [strip_html(v.get("content", "")) for v in r["passage"]]
            head = f"The {kind} for {day}: {r.get('display', '')}." if day else f"The {kind}: {r.get('display', '')}."
            return Segment("scripture", f"The {kind} · {r.get('display', '')}", cut([head] + verses, SEGMENT_CHARS * 2))
        return None

    def lxx_chapter(self, book: str, number: int) -> Segment:
        title = f"{book}, chapter {number}"
        return Segment("scripture", title, cut([f"From the {book}, chapter {number}."] + self.lxx[book][number - 1], SEGMENT_CHARS * 2))

    KINDS = {
        # content setting -> (kind, weight): the variety a break draws from, at random
        "saints": [("prologue", 5), ("homily", 2), ("theophan", 2), ("chrysostom", 1), ("life", 2)],
        "scripture": [("lectionary", 4), ("gospel", 4), ("apostle", 3), ("prophets", 3), ("psalm", 3), ("wisdom", 1)],
    }

    def works_for(self, cfg: dict, seconds: float) -> list[Segment]:
        """Whole works of different kinds that fit in `seconds` (never cut short)."""
        want = cfg["content"]
        if want == "psalter":
            return self.kathisma()
        kinds = self.KINDS["saints"] + self.KINDS["scripture"] if want == "mixed" else self.KINDS.get(want, self.KINDS["saints"])
        bag = [k for k, weight in kinds for _ in range(weight)]
        out: list[Segment] = []
        last = None
        left = seconds
        misses = 0
        seen: set[str] = set()
        while left > 45 and misses < 30:
            kind = self.rng.choice(bag)
            if kind == last and len(set(bag)) > 1:
                continue
            item = self.work(kind)
            if item is None or not item.lines or item.title in seen or speaking_time(item) > left:
                misses += 1
                continue
            out.append(item)
            seen.add(item.title)
            left -= speaking_time(item) + GAP
            last = kind
            misses = 0
        return out

    def filler(self, cfg: dict, seconds: float) -> Segment | None:
        """One more short whole work, if one fits in what is left of the break."""
        for _ in range(20):
            item = self.works_for(cfg, seconds)
            if item:
                return item[0]
        return None

    # ------------------------------------------------------------ a whole session

    def plan(self, kind: str, cfg: dict, minutes: float, civil: dt.date | None = None) -> tuple[list[Segment], Segment | None]:
        """What a session reads, in order, and the closing prayer it ends with.

        The opening prayers (or the Hour of the day), then whole works of different
        kinds chosen to fill the time: a life from the Prologue, a chapter of the
        Wisdom of Solomon, a thought of St. Theophan, a psalm... Nothing is cut off.
        kind "morning" reads the morning prayers and the day's readings first.
        """
        segments: list[Segment] = []
        now = dt.datetime.now()
        civil = civil or dt.date.today()
        office = self.hour(hour_for(now), now.weekday()) if cfg.get("hours") and kind == "break" else []
        if cfg["prayers"]:
            if kind == "morning" and self.prayers.get("morning_full"):
                segments += self.sequence("morning_full")
                segments.append(self.prayer("work"))
            elif kind == "morning":
                segments += [self.prayer("opening"), self.prayer("morning"), self.prayer("work")]
            elif office:
                segments += office  # the Hour opens with its own prayers
            elif (now.hour >= 21 or now.hour < 4) and self.prayers.get("evening_full"):
                segments += self.sequence("evening_full")
            else:
                segments.append(self.prayer("opening"))
        elif office:
            segments += office
        if kind == "day":  # a day chosen in the calendar: its Scripture and its saints, nothing else
            data = self.day(civil)
            segments = [self.prayer("opening")] if cfg["prayers"] else []
            if data:
                segments.append(self.calendar_segment(data))
                segments += self.readings(data)
            j = civil - dt.timedelta(days=13)
            saints = [w for w in self.works.get("prologue_lives") or [] if w.get("when") == f"{j.month}/{j.day}"]
            segments += [Segment("life", w["title"], [f"From the Prologue of Ohrid: {w['title']}."] + w["text"]) for w in saints]
            if not saints and data:
                segments += self.lives(data)
            return segments, (self.prayer("closing") if cfg["prayers"] else None)
        if kind == "morning":
            data = self.day(civil)
            if data:
                segments.append(self.calendar_segment(data))
                segments += self.readings(data)
            j = civil - dt.timedelta(days=13)
            today = [w for w in self.works.get("prologue_lives") or [] if w.get("when") == f"{j.month}/{j.day}"]
            segments += [Segment("life", w["title"], [f"From the Prologue of Ohrid: {w['title']}."] + w["text"]) for w in today]
            if not today and data:
                segments += self.lives(data)
        closing = self.prayer("closing") if cfg["prayers"] else None
        used = sum(speaking_time(seg) + GAP for seg in segments) + (speaking_time(closing) if closing else 0)
        segments += self.works_for(cfg, minutes * 60 - used - 30)
        return segments, closing

    def random_segments(self, cfg: dict, count: int) -> list[Segment]:
        return self.works_for(cfg, count * 120)
