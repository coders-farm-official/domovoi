import Foundation

/// Searching the conversations kept on this phone
/// (`ConversationStore.kt:184-215`), ported line for line.
///
/// It is a file of its own, and a free function rather than a method on the
/// store, for the Kotlin's own reason: the search is over
/// **conversations the caller already holds**, so a test drives it with a list
/// and never touches a disk, and the store's `search` is one line that reads
/// everything and calls this.

/// Characters shown either side of a search match (`MATCH_CONTEXT`,
/// `ConversationStore.kt:203`).
let assistantMatchContext = 40

/// The conversations matching `query`, newest first: those whose title or any
/// message contains it, case and spacing ignored. A blank query finds nothing
/// (`searchConversations`, `ConversationStore.kt:188`).
///
/// Two details that are easy to lose and each have a test:
///
///  - **A message match beats a title match**, and the hit then carries the
///    snippet; a title-only match carries nil. The list draws the snippet
///    where the last words would be, so a hit with no snippet is the list
///    saying "the name matched".
///  - **The messages are searched newest first** (`asReversed()`), so the
///    snippet is from the most recent place the words appear, not the first.
public func assistantSearchConversations(
    _ conversations: [AssistantConversation],
    query: String
) -> [AssistantConversationHit] {
    let q = query
        .trimmingCharacters(in: .whitespacesAndNewlines)
        .split(whereSeparator: { $0.isWhitespace })
        .joined(separator: " ")
    guard !q.isEmpty else { return [] }

    let hits: [AssistantConversationHit] = conversations.compactMap { conversation in
        let inMessage = conversation.messages.reversed().lazy
            .compactMap { assistantMatchSnippet($0.text, q) }
            .first
        if let inMessage {
            return AssistantConversationHit(summary: conversation.summary, snippet: inMessage)
        }
        if conversation.title.range(of: q, options: .caseInsensitive) != nil {
            return AssistantConversationHit(summary: conversation.summary, snippet: nil)
        }
        return nil
    }

    // Newest first, and on a tie the id descending — the Kotlin's second
    // comparator, which is what makes the order of two conversations saved in
    // the same millisecond the same on every read (the ids carry the
    // millisecond and a random tail: `assistantNewId`).
    return hits.sorted {
        if $0.summary.updated != $1.summary.updated { return $0.summary.updated > $1.summary.updated }
        return $0.summary.id > $1.summary.id
    }
}

/// The words around the first place `q` appears in `text` (case ignored,
/// spacing flattened), `…` where it was cut; nil when it is not there
/// (`matchSnippet`, `ConversationStore.kt:208`).
///
/// The context is counted in **characters as Swift counts them** where the
/// Kotlin counts UTF-16 units, so a snippet around an emoji is a few code
/// units wider here than there. That is the right difference to keep: the
/// number is "about forty characters either side", and cutting a `String` by
/// UTF-16 offsets is how a port comes to slice a grapheme in half.
public func assistantMatchSnippet(_ text: String, _ q: String) -> String? {
    guard !q.isEmpty else { return nil }
    let flat = text.split(whereSeparator: { $0.isWhitespace }).joined(separator: " ")
    guard let found = flat.range(of: q, options: .caseInsensitive) else { return nil }

    let characters = Array(flat)
    let at = flat.distance(from: flat.startIndex, to: found.lowerBound)
    let matchLength = flat.distance(from: found.lowerBound, to: found.upperBound)
    let start = max(at - assistantMatchContext, 0)
    let end = min(at + matchLength + assistantMatchContext, characters.count)

    let middle = String(characters[start..<end]).trimmingCharacters(in: .whitespaces)
    return (start > 0 ? "\u{2026}" : "") + middle + (end < characters.count ? "\u{2026}" : "")
}
