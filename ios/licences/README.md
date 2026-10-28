# Licences: the rule, and every part the iPhone app may use

Same rule as the rest of Domovoi (`docs/CONTRIBUTING.md` and the dependency
licence policy), restated here because Swift packages carry no licence
metadata at all, so nothing in the build can work it out for us.

## The rule

- **Allowed:** MIT, BSD-2-Clause, BSD-3-Clause, ISC, Apache-2.0.
- **MPL-2.0:** only for unmodified third-party code compiled into something
  else — Eigen inside sherpa-onnx is the one case — and then its notice ships
  with the app. Never the licence of a package we add ourselves.
- **Never:** GPL, AGPL or LGPL, in any version.
- **Never, whatever the licence:** anything that does analytics, advertising
  or crash reporting. The privacy card says this app does none of those, and
  that has to stay true.
- **Prefer Apple's own frameworks.** They are part of the OS, like Android's
  own APIs, and are not dependencies we ship.

**Adding a package means three things, in this order:** read its licence at
its own source (not at a package index), add a `LicenceRule` in
`ios/Packages/DomovoiKit/Sources/DomovoiKit/Licences.swift`, and add its row
below. `LicencesTests` fails until all three are done.

## The parts

Each licence below was read from the project's own licence file on 2026-10-21
(plan section 3). **Nothing is linked yet:** this batch has no dependencies at
all. Each row arrives for real in the batch that adds the package.

| Part | Licence | Shipped? | Where it was read |
|---|---|---|---|
| Apple's frameworks (SwiftUI, AVFoundation, MediaPlayer, CryptoKit, Network, Security, PDFKit, PhotosUI, AlarmKit, ActivityKit, WidgetKit, App Intents, CarPlay, MetricKit) | Part of the OS | No | — |
| LiteRT-LM (`litert-lm`), the phone assistant's model runtime | Apache-2.0 | Yes | github.com/google-ai-edge/LiteRT-LM |
| ONNX Runtime (`onnxruntime-swift-package-manager`) | MIT | Yes | github.com/microsoft/onnxruntime-swift-package-manager |
| ZIPFoundation (`zipfoundation`), for .docx and .xlsx | MIT | Yes, if chosen (owner answer Q7) | github.com/weichsel/ZIPFoundation |
| sherpa-onnx (`sherpa-onnx`) | Apache-2.0 | Only if the speech fallback is needed | github.com/k2-fsa/sherpa-onnx |
| XcodeGen | MIT | No — a build tool on the Mac | github.com/yonaskolb/XcodeGen |
| Gemma 4 E2B (`.litertlm`), Silero VAD, Moonshine, Piper voices | Apache-2.0, MIT, MIT, public domain | Downloaded, as on Android | Same pins and hashes as Android |
| openWakeWord models | Served by the household's own server | Not by us | As on Android |
| Fonts | The system font (SF) | No | Nothing bundled, as on Android |
| Icons | SF Symbols | No | Apple's licence covers them in apps on Apple platforms; the cat glyph is ours |

**Not used:** espeak-ng (GPL-3.0), any analytics, advertising or
crash-reporting SDK, CocoaPods (Swift Package Manager is enough), Firebase,
and Tuist (mostly MIT, but its repository holds AGPL parts).

## Two things not to get wrong

### sherpa-onnx's prebuilt iOS xcframeworks are never used

The xcframeworks attached to **sherpa-onnx v1.13.8** merge in **espeak-ng,
which is GPL-3.0**. Using them would put GPL code in an App Store binary.
They are not an option, not for a quick test, not for a spike.

If we need sherpa-onnx, it is **built on the Mac** from a pinned commit
**after PR #4055** (which removed espeak-ng and piper-phonemize from the
project, merged 2026-10-20), or from its own `build-ios-no-tts.sh`, with TTS
off, against the **same** ONNX Runtime the app links — two copies of ONNX
Runtime in one app would clash — and with the same "no espeak strings in the
output" check the Android build has.

Its compiled-in parts need their own notices, as on Android: Eigen (MPL-2.0,
unmodified), nlohmann/json (MIT), kaldi parts and simple-sentencepiece
(Apache-2.0), KISS FFT (BSD-3-Clause).

### We probably do need sherpa-onnx after all

The plan's section 2.2 recommended Apple's on-device speech first, which would
have meant no sherpa-onnx at all. **Owner answer Q12 went the other way:** our
own Moonshine, on ONNX Runtime, pinned, the same model Android uses, for the
same trust reasons as Gemma. Moonshine is how Android runs it through
sherpa-onnx, so the fallback is now the plan — and the paragraph above is not
a footnote, it is the build instruction.

Spike A0 measures Moonshine on Kamron's iPhone 14.
