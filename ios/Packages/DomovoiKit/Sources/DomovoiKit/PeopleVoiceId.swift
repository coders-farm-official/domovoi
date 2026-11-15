import Foundation

// MARK: - voice ID's state (GET /api/people/voice-id, V061)
//
// The port of Android 2095fd5f + 2766a325 (`PeopleModels.kt:174-281`), which
// is the web People page's `VoiceIdNotice` and `PersonVoiceRelearn`. 1.3.0
// changed the speaker model and voices the old one learned can't be used, so
// nobody is recognized until they say "my name is …" again. When voice ID
// can't run at all (its model isn't on the server) that is all the page says:
// asking anyone to introduce themselves then would be a control that can't do
// what it says ([[no-misleading-controls]]).

/// Server `VoiceIdStatus` (`schemas.py:376`; `PeopleModels.kt:186-190`).
///
/// ``ready`` is true when the answer leaves it out, so an answer missing it
/// never claims voice ID is off; ``problem`` is the server's own words.
public struct VoiceIdStatus: Equatable, Sendable {
    public let ready: Bool
    public let problem: String?
    public let relearn: Int

    public init(ready: Bool = true, problem: String? = nil, relearn: Int = 0) {
        self.ready = ready
        self.problem = problem
        self.relearn = relearn
    }
}

/// One read of the voice-id route (`VoiceIdRead`, `PeopleModels.kt:193-201`).
public enum VoiceIdRead: Equatable, Sendable {
    case known(VoiceIdStatus)
    /// The server has no such route: show nothing.
    case absent
    /// Anything else went wrong: keep what is shown.
    case failed
}

/// A notice's bold line and, when there is one, the line under it
/// (`VoiceNotice`, `PeopleModels.kt:238`).
///
/// ``isOff`` picks the icon and the wash: `mic.slash` on the warning wash
/// when voice ID can't run, `mic` otherwise. Android tells the two apart by
/// comparing the title (`PersonDetail.kt:378`); a flag says the same thing
/// without a string compare.
public struct VoiceNotice: Equatable, Sendable {
    public let title: String
    public let body: String?
    public let isOff: Bool

    public init(title: String, body: String?, isOff: Bool = false) {
        self.title = title
        self.body = body
        self.isOff = isOff
    }
}

/// The voice ID rules the People screens draw by.
public enum PeopleVoiceId {

    /// A voice-id answer, or nil when it isn't one (`parseVoiceIdStatus`,
    /// `PeopleModels.kt:204-207`).
    ///
    /// Not an object is nil. Inside one, a missing or `null` field takes its
    /// default (`ready` true, `problem` none, `relearn` 0) and unknown keys are
    /// ignored. A field of the wrong type is nil, the whole answer: Android's
    /// decoder fails it, and its read is then `Failed`, which keeps what is
    /// shown. `ready` is checked with `CFBooleanGetTypeID`, so a `1` is not a
    /// `true` (see ``prettyPersonPref(_:)`` for that trap).
    public static func read(_ data: Data) -> VoiceIdStatus? {
        guard
            let any = try? JSONSerialization.jsonObject(with: data, options: [.fragmentsAllowed]),
            let object = any as? [String: Any]
        else { return nil }

        var ready = true
        if let value = object["ready"], !(value is NSNull) {
            guard let number = value as? NSNumber, CFGetTypeID(number) == CFBooleanGetTypeID() else {
                return nil
            }
            ready = number.boolValue
        }

        var problem: String?
        if let value = object["problem"], !(value is NSNull) {
            guard let text = value as? String else { return nil }
            problem = text
        }

        var relearn = 0
        if let value = object["relearn"], !(value is NSNull) {
            guard
                let number = value as? NSNumber,
                CFGetTypeID(number) != CFBooleanGetTypeID(),
                let whole = Int(exactly: number.doubleValue)
            else { return nil }
            relearn = whole
        }
        return VoiceIdStatus(ready: ready, problem: problem, relearn: relearn)
    }

    /// Reads voice ID's state with `get` (`loadVoiceId`, `PeopleModels.kt:216-226`).
    ///
    /// A server from before V061 has no such route: FastAPI answers 404, or
    /// 422 when `/api/people/{person_id}` takes `voice-id` for a person id
    /// that isn't a number. Either is ``VoiceIdRead/absent``: nothing new is
    /// shown. Any other failure, or an answer that isn't a status, is
    /// ``VoiceIdRead/failed``: the page keeps what it showed and raises no
    /// toast.
    public static func load(_ get: @Sendable () async throws -> Data) async -> VoiceIdRead {
        do {
            let data: Data = try await get()
            guard let status = read(data) else { return .failed }
            return .known(status)
        } catch let error as DomovoiError {
            let status: Int? = error.failure?.status
            return status == 404 || status == 422 ? .absent : .failed
        } catch {
            return .failed
        }
    }

    /// What to show after `read`, given what was `shown` before it
    /// (`nextVoiceId`, `PeopleModels.kt:228-232`).
    public static func next(shown: VoiceIdStatus?, read: VoiceIdRead) -> VoiceIdStatus? {
        switch read {
        case .known(let status): return status
        case .absent: return nil
        case .failed: return shown
        }
    }

    /// Voice ID is known not to run on this server. Unknown is not off
    /// (`voiceIdOff`, `PeopleModels.kt:235`).
    public static func isOff(_ voiceId: VoiceIdStatus?) -> Bool {
        guard let voiceId else { return false }
        return !voiceId.ready
    }

    /// The household notice at the top of the People list (web
    /// `VoiceIdNotice`; `voiceIdNotice`, `PeopleModels.kt:247-260`), or nil
    /// when there is nothing to say.
    ///
    /// Off wins over relearn: while voice ID can't run nobody is asked to
    /// introduce themselves. Off's body is the server's problem as it is, or
    /// none when that is blank.
    public static func notice(_ voiceId: VoiceIdStatus?) -> VoiceNotice? {
        guard let voiceId else { return nil }
        if !voiceId.ready {
            let problem: String? = voiceId.problem
            let said: String? = (problem ?? "").trimmingCharacters(in: .whitespacesAndNewlines).isEmpty
                ? nil : problem
            return VoiceNotice(title: PeopleStrings.voiceIdOffTitle, body: said, isOff: true)
        }
        guard voiceId.relearn > 0 else { return nil }
        return VoiceNotice(
            title: PeopleStrings.voiceRelearnTitle,
            body: PeopleStrings.voiceRelearnBody(people: voiceId.relearn)
        )
    }

    /// The profile's notice for someone who has to introduce themselves
    /// again (web `PersonVoiceRelearn`; `personVoiceRelearnNotice`,
    /// `PeopleModels.kt:264-274`), or nil. While voice ID's state is unknown
    /// (an older server, or not read yet) the person's own flag decides.
    public static func personNotice(_ person: Person, voiceId: VoiceIdStatus?) -> VoiceNotice? {
        guard person.voiceRelearn, !isOff(voiceId) else { return nil }
        return VoiceNotice(
            title: PeopleStrings.personRelearnTitle,
            body: PeopleStrings.personRelearnBody(name: person.name.trimmingCharacters(in: .whitespacesAndNewlines))
        )
    }

    /// The roster row's idle dot (``PeopleStrings/relearnDotDescription``;
    /// `showRelearnDot`, `PeopleModels.kt:277-278`): not while voice ID is
    /// off, for the same reason as the notices.
    public static func showsRelearnDot(_ person: Person, voiceId: VoiceIdStatus?) -> Bool {
        person.voiceRelearn && !isOff(voiceId)
    }
}

/// A voice-id state and the server it was read from, captured before the
/// request. ``PeopleModel/voiceId`` hands the state out only while that is
/// still the active server ([[read-after-await-wrong-key]]).
struct PeopleVoiceIdAnswer: Equatable, Sendable {
    let server: String
    let status: VoiceIdStatus
}
