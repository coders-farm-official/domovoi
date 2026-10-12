"""Numbers -> English words (American style), for the phonemizer's text
normalizer. Pure standard library; ports line for line to Kotlin."""

ONES = ["zero", "one", "two", "three", "four", "five", "six", "seven", "eight", "nine", "ten",
        "eleven", "twelve", "thirteen", "fourteen", "fifteen", "sixteen", "seventeen", "eighteen",
        "nineteen"]
TENS = ["", "", "twenty", "thirty", "forty", "fifty", "sixty", "seventy", "eighty", "ninety"]
SCALES = [(10 ** 12, "trillion"), (10 ** 9, "billion"), (10 ** 6, "million"), (1000, "thousand")]
ORD_IRREG = {"one": "first", "two": "second", "three": "third", "five": "fifth", "eight": "eighth",
             "nine": "ninth", "twelve": "twelfth"}


def _under_1000(n):
    out = []
    if n >= 100:
        out += [ONES[n // 100], "hundred"]
        n %= 100
    if n >= 20:
        out.append(TENS[n // 10])
        if n % 10:
            out.append(ONES[n % 10])
    elif n or not out:
        out.append(ONES[n])
    return out


def cardinal(n):
    if n < 0:
        return "minus " + cardinal(-n)
    if n == 0:
        return "zero"
    if n >= 10 ** 15:
        return digits(str(n))  # past the scale words: read the digits
    out = []
    for val, name in SCALES:
        if n >= val:
            out += _under_1000(n // val) + [name]
            n %= val
    if n:
        out += _under_1000(n)
    return " ".join(out)


def ordinal(n):
    words = cardinal(n).split()
    last = words[-1]
    if last in ORD_IRREG:
        words[-1] = ORD_IRREG[last]
    elif last.endswith("y"):
        words[-1] = last[:-1] + "ieth"
    else:
        words[-1] = last + "th"
    return " ".join(words)


def year(n):
    if 2000 <= n <= 2009:
        return cardinal(n)
    hi, lo = divmod(n, 100)
    if lo == 0:
        return cardinal(hi) + " hundred"
    if lo < 10:
        return cardinal(hi) + " oh " + cardinal(lo)
    return cardinal(hi) + " " + cardinal(lo)


def digits(s):
    return " ".join("oh" if c == "0" else ONES[int(c)] for c in s if c.isdigit())


def decimal(s):
    """'3.14' -> 'three point one four'."""
    whole, _, frac = s.partition(".")
    w = cardinal(int(whole.replace(",", "") or "0"))
    if not frac:
        return w
    return w + " point " + " ".join(ONES[int(c)] for c in frac)
