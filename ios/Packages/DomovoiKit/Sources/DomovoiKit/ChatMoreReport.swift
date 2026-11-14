import Foundation

/// A report on one of Domovoi's replies — "report this reply".
///
/// `ReplyReport` (`assistant/ReplyReport.kt:27`) in the one shape this app
/// has a subject for: `draftServer` (`:171`), the house reply's. Google Play's
/// AI-generated content policy asks that a person can flag an offensive or
/// harmful answer without leaving the app, wherever the model ran.
///
/// It holds **exactly what the report sheet shows and nothing else**: the
/// question the reply answered, the reply, the model that wrote it and where
/// it ran, when, the app's version, and the person's own note. No audio (none
/// is ever kept), no device details, no other message of the conversation.
///
/// ### There is no server for this
///
/// There is no report route anywhere in `web/backend/api/` — the report is
/// handed on by the phone, which is how Play's policy is satisfied without a
/// server at all. So this is the one part of the item with no server
/// dependency, and the sinks below are the whole of its delivery.
///
/// **"Save on this phone" is left out**: Android keeps saved reports under
/// the *phone model's* settings and sets `canSave = false` for a house reply
/// anyway (`ChatScreen.kt:937`), so there is nothing here to save them to and
/// nothing is claimed.
public struct ChatMoreReport: Equatable, Sendable {

    /// When the report was made.
    public let created: Date
    /// The question the reply answered — the person's message before it; nil
    /// when the reply had none.
    public let question: String?
    /// …asked by voice, so its text is a transcript (`ReplyReport.kt:34`).
    public let spoken: Bool
    public let answer: String
    /// When the reply was written.
    public let answeredAt: Date
    /// The model's name as the reply names it.
    public let model: String
    /// The person's note; empty when they wrote none.
    public let note: String
    /// The app's version.
    public let appVersion: String

    /// A note is a few sentences, not a document (`ReplyReport.kt:123`).
    public static let noteMaxChars = 2_000

    /// The reply's first line, cut short (`ReplyReport.kt:63`).
    public static let titleChars = 60

    /// Where a report goes, the owner's one line (`ReplyReport.kt:334`,
    /// the owner's choice 2026-10-15): an email draft to the address the Play
    /// listing gives as its contact.
    public static let destination = "info@codersfarm.org"

    public init(
        created: Date,
        question: String?,
        spoken: Bool,
        answer: String,
        answeredAt: Date,
        model: String,
        note: String = "",
        appVersion: String
    ) {
        self.created = created
        self.question = question
        self.spoken = spoken
        self.answer = answer
        self.answeredAt = answeredAt
        self.model = model
        self.note = String(note.prefix(Self.noteMaxChars))
        self.appVersion = appVersion
    }

    /// The report for a house reply, or nil when that row is not a reply with
    /// words in it (`draftServer`, `ReplyReport.kt:171`).
    public static func draft(
        question: String?,
        spoken: Bool,
        answer: String?,
        answeredAt: Date,
        model: String?,
        appVersion: String,
        now: Date
    ) -> ChatMoreReport? {
        guard let answer, !answer.trimmingCharacters(in: .whitespacesAndNewlines).isEmpty else { return nil }
        return ChatMoreReport(
            created: now,
            question: question,
            spoken: spoken,
            answer: answer,
            answeredAt: answeredAt,
            model: model ?? "",
            appVersion: appVersion
        )
    }

    /// The same report with a note on it, which is what the sheet's field
    /// edits (`ReplyReportPanes.kt:72`).
    public func with(note: String) -> ChatMoreReport {
        ChatMoreReport(
            created: created,
            question: question,
            spoken: spoken,
            answer: answer,
            answeredAt: answeredAt,
            model: model,
            note: note,
            appVersion: appVersion
        )
    }

    /// "report this reply" is offered on every finished reply with words
    /// (`canReport`, `ChatScreen.kt:1347`).
    public static func canReport(role: String, pending: Bool, content: String) -> Bool {
        role != "user" && !pending && !content.trimmingCharacters(in: .whitespacesAndNewlines).isEmpty
    }

    /// The reply's first line, cut short — the sheet's own heading
    /// (`ReplyReport.kt:63`).
    public var title: String {
        let first = answer.trimmingCharacters(in: .whitespacesAndNewlines)
            .split(separator: "\n", omittingEmptySubsequences: false).first.map(String.init) ?? ""
        let cut = first.count > Self.titleChars
            ? String(first.prefix(Self.titleChars)).replacingOccurrences(
                of: "\\s+$", with: "", options: .regularExpression
              ) + "\u{2026}"
            : first
        return cut.isEmpty ? ChatMoreStrings.reportEmptyReply : cut
    }

    /// "llama3.2:3b, on the home server" (`modelLine`, `ReplyReport.kt:98`).
    ///
    /// A house reply has no catalogue id and no backend — those are the
    /// phone model's — so what is left is the model's name and the one clause
    /// that says where it ran.
    public var modelLine: String {
        let name = model.trimmingCharacters(in: .whitespacesAndNewlines)
        let parts = [name.isEmpty ? nil : name, ChatMoreStrings.reportOnTheHomeServer].compactMap { $0 }
        let line = parts.joined(separator: ", ")
        return line.isEmpty ? ChatMoreStrings.reportUnknownModel : line
    }

    /// ISO 8601, UTC, to the second — so a report reads the same wherever it
    /// is opened (`utc`, `ReplyReport.kt:126`).
    public static func utc(_ when: Date) -> String {
        let formatter = ISO8601DateFormatter()
        formatter.timeZone = TimeZone(secondsFromGMT: 0)
        formatter.formatOptions = [.withInternetDateTime]
        return formatter.string(from: when)
    }

    /// The report as plain text: what the share sheet and the email draft
    /// hand on, in the order the sheet shows it (`asText`,
    /// `ReplyReport.kt:73`).
    public func asText() -> String {
        var out = ChatMoreStrings.reportHeading + "\n"
        out += "\n"
        out += "Reported: \(Self.utc(created))\n"
        out += "Model: \(modelLine)\n"
        if !appVersion.trimmingCharacters(in: .whitespacesAndNewlines).isEmpty {
            out += "App: \(appVersion)\n"
        }
        out += "\n"
        out += (spoken ? "Question (spoken):" : "Question:") + "\n"
        let asked = question?.trimmingCharacters(in: .whitespacesAndNewlines) ?? ""
        out += (asked.isEmpty ? ChatMoreStrings.reportNoQuestion : asked) + "\n"
        out += "\n"
        out += "Reply (written \(Self.utc(answeredAt))):\n"
        let said = answer.trimmingCharacters(in: .whitespacesAndNewlines)
        out += (said.isEmpty ? ChatMoreStrings.reportEmptyReply : said) + "\n"
        let written = note.trimmingCharacters(in: .whitespacesAndNewlines)
        if !written.isEmpty {
            out += "\n"
            out += "Note:\n"
            out += written + "\n"
        }
        // Kotlin's `trimEnd() + "\n"`: one newline at the end, whatever the
        // last field was.
        while out.hasSuffix("\n") || out.hasSuffix(" ") { out.removeLast() }
        return out + "\n"
    }

    /// The `mailto:` the email sink opens (`EmailDraftSink`,
    /// `ReplyReport.kt:356`).
    ///
    /// Percent-encoded with the **query** allowed set minus `&`, `=`, `+` and
    /// `?`: a reply with an ampersand in it would otherwise become two query
    /// fields, and a `+` in a mail body is read as a space.
    public func mailtoURL(to address: String = ChatMoreReport.destination) -> URL? {
        var allowed = CharacterSet.urlQueryAllowed
        allowed.remove(charactersIn: "&=+?")
        guard let subject = ChatMoreStrings.reportSubject.addingPercentEncoding(withAllowedCharacters: allowed),
              let body = asText().addingPercentEncoding(withAllowedCharacters: allowed) else { return nil }
        return URL(string: "mailto:\(address)?subject=\(subject)&body=\(body)")
    }

    /// The fields the sheet shows, in order, each with its label — so the
    /// sheet is "exactly what would be sent" and a test can say so
    /// (`ReportPane`, `ReplyReportPanes.kt:87-92`).
    ///
    /// `written` is given as the UTC stamp alone; Android puts a relative time
    /// before it (`relTime(...)` at `:90`) and the view adds that here, since
    /// ``relTime(_:nowMs:)`` is the app's own formatter.
    public var fields: [(label: String, text: String)] {
        var out: [(label: String, text: String)] = []
        let asked = question?.trimmingCharacters(in: .whitespacesAndNewlines) ?? ""
        out.append((
            spoken ? ChatMoreStrings.reportYourQuestionSpoken : ChatMoreStrings.reportYourQuestion,
            asked.isEmpty ? ChatMoreStrings.reportNoQuestion : asked
        ))
        out.append((ChatMoreStrings.reportTheReply, answer))
        out.append((ChatMoreStrings.reportWritten, Self.utc(answeredAt)))
        if !appVersion.trimmingCharacters(in: .whitespacesAndNewlines).isEmpty {
            out.append((ChatMoreStrings.reportApp, appVersion))
        }
        return out
    }
}

/// Where a report can be handed on, and the sentence each one says **before**
/// it is tapped (`ReportSinks.available`, `ReplyReport.kt:394`).
///
/// Two of Android's three: the owner's destination is an email address
/// (`REPORT_DESTINATION`, `:334`), so there is no HTTPS sink to offer, and
/// `ReportSinks.valid` would refuse one that was not a valid address anyway.
public enum ChatMoreReportSink: Equatable, Sendable, CaseIterable {
    /// The email draft, addressed to the owner's destination.
    case email
    /// The share sheet, with the report as plain text.
    case share

    public var label: String {
        switch self {
        case .email: return ChatMoreStrings.reportEmailLabel
        case .share: return ChatMoreStrings.reportShareLabel
        }
    }

    public var explains: String {
        switch self {
        case .email: return ChatMoreStrings.reportEmailExplains(ChatMoreReport.destination)
        case .share: return ChatMoreStrings.reportShareExplains
        }
    }

    /// The owner's destination first when it is a valid address, then the
    /// share sheet (`ReplyReport.kt:398-410`).
    public static func available(destination: String = ChatMoreReport.destination) -> [ChatMoreReportSink] {
        validAddress(destination) ? [.email, .share] : [.share]
    }

    /// `ReportSinks.EMAIL` (`ReplyReport.kt:379`), the same shape of address.
    static func validAddress(_ address: String) -> Bool {
        let pattern = "^[A-Za-z0-9._%+-]+@[A-Za-z0-9-]+(\\.[A-Za-z0-9-]+)+$"
        let trimmed = address.trimmingCharacters(in: .whitespacesAndNewlines)
        return trimmed.range(of: pattern, options: .regularExpression) != nil
    }
}

/// What came of handing one on (`ReportOutcome`), and what is said about it
/// (`serverReportToast`, `ChatScreen.kt:1351`).
public enum ChatMoreReportOutcome: Equatable, Sendable {
    /// Handed to the share sheet or the mail app: **nothing is said**, as
    /// Android says nothing — the sheet that opened is the answer.
    case handedOn
    case noApp
    case failed

    public var toast: String? {
        switch self {
        case .handedOn: return nil
        case .noApp: return ChatMoreStrings.reportNoApp
        case .failed: return ChatMoreStrings.reportFailed
        }
    }
}
