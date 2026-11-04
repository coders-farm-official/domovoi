import Foundation

/// One tool on the markdown editor's formatting row: wrap the selection, or
/// prefix the line the caret is on.
///
/// `DocumentsEditor.kt:58-70`'s `MdTool` and `MD_TOOLS`, label for label.
public struct DocumentsMarkdownTool: Equatable, Sendable, Identifiable {

    /// What the button says: `B`, `I`, `H1`, `H2`, `•`, `1.`, `` ` ``, `>`.
    public let label: String
    /// Inserted before the selection, or at the start of its first line.
    public let before: String
    /// Inserted after the selection. Empty for a line prefix.
    public let after: String
    /// A heading, a list item or a quote: the mark goes once at the start of
    /// the line rather than around what is selected.
    public let linePrefix: Bool

    /// The label is the identity, as it is in the Kotlin's list — no two
    /// tools share one, and a `ForEach` needs something stable.
    public var id: String { label }

    /// The button's name for VoiceOver, which the Kotlin has no need of: a
    /// reader announcing "B" says the letter, not what it does.
    public let spoken: String

    public init(label: String, before: String, after: String = "", linePrefix: Bool = false, spoken: String) {
        self.label = label
        self.before = before
        self.after = after
        self.linePrefix = linePrefix
        self.spoken = spoken
    }
}

/// The text a tool left behind, and where the selection ended up in it.
public struct DocumentsMarkdownEdit: Equatable, Sendable {
    public let text: String
    /// Character offsets into ``text``, as a half-open range. Offsets rather
    /// than `String.Index`es because an index into the **old** string is
    /// meaningless in the new one, and a view that has to re-derive its
    /// selection anyway is better handed numbers it can convert once.
    public let selection: Range<Int>

    public init(text: String, selection: Range<Int>) {
        self.text = text
        self.selection = selection
    }
}

/// The eight tools, and the one pure function that applies them.
///
/// It is a type of its own, and takes and returns plain values, for the
/// reason the rest of this package keeps its rules out of its views: a
/// toolbar that wraps the wrong range is a bug a person discovers in their
/// own notes, and the only cheap place to catch it is a test.
public enum DocumentsMarkdownTools {

    /// `MD_TOOLS` (`DocumentsEditor.kt:61-70`), in order.
    public static let all: [DocumentsMarkdownTool] = [
        DocumentsMarkdownTool(label: "B", before: "**", after: "**", spoken: "bold"),
        DocumentsMarkdownTool(label: "I", before: "*", after: "*", spoken: "italic"),
        DocumentsMarkdownTool(label: "H1", before: "# ", linePrefix: true, spoken: "heading 1"),
        DocumentsMarkdownTool(label: "H2", before: "## ", linePrefix: true, spoken: "heading 2"),
        DocumentsMarkdownTool(label: "•", before: "- ", linePrefix: true, spoken: "bullet list"),
        DocumentsMarkdownTool(label: "1.", before: "1. ", linePrefix: true, spoken: "numbered list"),
        DocumentsMarkdownTool(label: "`", before: "`", after: "`", spoken: "code"),
        DocumentsMarkdownTool(label: ">", before: "> ", linePrefix: true, spoken: "quote"),
    ]

    /// Apply `tool` to `text` at `selection` — `applyMdTool`
    /// (`DocumentsEditor.kt:72-88`).
    ///
    /// `selection` is in **characters**, clamped to the text: a stale caret
    /// from a keystroke that has not reached the model yet must not trap, and
    /// a crash in a formatting button is a worse answer than a mark in the
    /// wrong place.
    ///
    /// Two shapes, as the Kotlin has:
    ///
    /// * a wrap puts `before` and `after` around what is selected and leaves
    ///   the **selection on the same words**, so a second tap on `B` wraps
    ///   what the first one did rather than the marks;
    /// * a line prefix inserts at the start of the line the selection begins
    ///   on and collapses the selection to its end, moved along by what was
    ///   inserted.
    ///
    /// ### The one departure from the Kotlin
    ///
    /// Kotlin's line search is `lastIndexOf('\n', (s - 1).coerceAtLeast(0))`,
    /// which for a caret at offset 0 searches **index 0** — so in a document
    /// that starts with a newline it finds that newline and prefixes the
    /// SECOND line, from a caret sitting on the first. Here a caret at 0 is
    /// the start of the first line by definition. Recorded in
    /// `overnight/documents/DECISIONS.md`.
    public static func apply(_ tool: DocumentsMarkdownTool, to text: String, selection: Range<Int>) -> DocumentsMarkdownEdit {
        let characters = Array(text)
        let start = min(max(selection.lowerBound, 0), characters.count)
        let end = min(max(selection.upperBound, start), characters.count)

        if tool.linePrefix {
            var lineStart = 0
            if start > 0 {
                var scan = start - 1
                while scan >= 0 {
                    if characters[scan] == "\n" { lineStart = scan + 1; break }
                    scan -= 1
                }
            }
            var out = characters
            out.insert(contentsOf: Array(tool.before), at: lineStart)
            let caret = end + tool.before.count
            return DocumentsMarkdownEdit(text: String(out), selection: caret..<caret)
        }

        let selected = Array(characters[start..<end])
        var out = Array(characters[0..<start])
        out += Array(tool.before)
        out += selected
        out += Array(tool.after)
        out += Array(characters[end..<characters.count])
        let from = start + tool.before.count
        return DocumentsMarkdownEdit(text: String(out), selection: from..<(from + selected.count))
    }
}
