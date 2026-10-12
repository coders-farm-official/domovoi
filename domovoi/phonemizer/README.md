# domovoi.phonemizer: text to phonemes, without espeak-ng

Domovoi's voices (the public-domain Piper voices `en_US-norman-medium`,
`en_US-kristin-medium` and `en_US-ljspeech-medium`) were trained on the
phoneme strings espeak-ng writes for British English (`"en"`). espeak-ng is
GPL, so Domovoi makes those strings itself: this package turns English text
into the same symbol set from permissively licensed parts only. The server
uses it in Python; the Android app uses a Kotlin port of it, and both check
themselves against one fixture, `golden.json`.

```python
from domovoi.phonemizer import get_phonemizer, phoneme_ids

ph = get_phonemizer()                      # loads once, ~0.6 s; thread-safe after that
ph.phonemize("Hey Domovoi! It's 72°F.")    # ['hˈeɪ dˈəʊməʊvˌɔɪ!', 'ɪts sˈɛvənti tˈuː dɪɡɹˈiːz fˈeəɹənhˌaɪt.']
phoneme_ids("hˈeɪ dˈəʊməʊvˌɔɪ!")          # [1, 0, 20, 0, 120, 0, ...] for the voice's ONNX model
```

About 0.1 ms a sentence once loaded; a word that is in no dictionary costs
about 1 ms the first time (the model), then it is cached.

## Licences

| Part | Licence |
|---|---|
| The code here, `lexicon_corrections.txt`, `function_words_en.tsv`, `pronunciations_default.txt`, the model and its training script | MIT (Domovoi's own) |
| `data/cmudict/` (the Carnegie Mellon Pronouncing Dictionary) | BSD-2-Clause style, `data/cmudict/LICENSE` (verified at the source, github.com/cmusphinx/cmudict) |
| `data/phoneme_ids_en.json` | the symbol table of the three voices' `.onnx.json` (public-domain voices) |

No espeak-ng code, data or output is used anywhere, including in training.

## How it works

1. **Normalize** (`normalize.py`): numbers, money, times, dates, percent,
   degrees, units, ordinals, phone numbers, abbreviations, acronyms (short
   all-capital words are spelled), stylized names (`$` glued to letters is an
   S), then split into sentences and words. Tables and regular expressions
   only.
2. **Each word to ARPAbet** (`convert.py`), first match wins: the owner's
   "say it like" list; context rules for heteronyms (read, live, close,
   wind, record, use, lead, tear); CMUdict plus `lexicon_corrections.txt`;
   possessive `'s`; hyphenated parts; a compound split into two dictionary
   words; the out-of-dictionary model; spelling the letters.
3. **ARPAbet to the voices' IPA** with stress, in espeak's `"en"`
   conventions (vowel qualities chosen partly from the spelling, linking r,
   `ŋ` before k/g, secondary stress next to the primary dropped), weak forms
   of function words (`function_words_en.tsv`) by position, and espeak's
   joins (`ɪnðə`, `ɒvðə`).
4. **Ids** (`phoneme_ids.py`), Piper's rule: NFD-normalize, then `^ _`,
   each known code point's ids followed by `_`, then `$`. One map serves
   all three voices.

## Files

```
domovoi/phonemizer/
  __init__.py            get_phonemizer(), the public names
  convert.py             Phonemizer: words -> ARPAbet -> IPA, sentences
  normalize.py           text normalization and tokens
  number_words.py        numbers -> words
  pronunciations.py      the "say it like" format: parsing and checks
  phoneme_ids.py         phoneme string -> voice ids
  golden.json            the fixture (below)
  data/
    SHA256SUMS           every data file's SHA-256 (a test checks them)
    cmudict/             cmudict.dict, LICENSE, README (pinned, see below)
    lexicon_corrections.txt   additions/fixes to CMUdict (ARPAbet)
    function_words_en.tsv     weak forms: word, default, before a vowel, clause-final
    pronunciations_default.txt the shipped "say it like" list
    phoneme_ids_en.json  the voices' phoneme id map
    g2p/g2p.int8.onnx    the out-of-dictionary model (3.3 MB)
    g2p/g2p_vocab.json   its letters and output phones
scripts/g2p/
  fetch_cmudict.py       download CMUdict at the pinned commit, check, filter
  train_g2p.py           train the model (seed 0)
  train-in-container.sh  run the training in a pinned container
  requirements-train.txt the training wheels, SHA-256 pinned
  make_golden.py         write or --check golden.json
  golden_inputs/*.txt    the fixture's texts, one per line
```

**CMUdict** comes from github.com/cmusphinx/cmudict at commit
`74790861f652b15e4ac49015a90074ad62a27690` (upstream `cmudict.dict`
SHA-256 `81917843c7f44ce2b094ac63873c2c7a4cf802040792c455ba3ca406891c3d22`).
Domovoi's copy drops the 17 entries whose word contains one of the repo's
reserved names (`AGENTS.md`); `scripts/g2p/fetch_cmudict.py` rebuilds it
byte for byte (SHA-256
`bd7b76b677381e8ea6ffced3dd18b310d903a6a3cd36aa718f8d79be1ffdcf5a`).

## "Say it like": the owner's pronunciation list

Settings keeps a list of words the voice should say differently. Each entry
is a word and a respelling in plain English spelling; the respelling is read
back through this same converter. Text form, one entry per line, `#` for
comments (`pronunciations_default.txt` is the shipped list):

```
Domovoi = DOH-moe-voy
Domo = DOH-moe
Nguyen = win
Siobhan = shiv-AWN
```

- **The word** (left): one word as it appears in text: letters, with `'`
  or `-` inside, at most 48 characters. Matching ignores case and accents
  (`Beyoncé` also matches `BEYONCE`), and `Word's` follows `Word`. An
  all-capital acronym (`SZA`) can have an entry too; it is then said as
  respelled instead of spelled.
- **Say it like** (right): syllables separated by hyphens or spaces, each
  written in plain letters. Each syllable is pronounced the way the
  converter says that spelling as a word on its own (dictionary, then
  model), never through the list again.
- **Stress**: the syllable in CAPITALS takes the main stress. With no
  capitals (or all capitals), the first syllable does. Other syllables are
  said lighter: their stress drops to secondary, and an unstressed "uh" or
  "er" sound becomes a schwa (`DOM-uh-voy` is D AA1 M AH0 V OY2).
- **Advanced**: between slashes, the text is the voice's own phoneme string,
  used unchanged: `Domovoi = /dˈəʊməʊvɔɪ/`. Every symbol must be one in the
  voices' map.
- **Refused**: an empty side, more than one word, characters other than
  letters in a respelling, more than 12 syllables or 100 characters, a
  respelling with no vowel sound, unknown phoneme symbols. The error names
  the entry and the reason; a list with a bad entry is not taken at all.
- An entry wins over everything else (context rules, dictionary, weak
  forms).

The shipped list holds the product's name as the owner says it
(2026-10-12): Domovoi is DOH-moe-voy, `dˈəʊməʊvˌɔɪ` (a full "oh" in the
middle, not DOM-uh-voy), Domo is `dˈəʊməʊ`, and Nguyen is "win". These
are only defaults: an owner can change or remove them.

## The golden fixture (`golden.json`)

What the converter must produce for 696 texts: the 13 comparison
sentences, 39 Domovoi phrases, function-word sentences, heteronyms, 32
number forms plus 93 more normalization cases (money, times, dates, units,
ordinals, phone numbers, abbreviations, odd punctuation), 74 names, 62
artists, the 337 common words, and 13 cases with their own "say it like"
lists. UTF-8 JSON, LF line endings, one case per line.

Top level:

| Key | Meaning |
|---|---|
| `format` | `domovoi-phonemizer-golden/1` |
| `data_sha256` | SHA-256 of each data file the fixture was made from (paths under `data/`) |
| `made_with` | the ONNX Runtime version and machine that wrote it (1.28.0, Linux x86_64: the phone's runtime; a server's lock runs 1.30.0, which reads every model word here the same) |
| `default_pronunciations` | the list in force for every case without its own: `[[word, say_it_like], ...]` |
| `ids` | which voice the ids are for (`en_US-norman-medium`; the same for kristin and ljspeech), the map file, and the id rule |
| `near_tie_margin`, `near_tie_words` | see below |
| `counts` | cases per group |
| `invalid_say_it_like` | `[word, say_it_like]` pairs a port must refuse |
| `cases` | the cases |

A case:

```json
{"id": "sentences-005", "group": "sentences", "text": "Hey Domovoi! Sure, I'll turn that down.",
 "phonemes": ["hˈeɪ dˈəʊməʊvˌɔɪ!", "ʃˈʊə, aɪl tˈɜːn ðat dˈaʊn."],
 "ids": [[1, 0, 20, 0, ...], [1, 0, ...]]}
```

- `phonemes`: one string per sentence, in order. Assert equality of the
  whole list.
- `ids`: one list per sentence, from `phonemes` by the id rule.
- `pronunciations` (only in `say_it_like` cases): the list in force for
  this case, replacing the default list completely (`[]` means none; a
  later entry for the same word wins). `resolved` gives each entry's
  ARPAbet (or `/phonemes/`), keyed by the normalized word.
- `model` (informational): the words of the text the out-of-dictionary
  model pronounced, with its ARPAbet, to tell a model difference from a
  rules difference. `respelling_model`: the same for the syllables of a
  "say it like" entry the case uses (`voy` in `DOH-moe-voy`).
- `near_tie`: model words (of the text or of a respelling) whose output
  is within `near_tie_margin` (0.1) logits of a different phoneme. The
  case may then differ by **one phoneme per near-tie word** (at most two
  code points each in the joined sentences, `iː` against `ɪ`), and nothing
  else: a runtime other than `made_with` may pick the other phoneme
  (ONNX Runtime 1.24 reads `shiv` as SH IY1 V, 1.28 and 1.30 as SH IH1 V;
  `oluwaseun` flips between the int8 and float models). The ids then
  follow the phonemes actually produced. The Python test, `make_golden.py
  --check` and the Kotlin port apply this same rule. The shipped "say it
  like" entries have no near tie, so Domovoi's own name sounds the same
  on every runtime.

Regenerate only on purpose, with ONNX Runtime 1.28 or newer (pyproject's
real-clients floor; in a throwaway `python:3.14-slim` container with
`pip install onnxruntime==1.28.0 numpy`, then `python -I
scripts/g2p/make_golden.py --out <file>`), and read the diff: each changed
line is something the voices will say differently. `--check` exits 1 when
the fixture is out of date. The test
`domovoi/tests/test_phonemizer_golden.py` also fails when any data file's
hash differs from `data_sha256`.

**Porting notes.** The regular expressions are Python `re` with Unicode
semantics (`\w`, `\b`, `\d`, `str.isupper()`, `str.isalpha()` see
non-ASCII letters and digits); in Kotlin use `UNICODE_CHARACTER_CLASS` or
equivalent. Accents are stripped with NFKD and dropping combining marks.
Dictionary lookups use the first CMUdict variant of a word; the corrections
file replaces all variants of a word it lists.

## The out-of-dictionary model

A letters-to-ARPAbet model for words in no dictionary (names, new words),
trained on CMUdict alone.

- **Input** `chars`: int64 `[1, T]`, one id per letter of the lower-cased
  word, `a`..`z` = 1..26 and `'` = 27 (`g2p_vocab.json` `letters`, id =
  index + 1), other characters dropped, at most 40.
- **Output** `logits`: float `[1, 3T, 70]`. Decode: argmax per frame, drop
  repeats of the previous frame's id, drop 0 (CTC blank); the rest index
  `phones` (ARPAbet with stress digits). A result with no vowel is
  discarded (the word is spelled instead).
- **Architecture**: each letter repeated 3 times, a 4-layer Transformer
  encoder (d 256, 4 heads, feed-forward 1024), a CTC head; 3,217,478
  parameters. Exported to ONNX (opset 17), then dynamically quantized to
  int8 (`g2p.int8.onnx`, 3,327,744 bytes).
- **Training**: `scripts/g2p/train-in-container.sh OUTDIR` (pinned
  `python:3.11-slim-bookworm` image by digest, pinned wheels by SHA-256,
  torch 2.5.1+cpu, onnxruntime 1.20.1, CPUs 16-31, network off when given
  `--wheels`). Seed 0, 40 epochs, 14 threads, AdamW 1e-3, cosine schedule,
  97/1/2 % train/dev/test split of the 124,911 usable words of the
  unchanged upstream file (first variant, letters and `'` only, at most 40
  letters: `load()` in the script). Held-out test (2,498 words): 32.4 % of words
  with any ARPAbet difference, 26.8 % ignoring stress, 7.8 % phone error
  rate; the int8 file scores the same to the third decimal.
- **Hashes** (the recorded run, 2026-10-12): `g2p.pt`
  `78f1502801011480ce3311b807c45914397266c520776b604724e5af72c62e15`,
  `g2p.onnx`
  `ce347c37ad5e9c986a3b8d18e77e5770e48f4042a9977d7082da32eacb869aba`,
  `g2p.int8.onnx`
  `b13e610b90dda492fa85581d9d2ceaf3be33f0ce43d2519cdd7ae0e34a74e543`,
  `g2p_vocab.json`
  `f6446b2332927190d98df95dc37ac081e27eb58c9484906e49f86b3f382bf65a`.
  Float results are bit-for-bit only on the same torch build, thread count
  and CPU family; elsewhere expect the same metrics, not the same bytes.
  Checked 2026-10-12: a full rerun of the script in the pinned container
  (14 threads on an i9-14900KF's E-cores, about 95 minutes) reproduced all
  four files above byte for byte, every epoch's loss and dev scores equal
  to the recorded run's.
