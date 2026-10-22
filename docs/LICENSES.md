# Licences of the models, voices and data Domovoi downloads

Domovoi's own code is [MIT](../LICENSE). Your server, the Android app and
the wake-word trainer also download models, voices and data that are not
Domovoi's. Each one comes from its publisher, under its publisher's
licence. Your server downloads these; Domovoi doesn't ship them. This page
lists each one with its licence, and says what the licence means. The
libraries and fonts that do come with Domovoi are listed after the models,
under "Not downloaded". For the downloads:

- for a **household** running Domovoi at home;
- for a **redistributor**: anyone who passes these files on (a box or a
  USB stick with Domovoi already set up, a mirror) or uses them in a
  business.

This is a summary, not legal advice. The licence text, linked in each row,
is what counts.

## In short

- **Two defaults are not permissive:**
  - the wake word **`hey_jarvis`**, under **CC BY-NC-SA 4.0** (non-commercial);
  - the question-answering model **`llama3.2:3b`**, under the **Llama 3.2
    Community License**.

  Both are fine to use at home. A redistributor should read
  [what the licences mean](#what-the-licences-mean) first. No default with
  a permissive licence exists for either yet.
- **Everything else a server downloads by default** is under MIT or
  Apache-2.0, or is public domain.
- **AI is optional.** If you leave AI chat and questions out when you set
  up a server, the Llama, Qwen and picture-describing models below are not
  downloaded at all.
- **A wake word you train at home is for your household only**, because
  the training data's licences don't allow sharing it
  ([why](../scripts/wake_word/README.md#5-licence-a-model-trained-here-is-for-this-household)).
- **Some models you can pick yourself are not permissive.** `qwen2.5:3b` is
  for non-commercial research or evaluation only. Llama's vision model has
  an EU restriction. Gemma 2 and 3 have Google's own terms. See
  [the Models page's choices](#models-you-pick-on-the-models-page).

The defaults below are pinned by revision or SHA-256 and checked when they
are downloaded. [SECURITY_PRIVACY.md](SECURITY_PRIVACY.md) lists everything
a server fetches and when.

## The server

| What | What it's for | Publisher, and where it comes from | Licence | Downloaded |
|---|---|---|---|---|
| Whisper `small.en` (faster-whisper / CTranslate2) | Speech to text, the CPU default | OpenAI's Whisper, converted by Systran: [`Systran/faster-whisper-small.en`](https://huggingface.co/Systran/faster-whisper-small.en) on Hugging Face | [MIT](https://huggingface.co/Systran/faster-whisper-small.en) | At install |
| Other Whisper sizes (`large-v3`, `distil-large-v3`, `large-v3-turbo`, …) | Speech to text, when you choose one (a GPU box) | Systran, `distil-whisper` and `mobiuslabsgmbh` on Hugging Face | MIT | When chosen on the Models page |
| `llama3.2:3b-instruct-q4_K_M` | Answers questions (both sizes of server) | Meta, from the [Ollama library](https://ollama.com/library/llama3.2) | [Llama 3.2 Community License](https://github.com/meta-llama/llama-models/blob/main/models/llama3_2/LICENSE) and its [acceptable use policy](https://github.com/meta-llama/llama-models/blob/main/models/llama3_2/USE_POLICY.md) | At install |
| `qwen3:4b-instruct-2507-q4_K_M` (smaller servers) or `qwen3:8b-q4_K_M` (22 GB of memory or more) | Turns a command into the right action | Alibaba's Qwen team, from the [Ollama library](https://ollama.com/library/qwen3) | [Apache-2.0](https://huggingface.co/Qwen/Qwen3-8B) | At install |
| `qwen3-vl:2b-instruct-q4_K_M` | Describes pictures in chat | Qwen, from the [Ollama library](https://ollama.com/library/qwen3-vl) | Apache-2.0 | At install |
| Piper voices Kristin (the default), Norman and Linda | The house voice | Bryce Beattie, from [`rhasspy/piper-voices`](https://huggingface.co/rhasspy/piper-voices) on Hugging Face | [Public domain](https://brycebeattie.com/files/tts/): trained from scratch on LibriVox and LJ Speech recordings, both public domain | At install |
| openWakeWord's pre-trained wake words: `hey_jarvis` (the default), `alexa`, `hey_mycroft`, `hey_rhasspy`, `timer`, `weather` | The wake word on satellites, spare computers and phones in room mode | David Scripka, from [openWakeWord's v0.5.1 release](https://github.com/dscripka/openWakeWord/releases/tag/v0.5.1) on GitHub | [CC BY-NC-SA 4.0](https://github.com/dscripka/openWakeWord#license) | When the satellite media cache is refreshed; a spare computer's installer fetches the ones it needs itself |
| openWakeWord's feature models: `embedding_model`, `melspectrogram` | The front end of every wake word, trained ones included | The same release. The embedding model is Google's speech embedding model | The embedding model: Apache-2.0 (Google's, says openWakeWord). The melspectrogram: no licence of its own; it ships in a release whose pre-trained models are CC BY-NC-SA 4.0 | The same |
| Silero VAD v4 (`silero_vad.onnx`) | Hears where speech starts and stops, on satellites | Silero, from the same openWakeWord release | [MIT](https://github.com/snakers4/silero-vad) | The same |
| NVIDIA NeMo streaming FastConformer (80 ms, int8) | The fast lane, if you turn it on (off by default) | NVIDIA, converted by sherpa-onnx, from [its `asr-models` release](https://github.com/k2-fsa/sherpa-onnx/releases/tag/asr-models) | [CC BY 4.0](https://creativecommons.org/licenses/by/4.0/) | Only when the fast lane is turned on |

A Piper voice you set by name, other than these three, comes with its own
licence (the `MODEL_CARD` beside it in `rhasspy/piper-voices`). Several
are research-only or trained on non-commercial data. That's why Domovoi
retired lessac, amy, ryan and the voices fine-tuned from them
(`domovoi/clients/tts.py`).

### Models you pick on the Models page

None of these is downloaded unless you choose it (Settings, Models), or
unless it's the setting's fallback. `qwen2.5:14b` is the tool model a server
uses when nothing else was set.

| Model | Licence |
|---|---|
| `llama3.2:1b` | Llama 3.2 Community License |
| `llama3.2-vision:11b` | Llama 3.2 Community License. Its acceptable use policy doesn't grant the multimodal models to people who live in the European Union, or to companies based there; people who only use a product built on one are exempt |
| `llama3.1:8b` | [Llama 3.1 Community License](https://github.com/meta-llama/llama-models/blob/main/models/llama3_1/LICENSE) |
| `qwen2.5:3b` | [Qwen Research License](https://huggingface.co/Qwen/Qwen2.5-3B-Instruct/blob/main/LICENSE): non-commercial research or evaluation only |
| `qwen2.5:7b`, `qwen2.5:14b`, `qwen2.5:32b`, `qwen2.5-coder:7b`, `qwen2.5vl:7b`, `qwen3-vl:4b-instruct` | Apache-2.0 |
| `phi3:3.8b` | MIT |
| `mistral:7b` | Apache-2.0 |
| `nomic-embed-text` | Apache-2.0 |
| `gemma4:e2b-it` | [Apache-2.0](https://huggingface.co/google/gemma-4-E2B-it) |
| `gemma2:9b`, `gemma3:12b` | [Gemma Terms of Use](https://ai.google.dev/gemma/terms) and Google's prohibited use policy |

Any other model you pull by name has its own licence: its page in the
[Ollama library](https://ollama.com/library) or on Hugging Face says which.

## The Android app

| What | What it's for | Publisher, and where it comes from | Licence | Downloaded |
|---|---|---|---|---|
| Gemma 4 E2B-it (LiteRT-LM) | The on-device assistant (preview), 64-bit phones only | Google, from [`litert-community/gemma-4-E2B-it-litert-lm`](https://huggingface.co/litert-community/gemma-4-E2B-it-litert-lm) on Hugging Face | [Apache-2.0](https://huggingface.co/google/gemma-4-E2B-it) | When you set the assistant up (about 2.6 GB) |
| Moonshine `tiny-en` | Speech to text for the assistant | Moonshine AI, converted by sherpa-onnx, on Hugging Face | [MIT](https://github.com/moonshine-ai/moonshine/blob/main/LICENSE) | With the assistant |
| Moonshine `base-en` | The benchmark only (developer options) | The same | MIT | Only when the benchmark runs it |
| Silero VAD | Hears where speech starts and stops | Silero, from `csukuangfj/vad` on Hugging Face | [MIT](https://github.com/snakers4/silero-vad) | With the assistant |
| Piper voices Kristin, Norman and Linda | The assistant's voice | As on the server | Public domain | One at a time, from Settings, Voice |
| The wake-word models (room mode, preview) | A phone as a room | **Your own server**, not the internet: the openWakeWord files above | As above (`hey_jarvis`: CC BY-NC-SA 4.0). That's why the app doesn't bundle them | When room mode starts |

The app's own parts and libraries, with their licence texts, are in the
app under Settings → About & updates → Open-source licences.

## The wake-word trainer

Wake-word training is off until you turn it on in Settings. The trainer is a
container your server builds and runs. What it downloads trains **your
household's** model, and the training data is why that model stays in the
household.

| What | Licence |
|---|---|
| openWakeWord's training code | Apache-2.0 |
| piper-sample-generator's code | MIT |
| Its LibriTTS-R voice generator (`en-us-libritts-high.pt`), which makes the synthetic clips | No licence of its own stated; trained on LibriTTS-R ([CC BY 4.0](https://www.openslr.org/141/)) |
| The negative features, ACAV100M (`davidscripka/openwakeword_features`) | CC BY-NC-SA 4.0 (non-commercial, share-alike) |
| The validation features | Include MUSDB18, which is for educational use only |
| MIT's room impulse responses (`davidscripka/MIT_environmental_impulse_responses`) | No licence stated at all |
| The background clips (FMA and FSD50K samples) | Include clips under non-commercial licences |
| espeak-ng | GPL-3.0. It runs only inside the trainer's image, as a separate program; Domovoi doesn't include or ship it |
| ffmpeg (the Linux distribution's own package), the mutagen library, libsndfile | ffmpeg as the distribution builds it, with its GPL parts turned on; mutagen GPL-2.0-or-later; libsndfile LGPL-2.1-or-later. Like espeak-ng, only inside the trainer's image |
| The image's base, `pytorch/pytorch` with CUDA | PyTorch is BSD-3-Clause; NVIDIA's CUDA and cuDNN libraries are under NVIDIA's own licence |

## Not downloaded: these come with Domovoi

- The text-to-phoneme converter's model is Domovoi's own (MIT). Its
  dictionary is the Carnegie Mellon Pronouncing Dictionary (BSD-2-Clause
  style), and its licence is in `domovoi/phonemizer/data/cmudict/LICENSE`.
- Resemblyzer's speaker-encoder weights, for voice profiles, come inside
  its Python package (Apache-2.0), which is installed with the server.
- The Windows tray icon's QR code ("Show a code for your phone",
  `windows/tray/Qr.cs`) is adapted from Project Nayuki's
  [QR Code generator library](https://www.nayuki.io/page/qr-code-generator-library)
  (MIT). Its copyright notice and licence text are in that file and inside
  `DomovoiTray.exe`. Nothing else is bundled into the Windows programs:
  they use only .NET Framework 4.8.1, which is part of Windows.
- **pypdfium2** (reads PDFs in Chat, draws pages for printing): Apache-2.0
  or BSD-3-Clause. Its wheel carries PDFium (BSD-3-Clause and Apache-2.0)
  and the libraries PDFium is built with: FreeType (under the FreeType
  licence), libjpeg-turbo (IJG and BSD), libpng, zlib, Little CMS (MIT),
  OpenJPEG (BSD), ICU (the Unicode licence), Abseil (Apache-2.0) and a few
  smaller permissive ones. Their licence texts come inside the wheel
  (`pypdfium2-*.dist-info/licenses/`). The FreeType licence asks for this
  credit: *Portions of this software are copyright © The FreeType Project
  (www.freetype.org). All rights reserved.*
- **ReportLab** (test pages, text and table printouts, scan PDFs): BSD.
- **The dashboard's libraries**, served by the server itself so the
  dashboard makes no outside requests (`web/static/vendor/`): React and
  ReactDOM 18.3.1 (MIT), Babel standalone 7.29.0 (MIT), Lucide 1.22.0
  (ISC), marked 12.0.2 (MIT), x-spreadsheet (MIT), qrcode-generator 1.4.4
  by Kazuhiko Arase (MIT) and Excalidraw 0.17.6 (MIT; it carries DOMPurify
  3.0.3 under Apache-2.0 or MPL-2.0, used here under Apache-2.0).
- **The dashboard's fonts**, all under the SIL Open Font License 1.1, each
  with its licence text beside it: Inter and JetBrains Mono (through
  fontsource 5.2.8, in `web/static/vendor/fonts/`), the three fonts
  Excalidraw carries (Virgil, Cascadia Code and Assistant, in
  `web/static/vendor/excalidraw/`), and Vast Shadow (copyright 2011 Sorkin Type Co, SIL Open Font License 1.1), the display
  face (`web/static/fonts/`, also in `docs/design/fonts/`).
- **Liberation Sans and Liberation Serif** 2.1.5 (SIL Open Font License
  1.1), embedded in the PDFs Chat writes. The same files are in the server
  (`domovoi/drafts/fonts/`, with `LICENSE-OFL.txt`) and in the app.

## What the licences mean

**MIT, ISC, Apache-2.0, BSD-3-Clause, public domain.** Use them at home or in a
business, change them, and pass them on.
- *Household:* nothing to do.
- *Redistributor:* keep the copyright notice and the licence text with the
  files. Apache-2.0 also wants its `NOTICE` file, if there is one, and a
  note on files you changed.

**CC BY-NC-SA 4.0:** openWakeWord's pre-trained wake words, `hey_jarvis`
among them, and the trainer's ACAV100M features.
- *Household:* personal, non-commercial use is what the licence allows,
  and it's what a home assistant does.
- *Redistributor:* no commercial use, so a box, stick or service that
  you sell can't carry these files. Non-commercial sharing must credit the
  author and use the same licence.
- A model trained from this data inherits the problem, so a wake word
  trained at home stays at home. A permissive default for Domovoi needs
  training data that is permissive throughout. It's planned, not built.

**Llama 3.2 (and 3.1) Community License:** `llama3.2:3b` and the other
Llama models.
- *Household:* you may use it at home without paying, within Meta's
  acceptable use policy.
- *Redistributor:*
  - include a copy of the licence;
  - show "Built with Llama" where the product is described;
  - pass on the acceptable use policy;
  - start the name of any model you build from it with "Llama".
- A company whose products had more than 700 million monthly active users
  on the Llama 3.2 release date needs Meta's own licence.
- The vision models carry the EU restriction above.

**Qwen Research License:** `qwen2.5:3b` only. It allows non-commercial
research or evaluation, and nothing else without Alibaba Cloud's own
licence. For ordinary use, pick another model.

**Gemma Terms of Use:** `gemma2:9b`, `gemma3:12b`.
- *Household:* you may use them and change them, within Google's
  prohibited use policy.
- *Redistributor:* pass the terms and that policy on to whoever gets the
  files.
- Gemma 4 (`gemma4:e2b-it`, and the phone's assistant) is Apache-2.0
  instead.

**CC BY 4.0:** the fast lane's NVIDIA model, and LibriTTS-R. Use them for
anything, but credit the author (NVIDIA; LibriTTS-R's authors) when you
pass them on.

**SIL Open Font License 1.1:** the dashboard's fonts and the Liberation
fonts.
- *Household:* nothing to do.
- *Redistributor:* use, copy and pass the fonts on, in a product you sell
  too, but not the font files on their own for money; keep the copyright
  notice and the licence text with them; a changed font can't keep the
  original name.

**"No licence stated" and "educational use only":** MIT's impulse
responses, and MUSDB18 inside the validation features. Without a licence,
nothing grants you the right to pass them on, and MUSDB18 is for education
only. Both are used only to train a household's own wake word.
