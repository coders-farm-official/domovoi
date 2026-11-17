// swift-tools-version:6.0
import PackageDescription

// The Mac host side of Domovoi (mac-installer PLAN 4.8, 4.13, 6 item 9).
//
// - DomovoiMacKit: the logic, with no UI. Paths (Layout), the pins, the Lima
//   template, the launchd jobs, the Ollama runner, lan.json and side.json,
//   the install record, and the plan/apply engine for install and uninstall.
//   Everything that touches the machine goes through `CommandRunner` and
//   `FileOps`, so the tests run it against a temporary folder with fakes.
// - DomovoiHelper: the command-line helper. `--system` (root) is the real
//   install: the role account, /Library, LaunchDaemons in the system domain,
//   Domovoi's own ports. `--user-mode --prefix DIR` runs the whole install
//   as the signed-in user into DIR (no root, test ports).
// - domovoi-uninstall: removes what the install record lists, then (system
//   install) what the package put there, nothing else.
// - Domovoi: the app the package installs, with the setup pages (PLAN 5):
//   the check, what changes, the questions, the work, the setup code; and
//   repair and uninstall. It never runs as root: the helper does the work
//   behind the system's administrator prompt.
//
// Its tests run on the Mac with `swift test`, as the iPhone app's DomovoiKit
// does. That package is separate (ios/Packages/DomovoiKit); this one is named
// DomovoiMacKit so the two never meet under one module name. The Ed25519
// release check the Mac side needs later (PLAN 4.12) can be shared then.
//
// The core installer is for Apple Silicon on macOS 26 or later (owner answer
// 2026-10-28); the helper checks that when it runs. The platform floor below
// is only what the package compiles against.
let package = Package(
    name: "DomovoiMac",
    platforms: [
        .macOS(.v15),
    ],
    products: [
        .library(name: "DomovoiMacKit", targets: ["DomovoiMacKit"]),
        .executable(name: "DomovoiHelper", targets: ["DomovoiHelper"]),
        .executable(name: "domovoi-uninstall", targets: ["domovoi-uninstall"]),
        .executable(name: "Domovoi", targets: ["Domovoi"]),
    ],
    targets: [
        .target(name: "DomovoiMacKit", path: "Sources/DomovoiMacKit"),
        .executableTarget(name: "DomovoiHelper", dependencies: ["DomovoiMacKit"], path: "Sources/DomovoiHelper"),
        .executableTarget(name: "domovoi-uninstall", dependencies: ["DomovoiMacKit"], path: "Sources/domovoi-uninstall"),
        // The setup pages (SwiftUI); mac/build.sh wraps it in Domovoi.app.
        .executableTarget(name: "Domovoi", dependencies: ["DomovoiMacKit"], path: "Sources/Domovoi"),
        // The executables too: SetupCommandsTests runs the app's command lines
        // through the very binaries the app calls (P3-7).
        .testTarget(name: "DomovoiMacKitTests", dependencies: ["DomovoiMacKit", "DomovoiHelper", "domovoi-uninstall"], path: "tests/DomovoiMacKitTests"),
    ],
    swiftLanguageModes: [.v5]
)
