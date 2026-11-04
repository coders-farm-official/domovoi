import Foundation

/// What a spoken message's marker says, and how strongly
/// (`SpeakerMarker.kt:32`).
public struct ChatMoreSpeakerMark: Equatable, Sendable {
    /// Read under the message: "Ana", "maybe Ana", "voice not recognized".
    public let label: String
    /// The person's name for the avatar; nil when nobody was recognized.
    public let name: String?
    /// Drawn softer: a weak match, someone other than the owner, or nobody.
    public let soft: Bool
    /// What a screen reader says.
    public let description: String

    public init(label: String, name: String?, soft: Bool, description: String) {
        self.label = label
        self.name = name
        self.soft = soft
        self.description = description
    }
}

/// Who Domovoi thinks said a voice message (owner decisions 2026-10-08).
///
/// `speakerMark` (`SpeakerMarker.kt:47`), ported whole — including the branch
/// this app cannot reach.
public enum ChatMoreSpeaker {

    /// The marker for a row, or nil when it needs none: anything typed, or
    /// Domovoi's own reply.
    ///
    /// ### `ownerID` is nil in this app, everywhere
    ///
    /// `DeviceSelf.ownerPersonID` is hard-coded nil
    /// (`ios/Domovoi/DomovoiApp.swift:142`, "no preference holds one yet"),
    /// ``DeviceRegistry/DeviceRow`` does not decode `owner_person_id` and
    /// `deviceSelf` is `ShellModel`'s private closure. With a null owner
    /// Android takes **exactly** this function's `notOwner == false` branch
    /// (`SpeakerMarker.kt:60`), so passing nil is faithful rather than
    /// reduced, and the softening becomes live for free the day some batch
    /// fills the preference in. The parameter is kept for that day rather
    /// than deleted, which is the one case on this track where a seam with no
    /// caller is right: it is a **ported** branch, and dropping it would make
    /// the port silently narrower than the original.
    public static func mark(
        source: String?,
        role: String,
        personID: Int64?,
        name: String?,
        confident: Bool,
        ownerID: String? = nil
    ) -> ChatMoreSpeakerMark? {
        guard source == "voice", role == "user" else { return nil }
        guard let personID else {
            return ChatMoreSpeakerMark(
                label: ChatMoreStrings.voiceNotRecognized,
                name: nil,
                soft: true,
                description: ChatMoreStrings.spokenNotRecognized
            )
        }
        let trimmed = name?.trimmingCharacters(in: .whitespacesAndNewlines) ?? ""
        let who = trimmed.isEmpty ? ChatMoreStrings.someoneItKnows : trimmed
        let notOwner = ownerID != nil && ownerID != String(personID)
        if !confident {
            return ChatMoreSpeakerMark(
                label: ChatMoreStrings.maybe(who),
                name: who,
                soft: true,
                description: ChatMoreStrings.spokenMaybeBy(who)
            )
        }
        if notOwner {
            return ChatMoreSpeakerMark(
                label: who,
                name: who,
                soft: true,
                description: ChatMoreStrings.spokenByNotOwner(who)
            )
        }
        return ChatMoreSpeakerMark(
            label: who,
            name: who,
            soft: false,
            description: ChatMoreStrings.spokenBy(who)
        )
    }

    /// The marker for a row of the parallel read, which is where the four
    /// fields survive (``ChatMoreRowExtras``) — `ChatMessage.live(...)` drops
    /// three of them (`ChatTranscript.swift:152-175`).
    ///
    /// A **pending** row gets none, as `ChatScreen.kt:1521` requires: the
    /// two rows a turn writes are drawn before the server has said who spoke,
    /// and "voice not recognized" under a row still being transcribed names a
    /// failure that has not happened.
    public static func mark(for extras: ChatMoreRowExtras, ownerID: String? = nil) -> ChatMoreSpeakerMark? {
        mark(
            source: extras.source,
            role: extras.role,
            personID: extras.speakerPersonID,
            name: extras.speakerName,
            confident: extras.speakerConfident,
            ownerID: ownerID
        )
    }
}
