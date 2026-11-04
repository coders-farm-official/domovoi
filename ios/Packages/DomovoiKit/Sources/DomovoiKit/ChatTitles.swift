import Foundation

/// A chat's title when the server has not written one yet — the third link of
/// ``ChatModel/openTitle``'s chain.
///
/// `ChatTitles.kt` (`com.domovoi.app.assistant`), which is itself kept in step
/// with the server's `domovoi/chat_titles.py`. **Only ``firstWords(_:)`` is
/// ported**, because only that one is a decision this app makes: the rest of
/// the Kotlin object is the phone-side title *job* — the prompt, the token
/// budget, the retry count, `clean` — and this app has no local model to run
/// one with. The server's job writes the real title and it arrives through the
/// list (`ServerChatPlace.kt:149`), which is the chain's first link.
public enum ChatTitles {

    /// `ChatTitles.kt:20`.
    public static let words = 5

    /// `ChatTitles.kt:21`.
    public static let maxChars = 60

    /// The first ``words`` words of `text`, with `…` when there were more (or
    /// they ran past ``maxChars``); nil when there are no words at all
    /// (`ChatTitles.kt:63`).
    public static func firstWords(_ text: String?) -> String? {
        let words = Self.words
        let found = plainText(text ?? "")
            .split(whereSeparator: { $0.isWhitespace })
            .map(String.init)
        guard !found.isEmpty else { return nil }
        var title = found.prefix(words).joined(separator: " ")
        var cut = found.count > words
        if title.count > maxChars {
            title = String(title.prefix(maxChars))
            while title.last?.isWhitespace == true { title.removeLast() }
            cut = true
        }
        return cut ? "\(title)\u{2026}" : title
    }

    /// `text` without control or format characters: controls (tabs, escapes)
    /// become spaces, format characters go, **so a title cannot reorder or hide
    /// what the list draws around it** (`ChatTitles.kt:42`). A bidi override
    /// (U+202E) in the first words of a message is the attack this is for.
    ///
    /// `Character.getType` in the Kotlin; `Unicode.Scalar.Properties
    /// .generalCategory` here, which is the same Unicode category by another
    /// name.
    private static func plainText(_ text: String) -> String {
        var out = ""
        out.reserveCapacity(text.unicodeScalars.count)
        for scalar in text.unicodeScalars {
            switch scalar.properties.generalCategory {
            case .format:
                continue
            case .control, .lineSeparator, .paragraphSeparator:
                out.unicodeScalars.append(" ")
            default:
                out.unicodeScalars.append(scalar)
            }
        }
        return out
    }
}
