import Foundation
import Observation

/// Which side of the server-or-device switch each page is showing.
///
/// `ui/screens/local/YourDevice.kt:17-54`, which is three small types and one
/// fact that matters more than all of them: **every page starts on the
/// server's side** (`YourDevice.kt:30`). A phone that opened Music on its own
/// files would look like a phone that had lost the house.

/// The pages that offer "your device" (`YourDevice.kt:17`).
///
/// Android's enum has a fourth case, `Audiobooks`. It is **left out** here:
/// the device side of Audiobooks is the books saved on the phone, and there
/// is no saved-book store on iOS yet (`ShellModel.savedBooks` is a constant
/// `false` whose own comment says the store is a later batch's). A switch
/// whose device segment leads nowhere is the control the owner's rule forbids
/// ([[no-misleading-controls]]), so Audiobooks keeps its plain route until
/// the store lands. DECISIONS records it.
public enum PhoneMediaPageKind: String, Codable, Equatable, Sendable, CaseIterable {
    case music = "Music"
    case videos = "Videos"
    case files = "Files"
}

/// `YourDevice.kt:20`.
public enum PhoneContentSource: String, Codable, Equatable, Sendable, CaseIterable {
    case server = "Server"
    case device = "Device"
}

/// The side each page last showed, for as long as the workspace lives.
///
/// Android's `SourceChoices` (`YourDevice.kt:27`) is provided by the
/// workspace shell — one per server, kept across a trip through local media
/// and a rotation. `ShellModel` is do-not-edit here, so the iOS counterpart
/// is held by the one view that wraps all three routes and therefore has the
/// same lifetime: it survives a tab switch inside a workspace and is rebuilt
/// when the workspace is.
///
/// ``encode()`` / ``decode(_:)`` keep Android's `"Music=Device"` wire shape,
/// to the letter, so the two apps' saved state is readable by the same eyes —
/// and an entry neither enum recognises is **dropped**, not defaulted, which
/// is what lets `Audiobooks=Device` from an Android phone's words pass
/// through harmlessly.
@MainActor
@Observable
public final class PhoneSourceChoices {

    private var chosen: [PhoneMediaPageKind: PhoneContentSource] = [:]

    public init() {}

    /// `YourDevice.kt:30`. The server's side until the person says otherwise.
    public func of(_ page: PhoneMediaPageKind) -> PhoneContentSource {
        chosen[page] ?? .server
    }

    public func choose(_ page: PhoneMediaPageKind, _ source: PhoneContentSource) {
        chosen[page] = source
    }

    /// `YourDevice.kt:37`. Sorted, which the Kotlin's map iteration is not:
    /// an order that depends on which page the person tapped first would make
    /// a saved-state comparison in a test depend on it too.
    public func encode() -> [String] {
        chosen
            .map { "\($0.key.rawValue)=\($0.value.rawValue)" }
            .sorted()
    }

    /// `YourDevice.kt:41`. Restores what ``encode()`` wrote.
    public static func decode(_ saved: [String]) -> PhoneSourceChoices {
        let out = PhoneSourceChoices()
        for entry in saved {
            let halves = entry.split(separator: "=", maxSplits: 1, omittingEmptySubsequences: false)
            guard halves.count == 2,
                  let page = PhoneMediaPageKind(rawValue: String(halves[0])),
                  let source = PhoneContentSource(rawValue: String(halves[1]))
            else { continue }
            out.choose(page, source)
        }
        return out
    }
}
