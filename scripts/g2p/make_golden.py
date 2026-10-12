"""Write (or check) the phonemizer's golden fixture, domovoi/phonemizer/golden.json.

The fixture pins what the converter says for a few hundred texts: the
comparison sentences, Domovoi's own phrases, numbers/money/times/dates/
units, heteronyms, names, artists and the common-words list (inputs in
golden_inputs/, one text per line), plus "say it like" lists and entries
that must be refused. The Python tests and the Kotlin port both assert
against the same file; its format is described in domovoi/phonemizer/README.md.

    python scripts/g2p/make_golden.py           # rewrite the fixture
    python scripts/g2p/make_golden.py --check   # exit 1 if it is out of date

Regenerate only on purpose (a converter or data change you mean), and read
the diff: every changed line is a word the voices will now say differently.
"""
import hashlib
import json
import os
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
REPO = os.path.dirname(os.path.dirname(HERE))
sys.path.insert(0, REPO)

from domovoi.phonemizer import (  # noqa: E402
    DATA_DIR,
    Phonemizer,
    PronunciationError,
    load_default_pronunciations,
    phoneme_ids,
    unknown_symbols,
)

OUT = os.path.join(REPO, "domovoi", "phonemizer", "golden.json")
GROUPS = ["sentences", "phrases", "function_words", "heteronyms", "numbers", "normalization",
          "names", "artists", "common_words"]
DATA_FILES = ["cmudict/cmudict.dict", "lexicon_corrections.txt", "function_words_en.tsv",
              "pronunciations_default.txt", "g2p/g2p.int8.onnx", "g2p/g2p_vocab.json",
              "phoneme_ids_en.json"]
NEAR_TIE = 0.1

# (list in force for the case, text). The list replaces the default one.
SAY_IT_LIKE_CASES = [
    ([("Domovoi", "doh moe voy")], "Hey Domovoi."),
    ([("Domovoi", "DOM-uh-voy")], "Hey Domovoi."),
    ([("Domovoi", "/dˈəʊməʊvɔɪ/")], "Domovoi, Domovoi's turn."),
    ([("Siobhan", "shiv-AWN")], "Siobhan is home."),
    ([("Saoirse", "SEER-shuh")], "Saoirse sings."),
    ([("Hozier", "HOE-zee-er")], "Hozier and Lorde."),
    ([("Kamron", "CAM ron")], "Kamron asked."),
    ([("Ollama", "oh-LAH-muh")], "Ollama is running."),
    ([("SZA", "SIZ-uh")], "Play SZA."),
    ([("Beyoncé", "bee-YON-say")], "Beyonce, BEYONCÉ and Beyoncé's."),
    ([("live", "liv")], "We saw a live show."),
    ([("Nguyen", "win"), ("nguyen", "NOO-yen")], "Nguyen's keys."),
    ([], "Hey Domovoi. Nguyen is here."),
]

# (word, say it like) pairs every port must refuse.
INVALID = [
    ("", "win"),
    ("two words", "win"),
    ("AC/DC", "ay see dee see"),
    ("x" * 49, "win"),
    ("Bob", ""),
    ("Bob", "b@b"),
    ("Bob", "/wɪn"),
    ("Bob", "/$/"),
    ("Bob", "/w€n/"),
    ("Bob", "-".join(["la"] * 13)),
    ("Bob", "shh"),
]


def sha256(path):
    h = hashlib.sha256()
    with open(path, "rb") as f:
        h.update(f.read())
    return h.hexdigest()


def margin(ph, word):
    """Smallest top-1 / top-2 logit gap of the model over ``word``."""
    np = ph.np
    ids = [ph.g2p_letters[c] for c in word if c in ph.g2p_letters][:ph.g2p_max_chars]
    lg = ph.g2p.run(None, {"chars": np.array([ids], dtype=np.int64)})[0][0]
    s = np.sort(lg, axis=-1)
    return float((s[:, -1] - s[:, -2]).min())


def case(ph, group, n, text, pron=None):
    detailed = ph.phonemize_detailed(text)
    phon = [p for p, _ in detailed]
    for p in phon:
        bad = unknown_symbols(p)
        if bad:
            raise SystemExit(f"{text!r}: symbols the voices lack: {bad}")
    c = {"id": f"{group}-{n:03d}", "group": group, "text": text}
    if pron is not None:
        c["pronunciations"] = [list(x) for x in pron]
        c["resolved"] = {k: ("/" + v.ipa + "/" if v.ipa is not None else " ".join(v.phones))
                         for k, v in ph.pronunciations().items()}
    c["phonemes"] = phon
    c["ids"] = [phoneme_ids(p) for p in phon]
    model = [[it["w"], " ".join(it["ph"])] for _, items in detailed for it in items if it.get("src") == "model"]
    if model:
        c["model"] = model
        ties = [w for w, _ in model if margin(ph, w) < NEAR_TIE]
        if ties:
            c["near_tie"] = ties
    return c


def build():
    ph = Phonemizer()
    if not ph.has_model:
        raise SystemExit("onnxruntime is needed to build the fixture")
    default = load_default_pronunciations(DATA_DIR)
    cases, counts = [], {}
    for g in GROUPS:
        texts = [t.strip() for t in open(os.path.join(HERE, "golden_inputs", g + ".txt"), encoding="utf-8")
                 if t.strip()]
        counts[g] = len(texts)
        for n, t in enumerate(texts, 1):
            cases.append(case(ph, g, n, t))
    for n, (pron, text) in enumerate(SAY_IT_LIKE_CASES, 1):
        ph.set_pronunciations(pron)
        cases.append(case(ph, "say_it_like", n, text, pron))
    ph.set_pronunciations(default)
    counts["say_it_like"] = len(SAY_IT_LIKE_CASES)
    for w, s in INVALID:
        try:
            ph.resolve_pronunciation(w, s)
        except PronunciationError:
            continue
        raise SystemExit(f"expected {w!r} = {s!r} to be refused")
    ties = sorted({w for c in cases for w in c.get("near_tie", [])})
    header = {
        "format": "domovoi-phonemizer-golden/1",
        "about": [
            "What Domovoi's text-to-phoneme converter (domovoi/phonemizer) must produce. Generated by "
            "scripts/g2p/make_golden.py; asserted by domovoi/tests/test_phonemizer_golden.py and by the "
            "Kotlin port. Format: domovoi/phonemizer/README.md, 'The golden fixture'.",
            "Each case: text -> phonemes (one string per sentence, the voice's symbols, UTF-8) -> ids "
            "(one list per sentence). Cases without 'pronunciations' use default_pronunciations; a case "
            "with 'pronunciations' replaces the list with exactly those entries ([] = none).",
            "'model' lists the words the out-of-dictionary model pronounced (ARPAbet), for debugging. "
            "'near_tie' names model words whose choice is within near_tie_margin logits of another "
            "phoneme: exact on x86-64 ONNX Runtime, possibly one phoneme off on another runtime.",
            "'invalid_say_it_like' entries must be refused.",
        ],
        "data_sha256": {f: sha256(os.path.join(DATA_DIR, f)) for f in DATA_FILES},
        "default_pronunciations": [list(x) for x in default],
        "ids": {
            "voice": "en_US-norman-medium",
            "same_for": ["en_US-kristin-medium", "en_US-ljspeech-medium"],
            "map_file": "data/phoneme_ids_en.json",
            "rule": "NFD-normalize the phoneme string; ids = map['^'] + map['_'], then for each code point "
                    "in the map: map[c] + map['_'] (code points not in the map are skipped), then map['$'].",
        },
        "near_tie_margin": NEAR_TIE,
        "near_tie_words": ties,
        "counts": counts,
    }
    return header, cases


def render(header, cases):
    lines = ["{"]
    for k, v in header.items():
        lines.append(f"  {json.dumps(k)}: {json.dumps(v, ensure_ascii=False)},")
    lines.append('  "invalid_say_it_like": [')
    for i, (w, s) in enumerate(INVALID):
        lines.append("    " + json.dumps([w, s], ensure_ascii=False) + ("," if i < len(INVALID) - 1 else ""))
    lines.append("  ],")
    lines.append('  "cases": [')
    for i, c in enumerate(cases):
        lines.append("    " + json.dumps(c, ensure_ascii=False) + ("," if i < len(cases) - 1 else ""))
    lines.append("  ]")
    lines.append("}")
    return "\n".join(lines) + "\n"


def main():
    text = render(*build())
    json.loads(text)
    if "--check" in sys.argv:
        with open(OUT, encoding="utf-8") as f:
            same = f.read() == text
        print("golden.json is up to date" if same else "golden.json is OUT OF DATE (run make_golden.py)")
        sys.exit(0 if same else 1)
    with open(OUT, "w", encoding="utf-8", newline="\n") as f:
        f.write(text)
    print(f"wrote {OUT}: {text.count(chr(10))} lines")


if __name__ == "__main__":
    main()
