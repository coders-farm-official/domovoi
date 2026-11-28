# domovoi_tor: away chat's Tor

Chat from away (Domovoi 1.5.0) reaches the house's away onion over Tor. This
crate is the Tor part, shared by both apps: [Arti](https://gitlab.torproject.org/tpo/core/arti)
(the Tor Project's Tor in Rust, MIT OR Apache-2.0) as an embedded onion client
with client authorization, used as a byte pipe. Away chat's own protocol
(the `DAC1` preface, Noise IK, framed messages) stays in Kotlin and Swift.

- `start(state, cache, authDir[, trustedPrefix])` bootstraps one Tor client
  (blocking, up to two minutes). `authDir` holds `<onion56>.auth_private`
  (`<onion56>:descriptor:x25519:<base32 private key>`, 0600, folder 0700),
  read through Arti's stable C-Tor client keystore. The app writes it when
  Tor starts and deletes it on stop; `state/` and `cache/` stay.
- `open(onion, port)` returns a file descriptor: one end of a Unix socket
  pair, the other end copied to and from the Tor stream.
- `stop()` drops the client.

C ABI for the iPhone app: `dtor_start`, `dtor_open`, `dtor_stop`. JNI for the
Android app: `com.domovoi.app.away.ArtiTor` (`nativeStart`, `nativeOpen`,
`nativeStop`, `nativeLastError`). Results are a descriptor or `0` (ok), or a
negative `ERR_*` (`src/lib.rs`).

## Pins

- `arti-client = "=0.48.0"` (Arti 2.8.0) and its `tor-*` crates exactly;
  `Cargo.lock` is committed and every build is `--locked`. Stable features
  only: tokio, rustls (ring), onion-service-client, vanguards, keymgr,
  static-sqlite, compression.
- Rust 1.100.1, cargo-ndk 4.1.2, NDK r29 (29.0.14206865), Android API 26;
  for the iPhone, Xcode and iOS 18.0.
- Release profile: `opt-level = "z"`, LTO, one codegen unit, stripped;
  `panic = "unwind"` so each FFI entry's `catch_unwind` turns a panic into
  `ERR_PANIC` instead of ending the app.

## Android

```bash
bash native/domovoi-tor/build-android.sh            # build all four ABIs, compare with the committed SHA256SUMS
bash native/domovoi-tor/build-android.sh --install  # ...and replace android/native/domovoi-tor/jniLibs
```

It checks the pinned rustc, cargo-ndk and NDK, builds with
`cargo ndk --locked --platform 26 -t <abi>` for arm64-v8a, armeabi-v7a,
x86_64 and x86 (the ABIs the app ships), with `--remap-path-prefix` for the
crate and Cargo's folders, `SOURCE_DATE_EPOCH`, and 16 KB pages for the
64-bit libraries. About 40 s per ABI on the owner's Mac.

**F-Droid** builds the app from source: its build recipe runs
`bash native/domovoi-tor/build-android.sh --install` (with the pinned Rust
toolchain, cargo-ndk and the NDK installed by its `sudo`/`srclibs` steps)
before Gradle, as it runs `android/native/sherpa-onnx/build-native.sh`.

After any `Cargo.lock` change, write the licence notice again:
`python3 native/domovoi-tor/third_party_licences.py`
(android/licences/domovoi-tor/THIRD-PARTY-LICENSES.txt, and its line in
android/licences/SHA256SUMS). One crate is MPL-2.0 (option-ext, unmodified);
the rest are permissive.

## iPhone

```bash
bash native/domovoi-tor/build-ios.sh            # build, compare with the committed SHA256SUMS
bash native/domovoi-tor/build-ios.sh --install  # ...and replace ios/Vendor/domovoi-tor
```

It builds the same crate as a static library (`cargo rustc --locked
--crate-type staticlib`) for `aarch64-apple-ios` (the iPhone) and for
`aarch64-apple-ios-sim` and `x86_64-apple-ios` (one universal Simulator
library: the app's Simulator builds are both architectures), with
`IPHONEOS_DEPLOYMENT_TARGET` set to the app's (18.0, checked against
`ios/project.yml`), the same `--remap-path-prefix` and `SOURCE_DATE_EPOCH`
as Android's (and `-ffile-prefix-map` for the C parts), then
`strip -S -x` (a static library isn't linked, so the profile's `strip`
doesn't reach it). It packages the two libraries with `include/`
(`domovoi_tor.h` and the module map for `DomovoiTorFFI`) as
`ios/Vendor/domovoi-tor/DomovoiTor.xcframework`, with `SHA256SUMS` and
`BUILD-INFO.txt`. Needs Xcode and the three Rust targets (`rustup target
add aarch64-apple-ios aarch64-apple-ios-sim x86_64-apple-ios`); about a
minute per target on the owner's Mac. DomovoiKit links it on iOS only
(`DomovoiTorEngine`).

sysinfo 0.38.4 (through tor-memquota) doesn't build for iOS, so both
platforms build it from `vendor/sysinfo-0.38.4` (`[patch.crates-io]`), a
one-line fix upstream made in 0.40.0; `vendor/README.md` has the change and
when to drop it. The iPhone app's licence notice is
`python3 native/domovoi-tor/third_party_licences.py --ios`
(ios/licences/notices/domovoi-tor/THIRD-PARTY-LICENSES.txt: the crates of
all three targets); write it again after any `Cargo.lock` change, as
Android's.
