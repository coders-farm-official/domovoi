import Foundation

/// The manual's inline markup, as spans — `parseInline`
/// (`ManualContent.kt:202`) and its `Span` hierarchy (`:186`).
///
/// The file's own `notes.inline` defines it: "a small Markdown subset:
/// `**bold**`, `*italic*`, `` `code` `` and `[text](target)` links. A target
/// is `route:<id>`, `node:<id>` or `doc:<repo path>#<anchor>`. `{admin}`,
/// `{preview}` and `{plugin}` are pills; `{wake}` is the wake word."
///
/// One scan, left to right, non-overlapping — the same loop as the Kotlin's,
/// so the same text yields the same spans in the same order. What the spans
/// are *drawn* as is `ManualRichText`'s; that a `route:` link to a screen
/// this server does not offer is drawn as **plain text** is
/// ``manualRouteOpens(_:_:)``, not this parser's business.
public enum ManualSpan: Equatable, Sendable {
    case plain(String)
    case bold(String)
    case italic(String)
    case code(String)
    /// `kind` is `route` | `node` | `doc`.
    case link(text: String, kind: String, target: String)
    /// `admin` | `preview` | `plugin` — a pill, worded from
    /// `labels.<name>.text`.
    case tag(String)
    /// `{wake}`: the wake word, quoted, in the lead phrase of
    /// ``ManualWakeInfo``.
    case wake
}

/// A compiled pattern that can cross an isolation boundary.
///
/// `@unchecked Sendable` with the reason, as the house rule asks:
/// `NSRegularExpression` is documented immutable and thread-safe once built,
/// the only stored property is `let`, and nothing here ever mutates it. The
/// alternative was compiling two patterns on every block of every page.
private struct ManualPattern: @unchecked Sendable {
    let regex: NSRegularExpression

    init(_ pattern: String) {
        // A literal pattern this file owns: a failure here is a programming
        // error at launch, not a server's input.
        regex = try! NSRegularExpression(pattern: pattern)
    }
}

/// `TOKEN` (`ManualContent.kt:196`), with one deliberate difference:
/// `[^*\s]` is written out as the ASCII whitespace class.
///
/// Kotlin's `Regex` is `java.util.regex`, whose `\s` is `[ \t\n\x0B\f\r]` and
/// ASCII only; `NSRegularExpression` is ICU, whose `\s` also matches the
/// Unicode spaces — so `*\u{00A0}italic*` would be italic on Android and
/// plain text here. Spelling the class out keeps the two platforms on the
/// same text. The rest of the pattern uses only negated literal classes,
/// which the two engines read identically.
private let manualTokenPattern = ManualPattern(
    #"\*\*[^*]+\*\*|\*[^* \t\n\x{0B}\f\r][^*]*\*|`[^`]+`|\[[^\]]+\]\((?:route|node|doc):[^)]+\)|\{(?:admin|preview|plugin|wake)\}"#
)

/// `LINK` (`ManualContent.kt:200`).
private let manualLinkPattern = ManualPattern(
    #"^\[([^\]]+)\]\((route|node|doc):([^)]+)\)$"#
)

/// `parseInline` (`ManualContent.kt:202`).
public func parseManualInline(_ text: String) -> [ManualSpan] {
    let whole = text as NSString
    var out: [ManualSpan] = []
    var at = 0
    let matches = manualTokenPattern.regex.matches(
        in: text,
        range: NSRange(location: 0, length: whole.length)
    )
    for match in matches {
        if match.range.location > at {
            let gap = NSRange(location: at, length: match.range.location - at)
            out.append(.plain(whole.substring(with: gap)))
        }
        out.append(manualSpan(of: whole.substring(with: match.range)))
        at = match.range.location + match.range.length
    }
    if at < whole.length { out.append(.plain(whole.substring(from: at))) }
    return out
}

/// One token as its span. The order of the tests is the Kotlin's `when`,
/// which matters: `{wake}` is checked before the general `{tag}`, and `**`
/// before the single `*`.
private func manualSpan(of token: String) -> ManualSpan {
    if token.hasPrefix("**") {
        return .bold(String(token.dropFirst(2).dropLast(2)))
    }
    if token.hasPrefix("`") {
        return .code(String(token.dropFirst().dropLast()))
    }
    if token == "{wake}" {
        return .wake
    }
    if token.hasPrefix("{") {
        return .tag(String(token.dropFirst().dropLast()))
    }
    if token.hasPrefix("[") {
        let whole = token as NSString
        guard
            let match = manualLinkPattern.regex.firstMatch(
                in: token,
                range: NSRange(location: 0, length: whole.length)
            ),
            match.numberOfRanges == 4
        else {
            // Kotlin asserts here (`LINK.find(t)!!`) because the token
            // pattern already proved the shape. Unreachable, and a crash in
            // a manual page is not worth being right about: the token reads
            // as the text it is.
            return .plain(token)
        }
        return .link(
            text: whole.substring(with: match.range(at: 1)),
            kind: whole.substring(with: match.range(at: 2)),
            target: whole.substring(with: match.range(at: 3))
        )
    }
    return .italic(String(token.dropFirst().dropLast()))
}
