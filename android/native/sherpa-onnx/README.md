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
| `build-in-container.sh` | Inside that container: installs the pinned tools, NDK and ONNX Runtime, then runs `build-native.sh`. |
| `build-native.sh` | The build itself, with the source and download pins; runs on any Linux host without Docker (F-Droid runs it). |
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
- sherpa-onnx v1.13.8, commit `11afbd009a7f8c08f4bcf2fc1b265d0df4670fbf`,
  and each library its CMake would download, by git commit (the list is
  under "F-Droid" below). The build hands CMake those checkouts and lets it
  download nothing.
- ONNX Runtime 1.28.0 AAR from Maven Central, checked by SHA-256.
- Options: `SHERPA_ONNX_ENABLE_TTS=OFF`, speaker diarization, C API,
  binaries, websocket and Python off; Android API 26 (the app's minSdk).

## Rebuilding

```bash
bash android/native/sherpa-onnx/build.sh             # build, compare with SHA256SUMS
bash android/native/sherpa-onnx/build.sh --install   # ...and replace the files here
```

`build.sh` needs Docker (without it, `build-native.sh` builds on a Linux
host as F-Droid does; see below). The container is pinned to CPUs 16-31 by default
(`SHERPA_BUILD_CPUS`) and removed afterwards. `SHERPA_BUILD_CACHE=<dir>`
keeps the NDK and ONNX Runtime downloads between runs. Two runs on
2026-10-12 produced byte-identical libraries, and three more the same evening
after armeabi-v7a and x86 were added (every file in `SHA256SUMS` identical;
the 64-bit libraries unchanged by the addition). The build fails if any
espeak-ng, piper-phonemize or TTS string is in the output, and
`NativeSpeechLibraryTest` (app unit tests) checks the committed files against
`SHA256SUMS`, the same strings, each library's ELF class and machine for its
ABI, and 16 KB alignment for the 64-bit ones.

CI checks this every week, too: `.github/workflows/sherpa-onnx-rebuild.yml`
rebuilds each ABI's library with `build-native.sh` (Tuesdays, on any change
to this folder, or by hand) and fails if a file differs from `SHA256SUMS`.

The 32-bit libraries are for the app's 32-bit APKs (old and budget phones),
which have no on-device assistant (LiteRT-LM is 64-bit only) but keep
speech recognition and voice detection for what else needs them.

A version bump changes the pins in `build-native.sh` (and the ONNX
Runtime version in `gradle/libs.versions.toml` and its hashes in
`gradle/verification-metadata.xml` together), then `--install`. A new
sherpa-onnx commit can move the libraries its CMake fetches: read the
version each `cmake/*.cmake` names at the new commit (kaldifst's is in
kaldi-decoder's `cmake/kaldifst.cmake`, kissfft's in kaldi-native-fbank's
`cmake/kissfft.cmake`), update `DEPS`, and give F-Droid the new srclib
commits below.

## F-Droid

F-Droid builds the app from source on its own Debian build server, without
Docker, so it builds these libraries itself with `build-native.sh`: the same
script `build-in-container.sh` runs to make the committed ones. Every source
comes in as a srclib (F-Droid's own pinned git checkout), the NDK from
`ndk: r29`, and ONNX Runtime from the pinned Maven Central AAR the app
already uses. sherpa-onnx's CMake normally downloads eight libraries; the
script copies the given checkouts to where CMake would have unpacked them,
points CMake at them (`FETCHCONTENT_SOURCE_DIR_<NAME>`) and turns its
downloads off (`FETCHCONTENT_FULLY_DISCONNECTED`). (sherpa-onnx's own
offline route, a release tarball under `~/Downloads` or `/tmp`, needs the
exact upstream tarball, so it does not fit git srclibs.) The libraries hold
only paths relative to the scratch directory, so the srclib and scratch
paths do not change a byte.

Srclibs (`sherpa_onnx` already exists in fdroiddata; the others are new),
each at a commit, read from sherpa-onnx's `cmake/*.cmake` at
`11afbd009a7f8c08f4bcf2fc1b265d0df4670fbf` (kaldifst's from kaldi-decoder's
`cmake/kaldifst.cmake`, kissfft's from kaldi-native-fbank's
`cmake/kissfft.cmake`). Each upstream release tarball that CMake pins by
SHA-256 holds exactly the files of the commit given here.

| srclib | Repository | Tag | Commit | Variable |
|---|---|---|---|---|
| `sherpa_onnx` | https://github.com/k2-fsa/sherpa-onnx | v1.13.8 | `11afbd009a7f8c08f4bcf2fc1b265d0df4670fbf` | `SHERPA_SRC` |
| `eigen` | https://gitlab.com/libeigen/eigen.git | 5.0.1 | `bc3b39870ecb690a623a3f49149a358b95c5781d` | `SHERPA_DEP_EIGEN` |
| `nlohmann_json` | https://github.com/nlohmann/json | v3.12.0 | `55f93686c01528224f448c19128836e7df245f72` | `SHERPA_DEP_JSON` |
| `kaldi_decoder` | https://github.com/k2-fsa/kaldi-decoder | v0.3.0 | `62fda2d74246a90db9570e46fb16b012f813fae7` | `SHERPA_DEP_KALDI_DECODER` |
| `kaldi_native_fbank` | https://github.com/csukuangfj/kaldi-native-fbank | v1.22.3 | `b09e686fe2084732ddd30d1ef80acfc0f13eaf01` | `SHERPA_DEP_KALDI_NATIVE_FBANK` |
| `kaldifst` | https://github.com/k2-fsa/kaldifst | v1.8.0 | `ab5bdd013bdf13921e6aeee77db5722ebf9955fb` | `SHERPA_DEP_KALDIFST` |
| `kissfft` | https://github.com/mborgerding/kissfft | (none) | `febd4caeed32e33ad8b2e0bb5ea77542c40f18ec` | `SHERPA_DEP_KISSFFT` |
| `openfst` | https://github.com/csukuangfj/openfst (sherpa-onnx's fork) | v1.8.5-2026-07-09 | `f702a6317d600ac953fdbb4978674c3a28b6115c` | `SHERPA_DEP_OPENFST` |
| `simple_sentencepiece` | https://github.com/pkufool/simple-sentencepiece | v0.7 | `62dd423df1d51da5ea06f1c3a046fc04f01b4f39` | `SHERPA_DEP_SIMPLE_SENTENCEPIECE` |

The `DEPS` list in `build-native.sh` holds the same pins; a srclib that is a
git checkout at any other commit stops the build. The `json` tag is
annotated: the commit above is the one it points at.

The build steps for one ABI (F-Droid builds arm64-v8a and armeabi-v7a
separately), run from the build's `subdir: android/app`:

```yaml
    srclibs:
      - sherpa_onnx@11afbd009a7f8c08f4bcf2fc1b265d0df4670fbf
      - eigen@bc3b39870ecb690a623a3f49149a358b95c5781d
      - nlohmann_json@55f93686c01528224f448c19128836e7df245f72
      - kaldi_decoder@62fda2d74246a90db9570e46fb16b012f813fae7
      - kaldi_native_fbank@b09e686fe2084732ddd30d1ef80acfc0f13eaf01
      - kaldifst@ab5bdd013bdf13921e6aeee77db5722ebf9955fb
      - kissfft@febd4caeed32e33ad8b2e0bb5ea77542c40f18ec
      - openfst@f702a6317d600ac953fdbb4978674c3a28b6115c
      - simple_sentencepiece@62dd423df1d51da5ea06f1c3a046fc04f01b4f39
    sudo:
      - apt-get update
      - apt-get install -y cmake ninja-build
      - install -d -m 1777 /work
    rm:
      - android/native/sherpa-onnx/jniLibs
    build: ABIS=arm64-v8a ANDROID_NDK=$$NDK$$ SHERPA_SRC=$$sherpa_onnx$$
      SHERPA_DEP_EIGEN=$$eigen$$ SHERPA_DEP_JSON=$$nlohmann_json$$
      SHERPA_DEP_KALDI_DECODER=$$kaldi_decoder$$
      SHERPA_DEP_KALDI_NATIVE_FBANK=$$kaldi_native_fbank$$
      SHERPA_DEP_KALDIFST=$$kaldifst$$ SHERPA_DEP_KISSFFT=$$kissfft$$
      SHERPA_DEP_OPENFST=$$openfst$$
      SHERPA_DEP_SIMPLE_SENTENCEPIECE=$$simple_sentencepiece$$
      bash ../native/sherpa-onnx/build-native.sh
    ndk: r29
```

(`ABIS=armeabi-v7a` for the 32-bit build.) The script writes
`jniLibs/<abi>/libsherpa-onnx-jni.so`, the Kotlin API, `LICENSE` and
`licenses/` into this directory, where the app's Gradle build reads them,
and prints each library's SHA-256 beside the one in `SHA256SUMS`. It needs
`cmake`, `ninja`, `unzip`, `curl` (or `wget`) and `git` on the host, and
fetches one file itself: `onnxruntime-android-1.28.0.aar` from Maven
Central, checked by SHA-256 (or set `ORT_AAR` to a copy). `ANDROID_NDK`
must be NDK 29.0.14206865; any other stops the build.

Three things decide whether F-Droid's libraries match the committed ones:

- **CMake 3.28 or later.** sherpa-onnx turns on link-time optimisation
  (ThinLTO) only when CMake's `check_ipo_supported` passes. CMake 3.25
  (Debian bookworm's own 3.25.1) runs that check with `-fuse-ld=gold`, which
  NDK r29 does not have, so LTO goes off and the library comes out about
  200 KB larger. The script stops when that happens. F-Droid's build server
  is Debian trixie (checked 2026-11-20), whose own CMake is 3.31.6, so the
  plain `apt-get install cmake` above is enough; on bookworm, take 3.31.6
  from bookworm-backports.
- **The NDK's path.** The unstripped link holds the NDK's header paths in
  its debug info, and the linker's build ID is a hash of that file, so it
  survives stripping. The script maps the NDK to `/opt/android-ndk-r29`
  (where the container unpacks it) with `-ffile-prefix-map`.
- **The scratch directory is `/work`.** `-ffile-prefix-map` keeps it out of
  the code and debug info, but each compiled module's `source_filename`
  keeps the absolute path, and ThinLTO hashes that into the names of the
  local symbols it promotes (`name.llvm.<hash>`). Those are stripped, but
  they are in the file the build ID is hashed from. The script uses
  `/work` unless `SHERPA_WORK` says otherwise (hence the `install -d` above), and
  says so when it is elsewhere: the libraries then differ only in their
  20-byte build ID.

Both routes were checked on 2026-11-20 and reproduced `SHA256SUMS` byte
for byte. `build.sh` (the container, sources cloned by the script) matched
every line. A plain `debian:bookworm` container stood in for F-Droid's
server: NDK r29 unzipped to an SDK-style path, the nine sources as git
checkouts under `/home/alex/fdroid/build/srclib/`, the committed libraries
deleted, `/work` made as root, then the `build:` command above run as an
unprivileged user from `android/app`, once each for arm64-v8a,
armeabi-v7a and x86_64 with x86. All four libraries, `LICENSE`, the
Kotlin API and every licence file matched (`BUILD-INFO.txt` is the
container's record and is not rewritten), and the srclib checkouts were
left unmodified. With bookworm's own CMake 3.25.1 the script stopped as
described above. Each dependency's release tarball, as pinned by
SHA-256 in sherpa-onnx's CMake, held exactly the files of its git commit.

The whole app was then built with `fdroid build` itself (fdroidserver from
git, in F-Droid's own `fdroidserver:buildserver` image, Debian trixie, the
`sudo:` steps above run as root and the build as the unprivileged user),
for both 261120011 (armeabi-v7a) and 261120012 (arm64-v8a). Each unsigned
APK was identical, file and every zip entry, to the one
`android/build-env/build.sh` makes for the same commit, so F-Droid can
verify and publish the developer-signed APK.
