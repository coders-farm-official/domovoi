import Foundation

/// The routes the rest of Chat asks for, and the bodies it sends.
///
/// An `extension` on ``ChatPath`` rather than a type of its own, because
/// ``ChatPath`` is a public `enum` with no cases and these are the same
/// screen's routes — the ones its doc comment (`ChatModels.swift:449`) lists
/// as "the routes this port does **not** ask for". That comment is now out of
/// date by this file, and `ChatModels.swift` is one this item may not edit
/// (NIGHT-RULES §2), so the correction is written here instead: every route it
/// names except `/lookup` (the phone chat's own, `chat.py:2046`) is asked for
/// below.
///
/// Read against `web/backend/api/chat.py`, `chat_documents.py`,
/// `chat_drafts.py` and `chat_doc_jobs.py` on 2026-11-02. The three document
/// modules are all mounted under `/api/chat` (`chat.py:2826`).
extension ChatPath {

    // MARK: - documents (`chat_documents.py`)

    /// Every document of a thread (`chat_documents.py:386`).
    public static func documents(thread id: Int64) -> String {
        "\(base)/threads/\(id)/documents"
    }

    /// A document's own file: a PDF inline, anything else as a download
    /// (`chat_documents.py:343`). What the page sheet renders from.
    public static func documentFile(_ token: String) -> String {
        "\(base)/documents/\(token)/file"
    }

    /// A document's text, part by part (`chat_documents.py:333`).
    public static func documentParts(_ token: String) -> String {
        "\(base)/documents/\(token)/parts"
    }

    /// A picture attached to a message (`chat.py:2784`), READ tier.
    ///
    /// **Fetched with the device token in a header**, never with the
    /// `?device_token=` query the route also takes: see
    /// ``ChatMorePictureStore``.
    public static func upload(token: String) -> String {
        "\(base)/uploads/\(token)"
    }

    // MARK: - long-document jobs (`chat_doc_jobs.py`)

    public static func docJob(_ id: String) -> String { "\(base)/doc-jobs/\(id)" }

    /// A job's progress from now on (`chat_doc_jobs.py:1042`), SSE on a
    /// **GET** — which is why ``ServerEvents``' `application/json` is never
    /// set for it (`ServerEvents.swift:146-149`).
    public static func docJobEvents(_ id: String) -> String { "\(base)/doc-jobs/\(id)/events" }

    public static func docJobStop(_ id: String) -> String { "\(base)/doc-jobs/\(id)/stop" }
    public static func docJobResume(_ id: String) -> String { "\(base)/doc-jobs/\(id)/resume" }

    // MARK: - drafts (`chat_drafts.py`)

    public static func draft(_ id: Int64) -> String { "\(base)/drafts/\(id)" }

    public static func threadDrafts(thread id: Int64) -> String { "\(base)/threads/\(id)/drafts" }

    /// A hand edit, as the next version (`chat_drafts.py:488`). **409** when a
    /// newer one landed meanwhile.
    public static func draftVersions(_ id: Int64) -> String { "\(base)/drafts/\(id)/versions" }

    /// Into Documents → From Chat, with the server's own check
    /// (`chat_drafts.py:585`).
    public static func draftSave(_ id: Int64) -> String { "\(base)/drafts/\(id)/save" }

    /// The file itself (`chat_drafts.py:613`).
    public static func draftExport(_ id: Int64, version: Int, format: String) -> String {
        "\(base)/drafts/\(id)/export?version=\(version)&format=\(format)"
    }

    // MARK: - the cards, and the chip

    /// An answer to a mid-answer lookup card (`chat.py:2086`), SEND tier.
    /// Streams the reply exactly as a send does.
    public static func lookups(thread: Int64, message: Int64) -> String {
        "\(base)/threads/\(thread)/lookups/\(message)"
    }

    /// A pick on a heads-up card (`chat.py:1127`), SEND tier, also a stream.
    public static func headsUp(thread id: Int64) -> String {
        "\(base)/threads/\(id)/heads-up"
    }

    /// One push-to-talk turn (`chat.py:2429`), DEVICE tier.
    ///
    /// **Not a stream**: the route answers with the core's whole JSON
    /// (`chat.py:2495`), which is why a spoken turn needs no change to the
    /// send body — the core writes both rows itself.
    public static func voice(thread id: Int64) -> String {
        "\(base)/threads/\(id)/voice"
    }

    /// What the chip reads (`chat.py:2031`).
    public static let internet = "\(base)/internet"

    // MARK: - the bodies

    /// `PATCH /threads/{id}` with the one field the chip writes
    /// (`ThreadPatch`, `chat.py:181`).
    public static func internetBody(allowed: Bool) -> Data {
        moreJSON(["internet_allowed": allowed])
    }

    /// `{"decision": "once"|"chat"|"no", "device_id"?}` (`chat.py:2024`).
    public static func lookupBody(decision: String, deviceID: String?) -> Data {
        var object: [String: Any] = ["decision": decision]
        if let deviceID, !deviceID.isEmpty { object["device_id"] = deviceID }
        return moreJSON(object)
    }

    /// `{message_id, option, heads_up: {off, skip}}` (`chat.py:1127`;
    /// `headsUpBody`, `ChatDocsApi.kt:105`).
    ///
    /// `skip` is sorted, as the Kotlin sorts it (`ChatDocsApi.kt:107`): the
    /// list is a set on both sides and a stable order is what makes a body
    /// comparable in a test.
    public static func headsUpBody(
        messageID: Int64,
        option: String,
        prefs: ChatMoreHeadsUpPrefs
    ) -> Data {
        moreJSON([
            "message_id": messageID,
            "option": option,
            "heads_up": ["off": prefs.off, "skip": prefs.skip.sorted()],
        ])
    }

    /// `{text, base_version, device_id?}` (`EditBody`, `chat_drafts.py:471`).
    public static func draftEditBody(text: String, baseVersion: Int, deviceID: String?) -> Data {
        var object: [String: Any] = ["text": text, "base_version": baseVersion]
        if let deviceID, !deviceID.isEmpty { object["device_id"] = deviceID }
        return moreJSON(object)
    }

    /// `{version?, format?, template?, device_id?}` (`SaveBody`,
    /// `chat_drafts.py:544`).
    public static func draftSaveBody(
        version: Int?,
        format: String?,
        template: String? = nil,
        deviceID: String?
    ) -> Data {
        var object: [String: Any] = [:]
        if let version { object["version"] = version }
        if let format, !format.isEmpty { object["format"] = format }
        if let template, !template.isEmpty { object["template"] = template }
        if let deviceID, !deviceID.isEmpty { object["device_id"] = deviceID }
        return moreJSON(object)
    }

    /// ``ChatPath``'s own `json(_:)` is private to its file, so this is the
    /// same two lines under a name that cannot collide with it.
    static func moreJSON(_ object: [String: Any]) -> Data {
        (try? JSONSerialization.data(withJSONObject: object)) ?? Data("{}".utf8)
    }
}
