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

    /// `10.0.0.42:6369` — the address a person can check against the box.
    public static func address(_ url: String) -> String {
        let clean = normalize(url)
        if clean.hasPrefix("http://") { return String(clean.dropFirst("http://".count)) }
        if clean.hasPrefix("https://") { return String(clean.dropFirst("https://".count)) }
        return clean
    }
}
