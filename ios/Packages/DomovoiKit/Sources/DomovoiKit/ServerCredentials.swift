import Foundation

/// The key every per-server answer is kept under.
///
/// The two functions this batch needs out of
/// `android/.../data/ServerCredentials.kt`: the saved address normalised (the
/// key for the token, for a refusal, for a wait, for a shared-screen answer)
/// and the same address with its scheme off, which is what a card shows a
/// person so they can check it against the box.
///
/// The rest of the Kotlin object — the trust list, the pin rules, the owner
/// and shared-screen maps, the JSON encodings — belongs to batches that have
/// something to store, and is deliberately not here. Two Domovois are two
/// households with two tokens, and the normalised address is what tells them
/// apart.
public enum ServerCredentials {

    /// Kotlin's `url.trim().trimEnd('/')`: trailing slashes and stray
    /// whitespace are not part of a server's identity. `trimEnd` drops EVERY
    /// trailing slash, not just one, so the loop is not an accident.
    public static func normalize(_ url: String) -> String {
        var clean = url.trimmingCharacters(in: .whitespacesAndNewlines)
        while clean.hasSuffix("/") { clean = String(clean.dropLast()) }
        return clean
    }

    /// Kotlin's `String.isBlank()`: empty, or nothing but whitespace.
    ///
    /// Every shell decision that asks "is a server saved?" asks this, because
    /// that is what the Kotlin asks (`serverUrl.isBlank()` in `shellMode`,
    /// `tokenTrouble` and `serverChoice`). It is **not** `normalize(_:).isEmpty`:
    /// `normalize` also strips trailing slashes, so `"/"` normalizes to the
    /// empty string while Kotlin's `"/".isBlank()` is false. Both are nonsense
    /// as a server address and neither is reachable, but the two apps should
    /// take the same branch on the same input, and `tokenTrouble` used to take
    /// the other one.
    ///
    /// Kotlin's `isBlank` is `Char.isWhitespace`, which covers the Unicode
    /// space/line/paragraph separators plus the ASCII control whitespace
    /// U+0009…U+000D and U+001C…U+001F. Swift's `.whitespacesAndNewlines` is
    /// the first group but not U+001C…U+001F, the same gap
    /// ``TokenPasteField/trimmed(_:)`` documents — so a server address of
    /// nothing but file separators counts as blank on Android and as a saved
    /// server here. It then fails to parse and nothing is sent to it.
    public static func isBlank(_ url: String) -> Bool {
        url.trimmingCharacters(in: .whitespacesAndNewlines).isEmpty
    }

    /// `10.0.0.42:6369` — the address a person can check against the box.
    public static func address(_ url: String) -> String {
        let clean = normalize(url)
        if clean.hasPrefix("http://") { return String(clean.dropFirst("http://".count)) }
        if clean.hasPrefix("https://") { return String(clean.dropFirst("https://".count)) }
        return clean
    }
}
