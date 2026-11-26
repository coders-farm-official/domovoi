import Foundation

/// Settings → Privacy → Keep voice history (1.5.0): how long the house keeps
/// what people say to Domovoi and what it answered (`voice_history_keep`: 30
/// days, 90 days, 1 year or forever), read-only — `KeepVoiceHistoryPanel.kt`
/// on Android, word for word. An admin changes it on the dashboard (web
/// `people.jsx` `VoiceHistoryKeepPanel`); the phone has no admin session.
/// `GET /api/voice-history` is a device read, so a paired phone asks.
///
/// The dashboard link keeps the rule every signpost here keeps: a word every
/// dashboard version understands. `#settings/keep-voice-history` only exists
/// on a 1.5.0 dashboard, and `GET /api/voice-history` answers on older ones
/// too (it is from 2026-10), so the link is offered only when the server's
/// own version label (`GET /api/config/version`) is 1.5.0 or later.
/// Otherwise the value alone, with where an admin changes it.
public enum KeepVoiceHistory {

    public static let dashboard = "keep-voice-history"
    public static let valueLabel = "Keep voice history"
    public static let adminChangesIt = "An admin changes this on the dashboard."
    public static let unpaired = "Pair this phone with your home server to see how long it keeps what was said (Settings → Phones & browsers)."
    public static let olderServer = "This home server doesn’t say how long it keeps voice history."

    /// "Keep voice history: 90 days".
    public static func valueLine(_ label: String) -> String { "\(valueLabel): \(label)" }

    /// What the two reads found: the value in words (nil: none said) and
    /// whether the dashboard has the page.
    public struct Read: Equatable, Sendable {
        public let label: String?
        public let linkable: Bool

        public init(label: String?, linkable: Bool) {
            self.label = label
            self.linkable = linkable
        }
    }

    /// The value in words, from `GET /api/voice-history`: its `keep_label`
    /// ("90 days", "Forever"), else the label of its `keep` among `choices`,
    /// else the raw `keep`; nil when the body says none.
    public static func label(_ data: Data?) -> String? {
        guard let data, let object = try? JSONSerialization.jsonObject(with: data, options: [.fragmentsAllowed]),
              let o = object as? [String: Any] else { return nil }
        func str(_ v: Any?) -> String? {
            guard let s = (v as? String)?.trimmingCharacters(in: .whitespacesAndNewlines), !s.isEmpty else { return nil }
            return s
        }
        if let label = str(o["keep_label"]) { return label }
        guard let keep = str(o["keep"]) else { return nil }
        let choice = (o["choices"] as? [Any])?.lazy
            .compactMap { $0 as? [String: Any] }
            .first { str($0["value"]) == keep }
            .flatMap { str($0["label"]) }
        return choice ?? keep
    }

    /// Whether the server's version label ("1.5.0", "1.5.0 + 14 (abc1234)")
    /// is 1.5.0 or later: only then is there a Keep voice history page on its
    /// dashboard. Anything else, an unreadable label included, is no.
    public static func dashboardHasPage(_ versionLabel: String?) -> Bool {
        guard let versionLabel,
              let m = versionPattern.firstMatch(
                  in: versionLabel, range: NSRange(versionLabel.startIndex..., in: versionLabel)
              ),
              let majorRange = Range(m.range(at: 1), in: versionLabel),
              let minorRange = Range(m.range(at: 2), in: versionLabel),
              let major = Int(versionLabel[majorRange]), let minor = Int(versionLabel[minorRange])
        else { return false }
        return major > 1 || (major == 1 && minor >= 5)
    }

    /// `X.Y.Z` at the start of a label, ASCII digits, at most four per part
    /// (`KeepVoiceHistory.dashboardHasPage`'s regex on Android).
    nonisolated(unsafe) private static let versionPattern = try! NSRegularExpression(
        pattern: #"^\s*([0-9]{1,4})\.([0-9]{1,4})\.([0-9]{1,4})(?![0-9])"#
    )

    /// The two reads. A refusal, an older server or a body that is not the
    /// shape is a value of nil; no answer (no network, a 5xx, a 429) throws;
    /// a failed version read is "no page". Untracked: never raises pairing.
    public static func read(_ client: DomovoiClient) async throws -> Read {
        var body: Data?
        do throws(DomovoiError) {
            body = try await client.raw(method: "GET", path: "/api/voice-history", trackPairing: false)
        } catch {
            if let failure = error.failure, failure.status != 429, (400..<500).contains(failure.status) {
                body = nil
            } else {
                throw error
            }
        }
        var version: String?
        if let data = try? await client.raw(method: "GET", path: "/api/config/version", trackPairing: false),
           let o = (try? JSONSerialization.jsonObject(with: data)) as? [String: Any] {
            version = o["version"] as? String
        }
        return Read(label: label(body), linkable: dashboardHasPage(version))
    }
}
