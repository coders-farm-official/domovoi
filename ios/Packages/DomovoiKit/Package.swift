// swift-tools-version:6.0
import PackageDescription

// The logic the app's screens sit on: the client's rules, pairing, identity,
// the protocol and the models. It has no UI, so its tests run on the Mac
// itself with `swift test` — fast, with nothing on screen and no Simulator
// (plan 5.1). macOS 15 is here only so those tests can run; the app ships for
// iOS 18 and up (owner answer Q1).
//
// It compiles and its tests run: `ios/scripts/pkgtest.sh` pushes the current
// commit to the Mac and runs `swift test` there, which is the loop every batch
// in this package works to.
//
// ## Its one dependency: ONNX Runtime (room mode, batch 2026-11-14)
//
// Microsoft's own Swift package, pinned EXACTLY. Its `onnxruntime` product is
// the Objective-C API (`import OnnxRuntimeBindings`) over a binary target, the
// CocoaPods "onnxruntime-c" archive, which SwiftPM fetches from
// download.onnxruntime.ai and checks against the SHA-256 in that package's
// manifest at this tag. The xcframework inside has an `ios-arm64` slice, an
// `ios-arm64_x86_64-simulator` slice and a `macos-arm64_x86_64` slice, so the
// same library runs here under `swift test` and in the app (read from the
// archive on 2026-11-14). MIT; the licence card's row and the notices it
// ships are `ios/licences/README.md`'s procedure (`LicencesTests`).
//
// What uses it: room mode's openWakeWord networks and Silero voice detection
// (`OrtWakeNets`, `SileroVad`), through ``OnnxModel``. Later: the phonemizer's
// G2P model and Piper voices — against THIS copy, never a second one (plan
// 2.2: two ONNX Runtimes in one app clash).
let package = Package(
    name: "DomovoiKit",
    platforms: [
        .iOS(.v18),
        .macOS(.v15),
    ],
    products: [
        .library(name: "DomovoiKit", targets: ["DomovoiKit"]),
    ],
    dependencies: [
        .package(url: "https://github.com/microsoft/onnxruntime-swift-package-manager", exact: "1.24.2"),
    ],
    targets: [
        .target(
            name: "DomovoiKit",
            dependencies: [
                .product(name: "onnxruntime", package: "onnxruntime-swift-package-manager"),
            ]
        ),
        .testTarget(
            name: "DomovoiKitTests",
            dependencies: ["DomovoiKit"],
            // Bytes the tests load by name (`Bundle.module`): the tiny ONNX
            // model ios/scripts/make-onnx-fixture.py writes, and room mode's
            // fixture audio when R1 adds it. Never a pretrained wake-word
            // model: their licence keeps them out of the repo (Android's rule).
            resources: [
                .copy("Fixtures"),
            ]
        ),
    ]
)
