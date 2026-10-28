# DomovoiPhonemizer

Empty on purpose. This is the package the Swift phonemizer will live in; the
port has not started.

## What it will be

The Domovoi voice (Piper-style voices on ONNX Runtime) needs text turned into
phonemes before the voice model sees it. The server does this in Python
(`domovoi/phonemizer/`), the Android app does it in Kotlin
(`android/app/src/main/java/com/domovoi/app/phonemizer/`, about 1,730 lines:
`Normalize`, `NumberWords`, `Phonemizer`, `PhonemizerData`, `Pronunciations`,
`Py`), and the iPhone app will do it here. Its golden test is
`android/app/src/test/java/com/domovoi/app/phonemizer/PhonemizerGoldenTest.kt`,
with `RepoPhonemizer.kt` beside it for reading the fixture out of the
repository — read both before starting.

- **Port from the Kotlin**, not from the Python. The Kotlin has already solved
  the "Python's strings and regexes are not this language's" problem once, and
  `Py.kt` is the file that holds those compromises.
- **Data** comes from the repository, not from this package: CMUdict (3.6 MB,
  BSD-2-Clause style), the word lists, and the 3.3 MB `g2p.int8.onnx` model.
- **The check** is `domovoi/phonemizer/golden.json`, read from the repository:
  every case exact, and the fixture's `near_tie` cases allowed one phoneme's
  difference. Exactly the Android test's rule: **fix the port, never the
  fixture.**
- **No UI**, so the golden test runs on the Mac itself with `swift test`.

## Why it is not started

Plan 2.5. Its whole difficulty is behaviour that differs between languages —
Swift `String` counts grapheme clusters, `NSRegularExpression` is ICU, and
Unicode NFD is `decomposedStringWithCanonicalMapping` — so every line of it
needs the golden file to be run against. Writing 1,730 lines of it before a
Mac exists would be writing 1,730 lines nobody has compiled.

It also needs ONNX Runtime, which is the first external dependency the iPhone
app takes; its licence rule is already in
`ios/Packages/DomovoiKit/Sources/DomovoiKit/Licences.swift`. Plan 2.5 notes
one thing to check at the pin: whether the official ONNX Runtime Swift package
has a macOS slice, because without one the golden test runs in the Simulator
instead of on the Mac.
