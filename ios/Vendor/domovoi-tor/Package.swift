// swift-tools-version:6.0
import PackageDescription

// Away chat's Tor for the iPhone: Arti (the Tor Project's Tor in Rust, MIT OR
// Apache-2.0) as an embedded onion client, from the repository's
// native/domovoi-tor crate. `DomovoiTor.xcframework` is what that crate's
// build-ios.sh writes (static libraries for the iPhone and the Simulator,
// with its C header as the module `DomovoiTorFFI`); SHA256SUMS and
// BUILD-INFO.txt beside it say which bytes and what built them. DomovoiKit
// links it on iOS only (`DomovoiTorEngine`); README.md says more.
let package = Package(
    name: "domovoi-tor",
    platforms: [.iOS(.v18)],
    products: [
        .library(name: "DomovoiTor", targets: ["DomovoiTor"]),
    ],
    targets: [
        .binaryTarget(name: "DomovoiTor", path: "DomovoiTor.xcframework"),
    ]
)
