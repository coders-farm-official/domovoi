"""Permissive English text-to-phoneme converter for Piper voices trained on
espeak-ng's British "en" phoneme set (Domovoi's voices: norman, kristin,
ljspeech, all medium).

Pipeline: normalize -> words -> ARPAbet (the owner's "say it like" list,
then context rules for heteronyms, CMUdict + built-in corrections, compound
split, and a small CTC model for words in no dictionary) -> IPA with stress
in the "en" conventions -> one phoneme string per sentence.

No espeak-ng code or data is used. Licences: CMUdict BSD-2-Clause style
(data/cmudict/LICENSE); everything else here is Domovoi's own (MIT). The
README beside this file documents the data, the "say it like" format and
the golden fixture the Kotlin port shares.
"""
import json
import logging
import os
import re

from .normalize import normalize
from .pronunciations import PronunciationError, load_default_pronunciations, parse_say_it_like

log = logging.getLogger(__name__)

DATA_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), "data")
G2P_MODEL = "g2p.int8.onnx"
_MODEL_CACHE_MAX = 20000

VOWELS = {"AA", "AE", "AH", "AO", "AW", "AY", "EH", "ER", "EY", "IH", "IY", "OW", "OY", "UH", "UW"}
CONS_IPA = {"B": "b", "CH": "tʃ", "D": "d", "DH": "ð", "F": "f", "G": "ɡ", "HH": "h", "JH": "dʒ", "K": "k",
            "L": "l", "M": "m", "N": "n", "NG": "ŋ", "P": "p", "R": "ɹ", "S": "s", "SH": "ʃ", "T": "t",
            "TH": "θ", "V": "v", "W": "w", "Y": "j", "Z": "z", "ZH": "ʒ"}
LETTERS = {"a": "EY1", "b": "B IY1", "c": "S IY1", "d": "D IY1", "e": "IY1", "f": "EH1 F", "g": "JH IY1",
           "h": "EY1 CH", "i": "AY1", "j": "JH EY1", "k": "K EY1", "l": "EH1 L", "m": "EH1 M", "n": "EH1 N",
           "o": "OW1", "p": "P IY1", "q": "K Y UW1", "r": "AA1 R", "s": "EH1 S", "t": "T IY1", "u": "Y UW1",
           "v": "V IY1", "w": "D AH1 B AH0 L Y UW0", "x": "EH1 K S", "y": "W AY1", "z": "Z IY1",
           "0": "Z IH1 R OW0", "1": "W AH1 N", "2": "T UW1", "3": "TH R IY1", "4": "F AO1 R", "5": "F AY1 V",
           "6": "S IH1 K S", "7": "S EH1 V AH0 N", "8": "EY1 T", "9": "N AY1 N"}
MODAL_TO = {"to", "will", "would", "can", "could", "should", "must", "might", "may", "shall", "do", "does",
            "did", "don't", "didn't", "doesn't", "won't", "can't", "couldn't", "wouldn't", "shouldn't",
            "i'd", "you'd", "we'd", "they'd", "he'd", "she'd", "i'll", "you'll", "we'll", "they'll",
            "he'll", "she'll", "let's", "please", "and", "or", "gonna", "wanna", "let", "help"}
PERFECT = {"have", "has", "had", "i've", "you've", "we've", "they've", "was", "were", "been", "being",
           "is", "are", "be", "having", "already", "just", "never", "ever"}
SUBJ = {"i", "you", "we", "they", "he", "she", "who", "people"}
DET = {"a", "an", "the", "this", "that", "my", "your", "his", "her", "our", "their", "its", "new", "world",
       "vinyl", "track", "personal", "every", "each", "no"}


IH_CONTEXT = {"T", "D", "S", "Z", "K", "G", "JH", "CH", "SH", "NG", "F", "V", "P", "B", "TH"}


def base(p):
    return p.rstrip("012")


def is_vowel(p):
    return base(p) in VOWELS


def possessive_arpa(phones):
    """The ARPAbet ending of "'s" after ``phones``."""
    last = base(phones[-1]) if phones else ""
    if last in {"S", "Z", "SH", "ZH", "CH", "JH"}:
        return ["IH0", "Z"]
    return ["S"] if last in {"P", "T", "K", "F", "TH"} else ["Z"]


def possessive_ipa(ipa):
    """The IPA ending of "'s" after a phoneme string."""
    if ipa.endswith(("s", "z", "ʃ", "ʒ")):
        return "ɪz"
    return "s" if ipa.endswith(("p", "t", "k", "f", "θ")) else "z"


_IPA_VOWEL_START = re.compile(r"[ˈˌ]*[aeiouæɑɒɔəɛɜɪʊʌɐ]")


class Phonemizer:
    """Text -> Piper phoneme strings. Load once (about half a second for the
    dictionary) and share: :meth:`phonemize` is safe to call from several
    threads, and :meth:`set_pronunciations` swaps the owner's list in one
    assignment."""

    def __init__(self, data_dir=DATA_DIR, *, model=True, pronunciations=None):
        self.data_dir = data_dir
        self.lex = {}
        with open(os.path.join(data_dir, "cmudict", "cmudict.dict"), encoding="utf-8") as f:
            for line in f:
                line = line.split("#")[0].strip()
                if not line:
                    continue
                w, *ph = line.split()
                w = re.sub(r"\(\d+\)$", "", w)
                self.lex.setdefault(w, []).append(ph)
        seen = set()
        with open(os.path.join(data_dir, "lexicon_corrections.txt"), encoding="utf-8") as f:
            for line in f:
                line = line.strip()
                if not line or line.startswith("#"):
                    continue
                w, *ph = line.split()
                if w not in seen:
                    self.lex[w] = []
                    seen.add(w)
                self.lex[w].append(ph)
        self.fw = {}
        with open(os.path.join(data_dir, "function_words_en.tsv"), encoding="utf-8") as f:
            for line in f:
                if line.startswith("#") or not line.strip():
                    continue
                w, d, v, fin = line.rstrip("\n").split("\t")
                self.fw[w] = (d, d if v == "-" else v, d if fin == "-" else fin)
        self.g2p = None
        self._model_cache = {}
        if model:
            self._load_model(os.path.join(data_dir, "g2p"))
        self._pron = {}
        self.set_pronunciations(load_default_pronunciations(data_dir) if pronunciations is None
                                else pronunciations)

    def _load_model(self, g2p_dir):
        try:
            import numpy as np
            import onnxruntime as ort
        except ImportError as e:  # the dictionary still works; unknown words get spelled
            log.warning("phonemizer: no ONNX Runtime (%s); words in no dictionary will be spelled out", e)
            return
        so = ort.SessionOptions()
        so.intra_op_num_threads = 1
        so.inter_op_num_threads = 1
        self.np = np
        self.g2p = ort.InferenceSession(os.path.join(g2p_dir, G2P_MODEL), so,
                                        providers=["CPUExecutionProvider"])
        with open(os.path.join(g2p_dir, "g2p_vocab.json"), encoding="utf-8") as f:
            v = json.load(f)
        self.g2p_letters = {c: i + 1 for i, c in enumerate(v["letters"])}
        self.g2p_phones = v["phones"]
        self.g2p_max_chars = int(v["max_chars"])

    @property
    def has_model(self):
        """Whether words in no dictionary go through the model (else spelled)."""
        return self.g2p is not None

    # ---------------- the owner's "say it like" list ----------------
    def set_pronunciations(self, entries):
        """Replace the "say it like" list. ``entries``: a mapping of word ->
        say-it-like text, or an iterable of (word, say_it_like) pairs (a
        later pair for the same word wins). Raises
        :class:`PronunciationError` naming the first entry it can't use;
        the old list stays in force then."""
        pairs = entries.items() if hasattr(entries, "items") else entries
        resolved = {}
        for word, say in pairs:
            p = self.resolve_pronunciation(word, say)
            resolved[p.key] = p
        self._pron = resolved

    def pronunciations(self):
        """The list in force: key (normalized word) -> :class:`Pronunciation`."""
        return dict(self._pron)

    def resolve_pronunciation(self, word, say):
        """One "say it like" entry -> :class:`Pronunciation`: ARPAbet read
        from the respelling, or the advanced /phonemes/ form as given."""
        entry = parse_say_it_like(word, say)
        if entry.ipa is not None:
            return entry
        phones = []
        for k, piece in enumerate(entry.pieces):
            ph, _ = self.word_arpa(piece.lower())
            ph = list(ph)
            if k == entry.stressed:
                if not any(p.endswith("1") for p in ph):
                    for j, p in enumerate(ph):
                        if is_vowel(p):
                            ph[j] = base(p) + "1"
                            break
            else:
                capital = entry.has_capitals and piece.isupper()
                for j, p in enumerate(ph):
                    if not is_vowel(p):
                        continue
                    if not capital and base(p) in ("AH", "ER"):
                        ph[j] = base(p) + "0"  # an unstressed "uh" / "er" is a schwa
                    elif p.endswith("1"):
                        ph[j] = base(p) + "2"
            phones += ph
        if not any(is_vowel(p) for p in phones):
            raise PronunciationError(f"{word}: {say!r} has no vowel sound")
        return entry.with_phones(tuple(phones))

    # ---------------- word -> ARPAbet ----------------
    def model_arpa(self, word):
        if self.g2p is None:
            return None
        cached = self._model_cache.get(word)
        if cached is not None:
            return list(cached)
        ids = [self.g2p_letters[c] for c in word if c in self.g2p_letters][:self.g2p_max_chars]
        if not ids:
            return None
        lg = self.g2p.run(None, {"chars": self.np.array([ids], dtype=self.np.int64)})[0][0]
        out, prev = [], 0
        for i in lg.argmax(-1).tolist():
            if i != prev and i != 0:
                out.append(self.g2p_phones[i])
            prev = i
        if len(self._model_cache) >= _MODEL_CACHE_MAX:
            self._model_cache.clear()
        self._model_cache[word] = tuple(out)
        return out

    def heteronym(self, w, prev, nxt, initial):
        if w == "read":
            if prev in MODAL_TO or initial:
                return "R IY1 D"
            if prev in PERFECT or prev in SUBJ:
                return "R EH1 D"
            return "R IY1 D"
        if w == "live":
            if prev in MODAL_TO or prev in SUBJ or prev in {"where", "that", "which"}:
                return "L IH1 V"
            return "L AY1 V"
        if w == "close":
            if nxt in {"to", "by"} or prev in {"so", "very", "too", "pretty", "quite", "is", "are", "was",
                                                 "be", "get", "getting", "how"}:
                return "K L OW1 S"
            return "K L OW1 Z"
        if w == "wind":
            return "W AY1 N D" if prev in MODAL_TO else "W IH1 N D"
        if w == "record":
            return "R EH1 K ER0 D" if prev in DET else "R IH0 K AO1 R D"
        if w == "use":
            return "Y UW1 S" if prev in DET else "Y UW1 Z"
        if w == "lead":
            return "L IY1 D"
        if w == "tear":
            return "T IH1 R" if prev in DET else "T EH1 R"
        return None

    def word_arpa(self, w, prev="", nxt="", initial=False):
        """Returns (phones, source)."""
        h = self.heteronym(w, prev, nxt, initial)
        if h:
            return h.split(), "rule"
        if w in self.lex:
            return list(self.lex[w][0]), "lex"
        if w.endswith("'s") and w[:-2] in self.lex:
            b = list(self.lex[w[:-2]][0])
            return b + possessive_arpa(b), "lex+s"
        if w.endswith("s'") and w[:-1] in self.lex:
            return list(self.lex[w[:-1]][0]), "lex"
        if "-" in w:
            parts = [p for p in w.split("-") if p]
            out = []
            for k, p in enumerate(parts):
                ph, _ = self.word_arpa(p)
                out += ph if k == 0 else [x.replace("1", "2") for x in ph]
            return out, "hyphen"
        cw = w.replace("'", "")
        if len(cw) >= 8:
            best = None
            for i in range(4, len(cw) - 3):
                a, b = cw[:i], cw[i:]
                if a in self.lex and b in self.lex:
                    score = min(len(a), len(b))
                    if best is None or score > best[0]:
                        best = (score, a, b)
            if best:
                _, a, b = best
                return list(self.lex[a][0]) + [x.replace("1", "2") for x in self.lex[b][0]], "compound"
        ph = self.model_arpa(w)
        if ph and any(is_vowel(p) for p in ph):
            return ph, "model"
        out = []
        for c in w:
            if c in LETTERS:
                out += LETTERS[c].split()
        return out, "spell"

    # ---------------- ARPAbet -> IPA ("en" conventions) ----------------
    @staticmethod
    def align(word, phones):
        if not word:
            return {}
        w = word.lower()

        def is_v(i):
            c = w[i]
            if c in "aeiou":
                return True
            if c == "y":
                return i > 0
            if c == "w":
                return i > 0 and w[i - 1] in "aeo" and not (i + 1 < len(w) and w[i + 1] in "aeiouy")
            return False

        groups, i = [], 0
        while i < len(w):
            if w[i].isalpha() and is_v(i):
                j = i
                while j < len(w) and w[j].isalpha() and is_v(j):
                    j += 1
                groups.append((i, w[i:j]))
                i = j
            else:
                i += 1
        vidx = [k for k, p in enumerate(phones) if is_vowel(p)]
        if len(groups) == len(vidx) + 1:
            s, t = groups[-1]
            if t == "e" and (s == len(w) - 1 or w[s:] in ("es", "ed", "e's")):
                groups = groups[:-1]
        if len(groups) != len(vidx):
            return {}
        return {k: groups[j] for j, k in enumerate(vidx)}

    @staticmethod
    def stress_marks(phones, keep_all=False):
        """Per-vowel stress mark. espeak-style: a secondary stress right next to
        the primary syllable is dropped (okay, Tuesday, takeout); word-final
        CMUdict IY2 (company, thirty) is unstressed."""
        vi = [k for k, p in enumerate(phones) if is_vowel(p)]
        st = [phones[k][-1] if phones[k][-1] in "012" else "0" for k in vi]
        if vi and base(phones[vi[-1]]) == "IY" and st[-1] == "2" and vi[-1] >= len(phones) - 2:
            st[-1] = "0"
        if "1" in st:
            p1 = st.index("1")
            st = [("2" if x == "1" else x) if j != p1 else x for j, x in enumerate(st)]
            if not keep_all:
                for j in (p1 - 1, p1 + 1):
                    if 0 <= j < len(st) and st[j] == "2":
                        st[j] = "0"
        marks = {"1": "ˈ", "2": "ˌ", "0": ""}
        return {k: (marks[x], x) for k, x in zip(vi, st)}

    def to_ipa(self, phones, word=None, next_vowel=False, keep_post_secondary=False):
        al = self.align(word, phones) if word else {}
        w = (word or "").lower()
        out = []
        n = len(phones)
        smap = self.stress_marks(phones, keep_post_secondary)
        prim = False
        i = 0
        while i < n:
            p = phones[i]
            b = base(p)
            st = p[len(b):]
            if b not in VOWELS:
                if b == "N" and i + 1 < n and base(phones[i + 1]) in ("K", "G"):
                    out.append("ŋ")  # velar assimilation; espeak never writes "nk"/"ng"
                else:
                    out.append(CONS_IPA.get(b, ""))
                i += 1
                continue
            g = al.get(i)
            grp, gs = (g[1], g[0]) if g else (None, 0)
            nb = base(phones[i + 1]) if i + 1 < n else None
            r = nb == "R"
            if r:
                after = phones[i + 2] if i + 2 < n else None
                r_prev = is_vowel(after) if after else next_vowel
            else:
                r_prev = False
            mark, sd = smap[i]
            stressed = st in ("1", "2") and not (b == "IY" and st == "2" and sd == "0" and i >= n - 2)
            v = self.vowel(b, stressed, grp, gs, w, r, r_prev, phones, i, prim and sd == "0")
            if sd == "1":
                prim = True
            if i > 0 and base(phones[i - 1]) in ("T", "D", "N") and grp and (
                    (b in ("UW", "UH") and grp[0] == "u") or (b == "UW" and grp in ("ue", "ew", "eu"))):
                out.append("j")
            if b == "ER":
                after = phones[i + 1] if i + 1 < n else None
                v = "ɜː" if stressed else ("ɐ" if (i == 0 and grp == "a") else "ə")
                if (after and is_vowel(after)) or (after is None and next_vowel):
                    v += "ɹ"
                out.append(mark + v)
                i += 1
                continue
            out.append(mark + v)
            if r:
                if r_prev:
                    out.append("ɹ")
                i += 2
                continue
            i += 1
        return "".join(out)

    @staticmethod
    def vowel(b, stressed, grp, gs, w, r, r_prev, phones, i, post_tonic):
        if b == "AA":
            o_sp = grp is not None and "o" in grp
            if r and not r_prev:
                return "ɑː"
            if r:
                return "ɒ" if o_sp else "ɑː"
            if grp is None:
                return "ɒ"
            if o_sp or grp.startswith("ua"):
                return "ɒ"
            pre = w[max(0, gs - 2):gs]
            if pre.endswith("w") or pre.endswith("qu"):
                return "ɒ"
            return "ɑː"
        if b == "AO":
            if r and not r_prev:
                return "ɔː"
            if r:
                return "ɒ" if (grp == "o" and w[gs + 1:gs + 3] == "rr") else "ɔː"
            return "ɒ" if grp == "o" else "ɔː"
        if b == "AE":
            if r and not r_prev:
                return "eə"
            return "a"
        if b == "EH":
            if r and not r_prev:
                return "eə"
            if r and grp and "a" in grp:
                return "eə"
            return "ɛ"
        if b == "IH":
            if r and not r_prev:
                return "iə"
            if r and grp and grp not in ("i", "y"):
                return "iə"
            return "ɪ"
        if b == "IY":
            if r:
                return "iə"
            if stressed:
                return "iː"
            nxt = phones[i + 1] if i + 1 < len(phones) else None
            if nxt is None or is_vowel(nxt):
                return "i"
            if i + 2 == len(phones) and base(nxt) in ("Z", "S", "D"):
                return "i"
            return "ɪ"
        if b == "UH":
            return "ʊə" if r else "ʊ"
        if b == "UW":
            return "ʊə" if r else "uː"
        if b == "AH":
            if stressed:
                return "ʌ"
            nxt = base(phones[i + 1]) if i + 1 < len(phones) else None
            if grp == "a" and i == 0:
                return "ɐ"
            if grp == "i":
                return "ɪ"
            if grp == "e" and nxt in IH_CONTEXT:
                return "ɪ"
            if grp == "a" and nxt == "JH":
                return "ɪ"
            return "ə"
        if b == "AW":
            return "aʊə" if r else "aʊ"
        if b == "AY":
            return "aɪə" if r else "aɪ"
        if b == "EY":
            return "eə" if r else "eɪ"
        if b == "OW":
            return "ɔː" if r else "əʊ"
        if b == "OY":
            return "ɔɪ"
        return "ə"

    # ---------------- sentences ----------------
    def phonemize(self, text):
        """Text -> one Piper phoneme string per sentence."""
        return [p for p, _ in self.phonemize_detailed(text)]

    def phonemize_detailed(self, text):
        """Text -> [(sentence phoneme string, word details)]. Each word's
        ``src`` says where its pronunciation came from: say (the owner's
        list), rule, lex, lex+s, fw (weak form), hyphen, compound, model,
        spell."""
        pron = self._pron
        res = []
        for sent in normalize(text):
            # group consecutive spelled letters into one word
            items = []
            for t in sent:
                if t[0] == "w" and t[2] and t[1] not in pron:
                    items.append({"kind": "spell", "letters": list(t[1])})
                elif t[0] == "w":
                    items.append({"kind": "word", "w": t[1]})
                else:
                    items.append({"kind": "punct", "p": t[1]})
            words = [it for it in items if it["kind"] != "punct"]
            for k, it in enumerate(items):
                if it["kind"] == "spell":
                    ph = []
                    L = it["letters"]
                    for j, c in enumerate(L):
                        lp = LETTERS.get(c, "").split()
                        if j < len(L) - 1:
                            lp = [x.replace("1", "2") for x in lp]
                        ph += lp
                    it["ph"], it["src"], it["text"] = ph, "spell", "".join(L).upper()
            for k, it in enumerate(words):
                if it["kind"] != "word":
                    continue
                it["text"] = it["w"]
                p = pron.get(it["w"])
                poss = False
                if p is None and it["w"].endswith("'s"):
                    p = pron.get(it["w"][:-2])
                    poss = p is not None
                if p is not None:
                    it["src"] = "say"
                    if p.ipa is not None:
                        it["ph"] = []
                        it["fixed"] = p.ipa + (possessive_ipa(p.ipa) if poss else "")
                    else:
                        ph = list(p.phones)
                        it["ph"] = ph + (possessive_arpa(ph) if poss else [])
                        it["align"] = p.spelling + ("'s" if poss else "")
                    continue
                prev = words[k - 1].get("w", "") if k > 0 else ""
                nxt = words[k + 1].get("w", "") if k + 1 < len(words) else ""
                it["ph"], it["src"] = self.word_arpa(it["w"], prev, nxt, k == 0)
            # IPA
            out = []
            for k, it in enumerate(items):
                if it["kind"] == "punct":
                    if out and out[-1] == " ":
                        out.pop()
                    out.append(it["p"])
                    out.append(" ")
                    continue
                nxt_it = items[k + 1] if k + 1 < len(items) else None
                clause_final = nxt_it is None or nxt_it["kind"] == "punct"
                nv = (not clause_final) and _starts_with_vowel(nxt_it)
                if it.get("fixed") is not None:
                    ipa = it["fixed"]
                elif it["kind"] == "word" and it["w"] in self.fw and it["src"] in ("lex", "rule"):
                    d, v, f = self.fw[it["w"]]
                    ipa = f if clause_final else (v if nv else d)
                    it["src"] = "fw"
                else:
                    ipa = self.to_ipa(it["ph"], it.get("align", it.get("w")) if it["kind"] == "word" else None,
                                      nv, keep_post_secondary=it["kind"] == "spell")
                    if it["kind"] == "word" and not any(c in ipa for c in "ˈˌ") and it["src"] != "fw":
                        ipa = self._force_stress(ipa)
                it["ipa"] = ipa
                # espeak-style joins: "of the", "in the", "on the", "from the"
                if it["kind"] == "word" and it["w"] == "the" and k > 0 and items[k - 1].get("w") in (
                        "of", "in", "on", "from") and out and out[-1] == " ":
                    out.pop()
                    if items[k - 1]["w"] == "from":
                        out[-1] = "fɹʌm"
                out.append(ipa)
                out.append(" ")
            while out and out[-1] == " ":
                out.pop()
            res.append(("".join(out), items))
        return res

    @staticmethod
    def _force_stress(ipa):
        m = re.search(r"[aeiouæɑɒɔəɛɜɪʊʌɐ]", ipa)
        if not m:
            return ipa
        return ipa[:m.start()] + "ˈ" + ipa[m.start():]


def _starts_with_vowel(it):
    if it.get("fixed") is not None:
        return bool(_IPA_VOWEL_START.match(it["fixed"]))
    return bool(it.get("ph")) and is_vowel(it["ph"][0])
