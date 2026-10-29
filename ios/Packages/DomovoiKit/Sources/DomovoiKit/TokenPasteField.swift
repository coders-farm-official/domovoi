import Foundation

/// The paste field that CHECKS a household token with the server before it is
/// saved — its rules and its words, which are Android's words.
///
/// Added on Android 2026-10-17 after a real incident: the owner pasted an old
/// custom token into a reinstalled server's pairing screen, it was saved
/// without a word, and every page then failed. The network half is
/// ``DomovoiClient/checkDeviceToken(_:)``; this is the pure half of
/// `android/.../ui/screens/settings/PairingScreen.kt` — when the button is
/// live, what the hint says, and what the person is told about each answer.
///
/// The screen is I1-C's. The strings are here for ``TokenTrouble``'s reason:
/// the two apps must say the same thing to the same person about the same
/// situation, and a string that lives beside the logic choosing it is a
/// string that gets updated with it. Every sentence below is copied character
/// for character from the Kotlin, em dashes included, and
/// `TokenPasteFieldTests` is what fails if only one side moves.
public enum TokenPasteField {

    /// Whether the **pair** button is live: 12…128 characters, all printable
    /// ASCII (0x20…0x7E, space allowed).
    ///
    /// The same question ``isStorableDeviceToken(_:)`` answers, asked of the
    /// text as typed — which is why it trims first and the storable test does
    /// not: Kotlin's field holds `trimmed` and enables on `trimmed`.
    public static func isUsable(_ typed: String) -> Bool {
        isStorableDeviceToken(trimmed(typed))
    }

    /// The candidate as the field would submit it. Kotlin's `value.trim()`.
    ///
    /// ``headerSafeToken(_:)``'s note applies: Kotlin's `trim()` also removes
    /// U+001C…U+001F and Swift's `.whitespacesAndNewlines` does not, so a
    /// token with a unit separator stuck to it is submitted by Android and
    /// refused by the field here. Swift refuses where Android sends; never
    /// the reverse.
    public static func trimmed(_ typed: String) -> String {
        typed.trimmingCharacters(in: .whitespacesAndNewlines)
    }

    /// The hint under a field holding something that is not usable yet, or
    /// nil when there is nothing to say (empty, or usable).
    ///
    /// The order is Android's and it matters: a 4-character paste with a tab
    /// in it is told about the alphabet, not about the length, because the
    /// alphabet is the thing the person cannot see.
    public static func hint(_ typed: String) -> String? {
        let candidate = trimmed(typed)
        guard !candidate.isEmpty, !isStorableDeviceToken(candidate) else { return nil }
        if candidate.utf8.contains(where: { $0 < 0x20 || $0 > 0x7E }) {
            return "letters, digits, punctuation and spaces only"
        }
        return "at least \(deviceTokenMinLength) characters"
    }

    /// What the person sees after a pasted token was checked with the server;
    /// nil while there is nothing to say (accepted, or not asked yet).
    ///
    /// `waitLeftMs` is how long the server still makes this address wait,
    /// counted down by the screen — so a throttled answer whose wait has run
    /// out says the person may try again rather than naming a 0:00.
    public static func message(_ check: TokenCheck?, waitLeftMs: Int64 = 0) -> String? {
        switch check {
        case nil, .accepted:
            return nil
        case .refused:
            return "That token doesn't match this server. Copy it from Settings → Phones & browsers → Household token "
                + "on this server's dashboard."
        case .throttled:
            return waitLeftMs > 0
                ? "Too many tries — wait \(waitClock(waitLeftMs)) before the next one."
                : "You can try again now."
        case .unavailable:
            // A 503, no answer, or a server that did not prove itself. NOT
            // "that token is wrong": the server never gave a verdict, and
            // telling a person their token is wrong when the box is
            // restarting is how a working token gets thrown away.
            return "Couldn't check it with the server — try again."
        }
    }
}
