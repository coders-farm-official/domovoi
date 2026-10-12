r"""Text normalization for the phonemizer: numbers, money, dates, times, %,
degrees, units, abbreviations, acronyms, stylized names. Regular
expressions and tables only, so it ports line for line to Kotlin (Python's
``re`` here is Unicode-aware: ``\w``, ``\b`` and ``\d`` match beyond ASCII).

normalize(text) -> list of sentences; a sentence is a list of tokens:
  ("w", word, spell)   spell=True -> say the letters
  ("p", ",")           punctuation the voices know: , . ! ? ; :
"""
import re
import unicodedata

from . import number_words as N

MONTHS = ["january", "february", "march", "april", "may", "june", "july", "august", "september",
          "october", "november", "december"]
MONTH_RE = "|".join(m.capitalize() for m in MONTHS) + "|" + "|".join(
    m[:3].capitalize() + r"\." for m in MONTHS if len(m) > 4)

ABBREV = {
    "mr.": "mister", "mrs.": "missus", "ms.": "miz", "dr.": "doctor", "prof.": "professor",
    "jr.": "junior", "sr.": "senior", "st.": "saint", "mt.": "mount", "ave.": "avenue",
    "blvd.": "boulevard", "rd.": "road", "vs.": "versus", "etc.": "et cetera", "approx.": "approximately",
    "inc.": "incorporated", "ltd.": "limited", "co.": "company", "dept.": "department",
    "no.": "number", "min.": "minutes", "hr.": "hour", "hrs.": "hours", "sec.": "seconds",
    "jan.": "january", "feb.": "february", "mar.": "march", "apr.": "april", "aug.": "august",
    "sep.": "september", "sept.": "september", "oct.": "october", "nov.": "november", "dec.": "december",
    "mon.": "monday", "tue.": "tuesday", "tues.": "tuesday", "wed.": "wednesday", "thu.": "thursday",
    "thurs.": "thursday", "fri.": "friday", "sat.": "saturday", "sun.": "sunday",
}
UNITS = {
    "mph": "miles per hour", "km/h": "kilometers per hour", "kph": "kilometers per hour",
    "km": "kilometers", "cm": "centimeters", "mm": "millimeters", "kg": "kilograms",
    "lb": "pounds", "lbs": "pounds", "oz": "ounces", "ft": "feet", "mi": "miles",
    "min": "minutes", "mins": "minutes", "hr": "hours", "hrs": "hours", "sec": "seconds",
    "secs": "seconds", "ms": "milliseconds", "gb": "gigabytes", "mb": "megabytes", "tb": "terabytes",
    "kb": "kilobytes", "hz": "hertz", "khz": "kilohertz", "mhz": "megahertz", "ghz": "gigahertz",
    "kw": "kilowatts", "kwh": "kilowatt hours", "mah": "milliamp hours", "ml": "milliliters",
}
UNIT_SING = {"miles per hour": "mile per hour", "kilometers per hour": "kilometer per hour",
             "feet": "foot", "hours": "hour", "minutes": "minute", "seconds": "second",
             "pounds": "pound", "ounces": "ounce", "miles": "mile", "kilometers": "kilometer",
             "centimeters": "centimeter", "millimeters": "millimeter", "kilograms": "kilogram",
             "milliseconds": "millisecond", "gigabytes": "gigabyte", "megabytes": "megabyte",
             "terabytes": "terabyte", "kilobytes": "kilobyte", "kilowatts": "kilowatt",
             "kilowatt hours": "kilowatt hour", "milliamp hours": "milliamp hour",
             "milliliters": "milliliter"}
SYMBOLS = {"&": " and ", "+": " plus ", "=": " equals ", "@": " at ", "#": " number ", "°": " degrees "}
CURRENCY = {"$": ("dollar", "dollars", "cent", "cents"), "£": ("pound", "pounds", "penny", "pence"),
            "€": ("euro", "euros", "cent", "cents")}
WORD_CAPS_OK = {"OK": "okay", "TV": None}  # None -> spell
KEEP_PUNCT = ",.!?;:"


def _money(m):
    sym, whole, cents, scale = m.group(1), m.group(2), m.group(3), m.group(4)
    one, many, c1, cmany = CURRENCY[sym]
    w = int(whole.replace(",", ""))
    scale = {"K": "thousand", "M": "million", "B": "billion"}.get(scale, scale)
    if scale:
        num = N.decimal(whole + ("." + cents if cents else ""))
        return f" {num} {scale.lower()} {many} "
    if cents and len(cents) != 2:
        return f" {N.decimal(whole + '.' + cents)} {many} "
    c = int(cents) if cents else 0
    parts = []
    if w or not c:
        parts.append(f"{N.cardinal(w)} {one if w == 1 else many}")
    if c:
        parts.append(f"{N.cardinal(c)} {c1 if c == 1 else cmany}")
    return " " + " and ".join(parts) + " "


def _time(m):
    h, mi, ampm = int(m.group(1)), int(m.group(2)), m.group(3)
    s = N.cardinal(h)
    if mi == 0:
        s += "" if ampm else " o'clock"
    elif mi < 10:
        s += " oh " + N.cardinal(mi)
    else:
        s += " " + N.cardinal(mi)
    if ampm:
        s += _spell("AM" if ampm[0].lower() == "a" else "PM")
    return " " + s + _keep_period(m)


def _keep_period(m):
    """'9 a.m.' at the end of a sentence: the abbreviation's dot is also the full stop."""
    if not m.group(0).endswith("."):
        return " "
    rest = m.string[m.end():]
    if rest.strip() == "" or re.match(r"\s+[A-Z\"']", rest):
        return " . "
    return " "


def _ampm(m):
    return " " + N.cardinal(int(m.group(1))) + _spell("AM" if m.group(2).lower() == "a" else "PM") + _keep_period(m)


def _temp(m):
    num = m.group(1)
    words = N.decimal(num) if "." in num else N.cardinal(int(num))
    unit = {"f": " Fahrenheit", "c": " Celsius"}.get((m.group(2) or "").lower(), "")
    deg = "degree" if num in ("1", "-1") else "degrees"
    return f" {words} {deg}{unit} "


def _number_word(s):
    s = s.replace(",", "")
    if "." in s:
        return N.decimal(s)
    if len(s) > 1 and s.startswith("0"):
        return N.digits(s)
    n = int(s)
    if len(s) >= 10:
        return N.digits(s)
    return N.cardinal(n)


def _plain_number(m):
    s = m.group(0)
    if re.fullmatch(r"\d{4}", s):
        n = int(s)
        if 1100 <= n <= 1999 or 2010 <= n <= 2099:
            return " " + N.year(n) + " "
    return " " + _number_word(s) + " "


def _date_md(m):
    mo, d, y = int(m.group(1)), int(m.group(2)), m.group(3)
    if not (1 <= mo <= 12 and 1 <= d <= 31):
        return m.group(0)
    s = f" {MONTHS[mo - 1]} {N.ordinal(d)}"
    if y:
        yy = int(y) if len(y) == 4 else 2000 + int(y)
        s += " " + N.year(yy)
    return s + " "


def _spell(s):
    return " <" + "".join(c.upper() for c in s if c.isalnum()) + "> "


def _units(m):
    num, unit = m.group(1), m.group(2)
    words = UNITS[unit.lower()]
    if num == "1":
        words = UNIT_SING.get(words, words)
    return f" {num} {words} "


def prenormalize(text):
    t = unicodedata.normalize("NFC", text)
    t = t.replace("’", "'").replace("‘", "'").replace("“", '"').replace("”", '"')
    t = t.replace("—", ", ").replace("–", " to ").replace("…", "...")
    # stylized names: $ glued to letters is an S ($uicideboy$ -> suicideboys)
    t = re.sub(r"(?<=[A-Za-z])\$|\$(?=[A-Za-z])", "s", t)
    # money
    t = re.sub(r"([$£€])\s?(\d[\d,]*)(?:\.(\d+))?(?:\s?(thousand|million|billion|trillion|[KMB]\b))?",
               _money, t)
    # temperature / degrees
    t = re.sub(r"(-?\d+(?:\.\d+)?)\s?°\s?([FC])?\b", _temp, t)
    # percent
    t = re.sub(r"(-?\d+(?:\.\d+)?)\s?%", lambda m: f" {m.group(1)} percent ", t)
    # clock times
    t = re.sub(r"\b(\d{1,2}):(\d{2})(?:\s*([AaPp]\.?\s?[Mm]\.?)(?![A-Za-z]))?", _time, t)
    t = re.sub(r"\b(\d{1,2})\s*([AaPp])\.?\s?[Mm]\b\.?", _ampm, t)
    # phone numbers: digit by digit
    t = re.sub(r"(?:\(\d{3}\)\s?|\b\d{3}[-.])?\b\d{3}[-.]\d{4}\b", lambda m: " " + N.digits(m.group(0)) + " ", t)
    # numeric dates m/d(/y)
    t = re.sub(r"\b(\d{1,2})/(\d{1,2})(?:/(\d{4}|\d{2}))?\b", _date_md, t)
    # "24/7"
    t = t.replace("24/7", " twenty four seven ")
    # units glued to numbers
    t = re.sub(r"(\d+(?:\.\d+)?)\s?(" + "|".join(re.escape(u) for u in sorted(UNITS, key=len, reverse=True))
               + r")\b", lambda m: _units(m), t, flags=re.I)
    # ordinals
    t = re.sub(r"\b(\d+)(st|nd|rd|th)\b", lambda m: " " + N.ordinal(int(m.group(1))) + " ", t)
    # Month + day -> ordinal ("October 14" -> "October fourteenth")
    t = re.sub(r"\b(" + MONTH_RE + r")\s+(\d{1,2})\b(?![:.,]?\d)",
               lambda m: f"{m.group(1)} {N.ordinal(int(m.group(2)))}", t)
    # abbreviations
    t = re.sub(r"\b[A-Za-z]{1,6}\.", _abbrev, t)
    # dotted acronyms U.S. / e.g.
    t = re.sub(r"\b(?:[A-Za-z]\.){2,}", lambda m: _spell(m.group(0)) + _keep_period(m), t)
    # AC/DC style: short capitals around a slash -> letters
    t = re.sub(r"\b([A-Z]{1,4})/([A-Z]{1,4})\b", lambda m: _spell(m.group(1)) + _spell(m.group(2)), t)
    t = re.sub(r"(?<=\w)/(?=\w)", " slash ", t)
    # decimals and integers
    t = re.sub(r"(?:(?<![\w-])-)?\d[\d,]*(?:\.\d+)?", lambda m: (" minus " if m.group(0).startswith("-") else "")
               + _plain_number(_Sub(m.group(0).lstrip("-"))), t)
    for k, v in SYMBOLS.items():
        t = t.replace(k, v)
    return t


class _Sub:
    def __init__(self, s):
        self.s = s

    def group(self, i):
        return self.s


TITLES = {"mr.", "mrs.", "ms.", "dr.", "prof.", "mt.", "no."}


def _abbrev(m):
    k = m.group(0).lower()
    if k not in ABBREV:
        return m.group(0)
    rest = m.string[m.end():]
    w = ABBREV[k]
    if k == "st.":
        w = "saint" if re.match(r"\s+[A-Z]", rest) else "street"
    end = "." if (k not in TITLES and rest.strip() == "") else ""
    return " " + w + " " + end


def strip_accents(w):
    return "".join(c for c in unicodedata.normalize("NFKD", w) if not unicodedata.combining(c))


TOKEN_RE = re.compile(r"<[A-Z0-9]+>|[A-Za-zÀ-ɏ](?:[A-Za-zÀ-ɏ'\-]*[A-Za-zÀ-ɏ])?'?|[,.!?;:]")


def normalize(text):
    t = prenormalize(text)
    sents, cur = [], []
    for tok in TOKEN_RE.findall(t):
        if tok in KEEP_PUNCT:
            if tok == "." and cur and cur[-1][0] == "p":
                continue
            if not cur:
                continue
            cur.append(("p", tok))
            if tok in ".!?":
                sents.append(cur)
                cur = []
            continue
        if tok.startswith("<"):
            cur.append(("w", tok[1:-1].lower(), True))
            continue
        if tok.isupper() and len(tok) >= 2 and tok.isalpha():
            if tok in WORD_CAPS_OK and WORD_CAPS_OK[tok]:
                cur.append(("w", WORD_CAPS_OK[tok], False))
                continue
            if len(tok) <= 3 or not re.search(r"[AEIOU]", tok):
                cur.append(("w", tok.lower(), True))
                continue
        cur.append(("w", strip_accents(tok).lower(), False))
    if cur:
        sents.append(cur)
    return sents
