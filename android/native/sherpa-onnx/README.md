# sherpa-onnx for the app, without text to speech

The on-device assistant listens with [sherpa-onnx](https://github.com/k2-fsa/sherpa-onnx)
(Apache-2.0): Moonshine speech to text and Silero voice detection. Its
release AAR also compiles in text to speech, and with it espeak-ng
(GPL-3.0), so the app does not use that AAR. This directory holds
sherpa-onnx's JNI library built from source with text to speech off, and the
phone speaks with Android's own `TextToSpeech` instead
(`app/.../assistant/AndroidSpeechOut.kt`).

| Path | What |
|---|---|
| `build.sh` | Rebuilds everything here in a pinned Docker container. |
| `build-in-container.sh` | The build itself, with every pin. |
| `jniLibs/<abi>/libsherpa-onnx-jni.so` | arm64-v8a (phones), x86_64 (emulator), armeabi-v7a (32-bit phones), x86 (the 32-bit emulator); stripped; the 64-bit ones with 16 KB pages. |
| `kotlin/` | The Kotlin API those libraries bind to, from the same commit. |
| `LICENSE`, `licenses/` | sherpa-onnx's licence and every compiled-in dependency's. |
| `BUILD-INFO.txt`, `SHA256SUMS` | What built it, and the hash of every file here. |

The library's one native dependency, `libonnxruntime.so`, comes from the
official `com.microsoft.onnxruntime:onnxruntime-android` AAR (MIT) through
Gradle, hash-pinned in `gradle/verification-metadata.xml`; the build links
against that same AAR.

## Pins

- Image `ubuntu:24.04` by digest; build tools from the Ubuntu snapshot
  archive at a fixed date, exact package versions.
- NDK r29 (29.0.14206865), zip checked by SHA-256.
- sherpa-onnx v1.13.8, commit `11afbd009a7f8c08f4bcf2fc1b265d0df4670fbf`;
  the dependencies its CMake fetches are checked by the SHA-256s in its tree.
- ONNX Runtime 1.28.0 AAR from Maven Central, checked by SHA-256.
- Options: `SHERPA_ONNX_ENABLE_TTS=OFF`, speaker diarization, C API,
  binaries, websocket and Python off; Android API 26 (the app's minSdk).

## Rebuilding

```bash
bash android/native/sherpa-onnx/build.sh             # build, compare with SHA256SUMS
bash android/native/sherpa-onnx/build.sh --install   # ...and replace the files here
```

Docker is required; the container is pinned to CPUs 16-31 by default
(`SHERPA_BUILD_CPUS`) and removed afterwards. `SHERPA_BUILD_CACHE=<dir>`
keeps the NDK and ONNX Runtime downloads between runs. Two runs on
2026-10-12 produced byte-identical libraries, and three more the same evening
after armeabi-v7a and x86 were added (every file in `SHA256SUMS` identical;
the 64-bit libraries unchanged by the addition). The build fails if any
espeak-ng, piper-phonemize or TTS string is in the output, and
`NativeSpeechLibraryTest` (app unit tests) checks the committed files against
`SHA256SUMS`, the same strings, each library's ELF class and machine for its
ABI, and 16 KB alignment for the 64-bit ones.

The 32-bit libraries are for the app's 32-bit APKs (old and budget phones),
which have no on-device assistant (LiteRT-LM is 64-bit only) but keep
speech recognition and voice detection for what else needs them.

A version bump changes the pins in `build-in-container.sh` (and the ONNX
Runtime version in `gradle/libs.versions.toml` and its hashes in
`gradle/verification-metadata.xml` together), then `--install`.
