import Foundation

/// Which source chips a document reply shows, in what order, and where the
/// matched sentence sits inside a passage.
///
/// Ported from `ChatDocsUi.kt:94` (`orderedSources`), `:98` (`matchRange`) and
/// `:105` (`sourceChipText`). Pure, so the three decisions a reader notices —
/// a cited chip before an uncited one, one chip per page, the matched sentence
/// marked — are testable without a view.
public enum ChatMoreSourceRules {

    /// Cited sources first, each group in the order the server gave them; one
    /// chip per label.
    ///
    /// Two passages of one page that overlap by a sentence are one "p. 4" —
    /// the cited one, else the first. `distinctBy` in Kotlin keeps the
    /// **first** of each key, and the cited ones are all moved ahead of the
    /// uncited ones before that happens, so a page both cited and merely read
    /// shows as cited.
    public static func ordered(_ sources: [ChatMoreSource]) -> [ChatMoreSource] {
        var seen: Set<String> = []
        var out: [ChatMoreSource] = []
        for source in sources.filter(\.cited) + sources.filter { !$0.cited } {
            if seen.insert(source.label).inserted { out.append(source) }
        }
        return out
    }

    /// A chip's words: its label, and whether the reply cited it
    /// (`ChatDocsUi.kt:105`).
    public static func chipText(_ source: ChatMoreSource) -> String {
        source.cited ? source.label : "\(source.label) · read"
    }

    /// A chip's screen-reader sentence (`ChatDocsUi.kt:188`).
    public static func chipDescription(_ source: ChatMoreSource) -> String {
        ChatMoreStrings.sourceLabel(source.label)
            + (source.cited ? "" : ChatMoreStrings.sourceReadNotCited)
    }

    /// Where `match` sits inside `text` — the sentence the page sheet marks —
    /// or nil when the server's match is blank or is not in the passage
    /// (`ChatDocsUi.kt:98`).
    ///
    /// A `Range<String.Index>` rather than Kotlin's `IntRange`: a passage can
    /// hold anything a document holds, and an index into UTF-16 offsets is how
    /// a marked range lands on the wrong character of a passage with an emoji
    /// or a combining accent in it.
    public static func matchRange(in text: String, match: String) -> Range<String.Index>? {
        guard !match.trimmingCharacters(in: .whitespacesAndNewlines).isEmpty else { return nil }
        return text.range(of: match)
    }

    /// The notes above the chips, as lines (`ChatDocsUi.kt:171`).
    public static func noteLines(_ notes: [ChatMoreDocNote]) -> [String] {
        notes.map(\.text).filter { !$0.trimmingCharacters(in: .whitespacesAndNewlines).isEmpty }
    }

    /// Whether `ReplyDocs` draws at all (`ChatDocsUi.kt:169`).
    public static func hasAnything(notes: [ChatMoreDocNote], sources: [ChatMoreSource]) -> Bool {
        !noteLines(notes).isEmpty || !sources.isEmpty
    }
}
