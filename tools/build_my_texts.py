"""Build a private "my texts" pack from books you own, for your copy of the app.

The app ships with public-domain texts only. If you own other translations, this
turns them into %APPDATA%\\OrthodoxReader\\my-texts\\, which the app reads
first. Nothing here is uploaded or committed: the pack stays on your computer.

    python tools/build_my_texts.py --jordanville "Jordanville Prayer Book.pdf"
                                   --horologion "Unabbreviated Horologion.pdf"
                                   --psalter liturgy.io

  --jordanville  Prayer Book, Holy Trinity Monastery (4th ed.): morning prayers,
                 prayers during the day, prayers before sleep
  --horologion   The Unabbreviated Horologion, Holy Trinity Monastery (2nd ed.):
                 the First, Third, Sixth and Ninth Hours, Small Compline, Midnight Office
  --psalter      "liturgy.io": the Psalter According to the Seventy from
                 liturgy.io, fetched once (151 pages, politely)

Page ranges are for those editions (scanned PDFs: italic = rubric, bold = heading).
"""

from __future__ import annotations

import argparse
import collections
import html
import json
import re
import sys
import time
import urllib.request
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from reader import settings  # noqa: E402

OUT = settings.HOME / "my-texts"

# PDF page index ranges (inclusive), per book.
JORDANVILLE = {
    "morning": (4, 31),
    "day": (32, 35),
    "evening": (36, 61),
}
HOROLOGION = {  # book page + 3 = PDF page
    "midnight": ("The Midnight Office", 4, 23),
    "first": ("The First Hour", 89, 95),
    "third": ("The Third Hour", 116, 122),
    "sixth": ("The Sixth Hour", 127, 133),
    "ninth": ("The Ninth Hour", 177, 183),
    "compline": ("Small Compline", 241, 258),
}
REPEATS = {"thrice": 3, "three times": 3, "twelve": 12, "twelve times": 12, "forty": 40, "forty times": 40}
SPEAKERS = re.compile(r"^(?:The )?(?:Priest|Reader|Deacon|Choir|Chanters?|People)(?: saith)?:\s*")
# Scanned drop caps that the OCR lost or garbled; the generic repair below catches most.
FIXUPS = [
    (r"^J\\s I ", "As I "), (r"^J-?\\\.*\s*", ""), (r"^ory to Thee", "Glory to Thee"),
    (r"^I rise from sleep, I thank Thee", "As I rise from sleep, I thank Thee"), (r"^R ?member", "Remember"),
    (r"^ave mercy", "Have mercy"), (r"\b50uls\b", "souls"), (r"~faster\b", "Master"),
    (r"^H Have\b", "Have"), (r"^T ord\b", "Lord"), (r"^W hat\b", "What"), (r"\bOianters\b", "Chanters"),
    (r"\bSticlws\b", "Stichos"), (r"!vlacarius", "Macarius"), (r"\bIioly\b", "Holy"), (r"/Jy\b", "by"),
    (r"\bCod\b", "God"), (r"\bPsaim\b", "Psalm"), (r"\bStich-os\b", "Stichos"),(r"[\"•](?=\s)", ""),
    # running page headers caught mid-text: "SMALL COMPLINE 243", "THE FIRST HOUR"
    (r"\s*\b(?:THE (?:FIRST|THIRD|SIXTH|NINTH) HOUR|SMALL COMPLINE|THE MIDNIGHT OFFICE[A-Z ]*)\b\s*\d*\s*", " "),
]
CAP = "⁣"  # marks a drop cap waiting to be joined to its word
# Whole words the scans misread (found by checking every word against the KJV and the Psalter).
MISREAD = {
    "Quist": "Christ", "lhe": "the", "lhou": "Thou", "lhy": "Thy", "Thol": "Thou", "Anrl": "And",
    "aJked": "asked", "earken": "hearken", "fuce": "face", "fuinteth": "fainteth", "fur": "for",
    "LordJesus": "Lord Jesus", "ofmy": "of my", "PrayerV": "Prayer V", "SpiriL": "Spirit",
    "trength": "strength", "reating": "creating", "suplicate": "supplicate", "Psaim": "Psalm",
    "compasssionate": "compassionate", "Mostholy": "Most-holy", "onlybegotten": "only-begotten",
    "lifecreating": "life-creating", "allmerciful": "all-merciful", "wellpleased": "well-pleased",
    "Godbearing": "God-bearing", "selflove": "self-love", "Iv": "IV", "eU": "all", "gaveth": "gavest",
}
PHRASES = [
    (r"\bgrac~", "grace"), (r"\bg~tes\b", "gates"), (r"\bcur souls\b", "our souls"), (r"\beil deeds\b", "evil deeds"),
    (r"\bN aine\b", "Name"), (r"\bvmrd\b", "word"), (r"\b([a-z]+)- \d+ ([a-z]+)\b", r"\1\2"),  # "salva- 10 tion"
    (r"\bneg-lectful\b", "neglectful"),
]
# Running heads of the Horologion that the scan dropped into the text.
HEADS = re.compile(r"\s*\b(?:[A-Z]{2,}[A-Za-z]*\s*)*(?:COMPL\S*|HOROLOG\S*|TIIE|WEEKDA\S*)(?:\s+[A-Z]{2,}\S*)*\s*\d*\s*")


# ------------------------------------------------------------ PDF -> sections


def runs(pdf, first: int, last: int):
    """(style, text) runs in reading order: style is "head", "rubric" or "text"."""
    import pymupdf

    doc = pymupdf.open(pdf)
    out: list[tuple[str, str]] = []
    for pn in range(first, last + 1):
        for block in doc[pn].get_text("dict")["blocks"]:
            for line in block.get("lines", []):
                for span in line["spans"]:
                    text = span["text"]
                    if not text.strip():
                        continue
                    if re.fullmatch(r"\s*-\s*\d+\s*-\s*", text):  # page number
                        continue
                    font = span["font"].lower()
                    letters = sum(c.isalpha() for c in text)
                    if letters < len(text.strip()) * 0.5 and not re.search(r"\d", text):
                        continue  # ornament rules: ++++++ l·+++
                    if len(text.strip()) == 1 and span["size"] >= 18:
                        style = "cap"  # a drop cap: glued to the next word
                    elif "bold" in font or span["flags"] & 16:
                        style = "head"
                    elif "italic" in font or span["flags"] & 2:
                        style = "rubric"
                    else:
                        style = "text"
                    if out and out[-1][0] == style:
                        prev = out[-1][1]
                        joiner = "" if prev.endswith("-") and text[:1].islower() else " "
                        out[-1] = (style, (prev[:-1] if joiner == "" else prev.rstrip()) + joiner + text.strip())
                    else:
                        out.append((style, text.strip()))
    return out


def clean(text: str) -> str:
    text = re.sub(r"\s+", " ", text).strip()
    for pattern, repl in FIXUPS:
        text = re.sub(pattern, repl, text)
    text = re.sub(r"(?<![\w\d])0(?=\s+[A-Za-z])", "O", text)  # the OCR reads O as zero
    text = re.sub(r"(?<![\w\d])0(?=[A-Z][a-z])", "O ", text)
    text = re.sub(r"\b([A-Za-z]+)- ([a-z]+)\b", r"\1\2", text)  # hyphen left by a line break
    text = text.replace("_", "").replace(" ,", ",").replace(" .", ".")
    text = re.sub(r"(?<=[a-z]),(?=[a-z])", "", text)  # "sl,eep"
    text = SPEAKERS.sub("", text)
    for pattern, repl in FIXUPS:
        text = re.sub(pattern, repl, text)
    return text.strip()


def is_title(text: str) -> bool:
    words = text.rstrip(":. ").split()
    caps = sum(1 for w in words if w[:1].isupper())
    return 1 <= len(words) <= 14 and text[:1].isupper() and (caps >= 2 or len(words) <= 3) and not text.lower().startswith(("then", "and ", "say", "or", "if ", "here", "after this", "thrice", "twelve"))


def sections(pdf, first: int, last: int, default_title: str) -> list[dict]:
    """Split a range into titled sections of spoken paragraphs; rubrics are not read,
    except that "Thrice" / "Twelve times" repeats the line before it."""
    out: list[dict] = [{"title": default_title, "text": []}]
    cap = ""
    for style, raw in runs(pdf, first, last):
        if style == "cap":
            cap = raw.strip().replace("0", "O")
            cap = cap if cap.isalpha() else ""
            continue
        text = clean(raw)
        if not text:
            continue
        if style == "text":
            if cap and text[:1].islower():
                text = cap + CAP + text
            elif cap:
                text = f"{cap} {text}"  # "O" + "Heavenly King"
            cap = ""
            out[-1]["text"].append(text)
            continue
        cap = ""
        lowered = text.lower().strip(" .:;")
        repeat = next((n for key, n in REPEATS.items() if lowered.startswith(key)), 0)
        if repeat and out[-1]["text"]:
            last_line = out[-1]["text"][-1]
            sentence = re.split(r"(?<=[.!?])\s+", last_line)[-1]
            out[-1]["text"] += [sentence] * (repeat - 1)
            continue
        if style == "head" or (style == "rubric" and text.endswith(":") and is_title(text)):
            title = re.sub(r"^(?:Prostration\.\s*)+", "", text.rstrip(":").strip())
            title = re.sub(r"[.,]?\s*(?:Priest|Reader|Deacon|Choir|Chanters?)$", "", title).strip()
            if "\\" in title or len(re.sub(r"[^A-Za-z]", "", title)) < 4:
                continue
            if title.isupper():
                title = title.title()
            if out[-1]["text"]:
                out.append({"title": title, "text": []})
            elif out[-1]["title"] == default_title:
                out[-1]["title"] = title
            else:  # a heading printed in pieces
                joiner = " " if title[:1].islower() else ": "
                out[-1]["title"] = out[-1]["title"] + joiner + title
    return merge_fragments([s for s in out if s["text"]])


def merge_fragments(secs: list[dict]) -> list[dict]:
    """A "section" whose only text is a scrap like "of the" was a piece of the next heading."""
    out: list[dict] = []
    carry = ""
    for sec in secs:
        if len(sec["text"]) == 1 and len(sec["text"][0]) < 30 and not re.search(r"[.!?;]$", sec["text"][0]):
            carry += f"{sec['title']} {sec['text'][0]} "
            continue
        if carry:
            sec = {"title": (carry + sec["title"]).strip(), "text": sec["text"]}
            carry = ""
        out.append(sec)
    return out


def known_words(groups: list[list[dict]]) -> set[str]:
    """Words we trust: the bundled KJV, plus any word the books use three times or more."""
    words: set[str] = set()
    kjv = json.loads((settings.DATA / "kjv.json").read_text(encoding="utf-8"))
    for book in kjv["books"]:
        for chapter in book["chapters"]:
            for verse in chapter:
                words.update(w.lower() for w in re.findall(r"[A-Za-z]+", verse))
    seen: collections.Counter = collections.Counter()
    for secs in groups:
        for sec in secs:
            for line in sec["text"]:
                seen.update(w.lower() for w in re.findall(r"[A-Za-z]+", line))
    return words | {w for w, n in seen.items() if n >= 3}


def spellfix(groups: list[list[dict]]) -> None:
    """Fix the scans' misread words, and re-join words split in two ("impu rity")."""
    vocab = known_words(groups)

    def fix_line(line: str) -> str:
        line = HEADS.sub(" ", line)
        for pattern, repl in PHRASES:
            line = re.sub(pattern, repl, line)
        line = re.sub(r"[A-Za-z]+", lambda m: MISREAD.get(m.group(0), m.group(0)), line)

        tokens = line.split(" ")
        out: list[str] = []
        for tok in tokens:
            if out:
                a = re.sub(r"^\W+|-$", "", out[-1])
                b = re.sub(r"\W+$", "", tok)
                if (a.isalpha() and b.isalpha() and b[:1].islower() and (a + b).lower() in vocab
                        and (a.lower() not in vocab or b.lower() not in vocab)):
                    out[-1] = out[-1].rstrip("-") + tok
                    continue
            out.append(tok)
        return re.sub(r"\s{2,}", " ", " ".join(out)).strip()

    for secs in groups:
        for sec in secs:
            sec["text"] = [t for t in (fix_line(line) for line in sec["text"]) if t]
            sec["title"] = fix_line(sec["title"])


def repair_drop_caps(groups: list[list[dict]]) -> None:
    """A paragraph that starts lower-case lost its drop cap: complete the first word
    from the most common capitalised word in the books that ends with it."""
    counts: collections.Counter = collections.Counter()
    vocab: collections.Counter = collections.Counter()
    for secs in groups:
        for sec in secs:
            for line in sec["text"]:
                counts.update(w for w in re.findall(r"\b[A-Z][a-z]+\b", line))
                vocab.update(w.lower() for w in re.findall(r"\b[A-Za-z]+\b", line))
    for secs in groups:
        for sec in secs:
            for i, line in enumerate(sec["text"]):
                if CAP in line:  # "H"+"ave" is a word: glue it; "I"+"believe" is not: a space
                    cap, rest = line.split(CAP, 1)
                    word = re.match(r"[A-Za-z]*", rest).group(0)
                    joined = cap + rest if vocab[(cap + word).lower()] >= 2 else f"{cap} {rest}"
                    for pattern, repl in FIXUPS:
                        joined = re.sub(pattern, repl, joined)
                    sec["text"][i] = joined
                    continue
                m = re.match(r"([a-z]+)\b", line)
                if not m:
                    continue
                stem = m.group(1)
                options = [(n, w) for w, n in counts.items() if w.lower().endswith(stem) and len(w) - len(stem) <= 2 and w.lower() != stem]
                if options:
                    sec["text"][i] = max(options)[1] + line[len(stem):]


# ------------------------------------------------------------ the books


def jordanville(pdf: str) -> dict:
    morning = sections(pdf, *JORDANVILLE["morning"], "Morning Prayers")
    day = sections(pdf, *JORDANVILLE["day"], "Prayers during the Day")
    evening = sections(pdf, *JORDANVILLE["evening"], "Prayers before Sleep")
    repair_drop_caps([morning, day, evening])
    spellfix([morning, day, evening])
    # The opening: everything before the Troparia to the Holy Trinity.
    opening_lines: list[str] = []
    for sec in morning:
        if "troparia" in sec["title"].lower():
            break
        opening_lines += sec["text"]
    # The closing: "It is truly meet" to the end of the morning prayers.
    closing: list[str] = []
    for sec in morning:
        for i, line in enumerate(sec["text"]):
            at = line.find("It is truly meet")
            if at >= 0:
                closing = [line[at:]] + sec["text"][i + 1:]
    work = [s for s in day if any("without Me" in line for line in s["text"])]
    return {
        "source": "Prayer Book, Holy Trinity Monastery, Jordanville (private copy)",
        "opening": [{"title": "The Beginning Prayers", "text": opening_lines}],
        "morning_full": morning,
        "work": work[:1] or day[:1],
        "middle": [s for s in day if s not in work[:1]],
        "evening_full": evening,
        "closing": [{"title": "Closing Prayers", "text": closing}] if closing else [],
    }


def horologion(pdf: str) -> dict:
    hours = {}
    for key, (title, first, last) in HOROLOGION.items():
        secs = sections(pdf, first, last, title)
        repair_drop_caps([secs])
        spellfix([secs])
        # The Lenten troparia and the Paschal "Christ is risen" belong to their seasons only.
        secs = [s for s in secs if "lent" not in s["title"].lower()]
        for sec in secs:
            sec["text"] = [re.sub(r"^(?:Amen\.? )?Christ is risen from the dead[^.]*\.\s*", "", line) for line in sec["text"]]
            sec["text"] = [line for line in sec["text"] if line]
            if sec["text"] and re.fullmatch(r"\d+", sec["text"][0]):  # "Psalm" + "24"
                sec["title"] = f"{sec['title']} {sec['text'].pop(0)}"
        secs = [s for s in secs if s["text"]]
        secs[0]["title"] = title
        hours[key] = {"title": title, "sections": secs}
    return {"source": "The Unabbreviated Horologion, Holy Trinity Monastery (private copy)", "hours": hours}


def psalter() -> dict:
    psalms = {}
    for n in range(1, 152):
        url = f"https://www.liturgy.io/orthodox-psalter?chapter={n}&psalt=PSALTER70&style=TTS"
        page = urllib.request.urlopen(urllib.request.Request(url, headers={"User-Agent": "OrthodoxReader/1.0"}),
                                      timeout=30).read().decode("utf-8")
        page = re.sub(r"<script.*?</script>|<style.*?</style>", "", page, flags=re.S)
        lines = [l.strip() for l in html.unescape(re.sub(r"<[^>]+>", "\n", page)).splitlines() if l.strip()]
        start = lines.index(f"Psalm {n}") if f"Psalm {n}" in lines else None
        stop = next(i for i, l in enumerate(lines) if l.startswith("Here you can access"))
        if start is None:
            print(f"  psalm {n}: not found")
            continue
        psalms[str(n)] = {"title": f"Psalm {n}", "verses": lines[start + 1:stop]}
        print(f"  psalm {n}: {len(lines[start + 1:stop])} lines")
        time.sleep(0.5)
    return {"source": "The Psalter According to the Seventy, Holy Transfiguration Monastery, via liturgy.io (private copy)",
            "psalms": psalms}


def _paragraphs(text: str) -> list[str]:
    """Book text as spoken paragraphs: line breaks inside a paragraph joined."""
    text = re.sub(r"-\n(?=[a-z])", "", text or "")
    paras = [re.sub(r"\s+", " ", p).strip() for p in re.split(r"\n\s*\n|\n(?=[A-Z\"“‘(])", text)]
    return [p for p in paras if p]


def works_from_bot(bot: str, chrysostom_count: int = 240) -> dict:
    """Whole works from the Daily Readings bot's parsed books (its cache/ and PDFs):
    every life in the Prologue of Ohrid with its homily and reflection, every thought
    of St. Theophan, and a few hundred passages of St. John Chrysostom."""
    root = Path(bot)
    prologue = json.loads((root / "cache/prologue.json").read_text(encoding="utf-8"))["days"]
    lives, homilies, reflections = [], [], []
    for day in prologue:
        when = f"{day['month']}/{day['day']}"
        for saint in day.get("saints") or []:
            title = saint["title"].strip().title().replace("'S ", "'s ")
            lives.append({"title": title, "text": _paragraphs(saint["body"]), "when": when})
        if day.get("homily"):
            homilies.append({"title": "A Homily from the Prologue", "text": _paragraphs(day["homily"]), "when": when})
        if day.get("reflection"):
            reflections.append({"title": "A Reflection from the Prologue", "text": _paragraphs(day["reflection"]), "when": when})
    theophan = [{"title": f"St. Theophan the Recluse: {t['title']}" + (f" ({t['citation'].strip()})" if t.get("citation") else ""),
                 "text": _paragraphs(t["body"])}
                for t in json.loads((root / "cache/theophan.json").read_text(encoding="utf-8"))["thoughts"]]
    chrysostom = []
    try:
        import random

        sys.path.insert(0, str(root))
        import library as bot_library  # the bot's own reader, with its PDF paths

        bot_library.load_dotenv(root / ".env")
        books = bot_library.Library()
        books.chrysostom.load()
        rng = random.Random(7)
        seen = set()
        for _ in range(chrysostom_count * 3):
            try:
                passage = books.chrysostom.excerpt(rng)
            except Exception:
                continue
            if passage["title"] in seen:
                continue
            seen.add(passage["title"])
            chrysostom.append({"title": f"St. John Chrysostom: {passage['title'].title()}",
                               "text": _paragraphs(passage["body"])})
            if len(chrysostom) >= chrysostom_count:
                break
        books.close()
    except Exception as exc:  # the Chrysostom PDF is optional
        print("  Chrysostom skipped:", exc)
    return {"source": "The Prologue from Ohrid, Thoughts for Each Day, St. John Chrysostom (private copies)",
            "prologue_lives": lives, "prologue_homilies": homilies, "prologue_reflections": reflections,
            "theophan": theophan, "chrysostom": chrysostom}


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--jordanville")
    ap.add_argument("--horologion")
    ap.add_argument("--psalter", choices=["liturgy.io"])
    ap.add_argument("--bot", help="the Daily Readings bot folder (its parsed Prologue, Theophan, Chrysostom)")
    args = ap.parse_args()
    OUT.mkdir(parents=True, exist_ok=True)
    if args.bot:
        data = works_from_bot(args.bot)
        (OUT / "works.json").write_text(json.dumps(data, indent=1, ensure_ascii=False), encoding="utf-8")
        print("works.json:", {k: len(v) for k, v in data.items() if isinstance(v, list)})
    if args.jordanville:
        data = jordanville(args.jordanville)
        (OUT / "prayers.json").write_text(json.dumps(data, indent=1, ensure_ascii=False), encoding="utf-8")
        print("prayers.json:", {k: len(v) for k, v in data.items() if isinstance(v, list)})
    if args.horologion:
        data = horologion(args.horologion)
        (OUT / "hours.json").write_text(json.dumps(data, indent=1, ensure_ascii=False), encoding="utf-8")
        print("hours.json:", {k: len(v["sections"]) for k, v in data["hours"].items()})
    if args.psalter:
        data = psalter()
        (OUT / "psalter.json").write_text(json.dumps(data, indent=1, ensure_ascii=False), encoding="utf-8")
        print("psalter.json:", len(data["psalms"]), "psalms")
    print("Written to", OUT)


if __name__ == "__main__":
    main()
