// swift-tools-version:6.0
import PackageDescription

// A skeleton. The phonemizer itself is its own milestone (plan 2.5) and must
// not be started before `swift test` runs on the Mac: it is about 1,730 lines
// of Kotlin whose whole point is string and regex behaviour that differs
// between languages, which is exactly the kind of port that cannot be written
// blind. See README.md beside this file.
let package = Package(
    name: "DomovoiPhonemizer",
    platforms: [
        .iOS(.v18),
        .macOS(.v15),
    ],
    products: [
        .library(name: "DomovoiPhonemizer", targets: ["DomovoiPhonemizer"]),
    ],
    targets: [
        .target(name: "DomovoiPhonemizer"),
    ]
)
