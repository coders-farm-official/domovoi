// swift-tools-version:6.0
import PackageDescription

// The logic the app's screens sit on: the client's rules, pairing, identity,
// the protocol and the models. It has no UI and no dependencies, so its tests
// run on the Mac itself with `swift test` — fast, with nothing on screen and
// no Simulator (plan 5.1). macOS 15 is here only so those tests can run; the
// app ships for iOS 18 and up (owner answer Q1).
//
// It compiles and its tests run: `ios/scripts/pkgtest.sh` pushes the current
// commit to the Mac and runs `swift test` there, which is the loop every batch
// in this package works to.
let package = Package(
    name: "DomovoiKit",
    platforms: [
        .iOS(.v18),
        .macOS(.v15),
    ],
    products: [
        .library(name: "DomovoiKit", targets: ["DomovoiKit"]),
    ],
    targets: [
        .target(name: "DomovoiKit"),
        .testTarget(name: "DomovoiKitTests", dependencies: ["DomovoiKit"]),
    ]
)
