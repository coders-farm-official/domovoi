import Foundation
import Observation

/// What a group write came to: the sentence to say, and whether it worked.
public struct GroupSaid: Equatable, Sendable {
    public let text: String
    public let ok: Bool

    public init(text: String, ok: Bool) {
        self.text = text
        self.ok = ok
    }
}

/// The household's groups (V060): the read behind a room card's share menu
/// on Home and the Groups card in Settings → Rooms, and the writes both make.
///
/// **Household only.** ``start(paired:)`` asks nothing on an unpaired phone
/// (a guest); a 404 (a server older than groups) and a 401/403 (not a
/// household device) are answers that hide the controls, never errors
/// (``groupsAccess(paired:read:server:refused:)``).
///
/// **Every answer is keyed by the server it was for.** The read carries its
/// server (``MusicGroupsRead``) and is used only while that is still the
/// server on screen; a write's sentence is returned only while the app still
/// talks to the server the write went to, and nil otherwise — so house A's
/// "play in garage too · done" never lands on house B
/// ([[read-after-await-wrong-key]]).
@MainActor
@Observable
public final class MusicGroupsModel {

    /// The list, with the server it was asked of.
    public let read: ApiRead<MusicGroupsRead>

    /// A Settings write (make, save, delete) is in flight: one at a time.
    public private(set) var busy = false

    /// The server that refused a write (401/403) since this model was built:
    /// this phone is not a household device there, and the controls go.
    private var refusedOn: String?

    private let container: AppContainer
    private let api: MusicGroupsApi
    private var eventSink: Int?

    /// The web re-reads its groups on this push (`home.jsx` ~:1206): a shared
    /// queue starting or ending changes what plays.
    public static let events: Set<String> = ["music.now_playing.changed"]

    public init(container: AppContainer) {
        self.container = container
        let api = MusicGroupsApi(client: container.client)
        self.api = api
        let store = container.store
        // The server is read BEFORE the request, and travels with its answer.
        read = ApiRead(in: container) {
            let server = store.serverURL
            let answer = try await api.read()
            return MusicGroupsRead(server: server, answer: answer)
        }
    }

    /// The server on screen now.
    public var server: String { container.store.serverURL }

    /// Whether the household's controls are drawn, for a phone that is (or
    /// is not) paired.
    public func access(paired: Bool) -> GroupsAccess {
        groupsAccess(paired: paired, read: read.data, server: server, refused: refusedOn == server)
    }

    /// The named groups and the shared queues, for this server, or nil.
    public var list: MusicGroupsList? { groupsList(read.data, server: server) }

    /// The named groups, for this server.
    public var groups: [MusicGroup] { list?.groups ?? [] }

    // MARK: - lifecycle

    /// On screen. **A guest asks nothing**: the read is a household read.
    public func start(paired: Bool) {
        guard paired else { return }
        if eventSink == nil {
            eventSink = container.onStateEvent { [weak self] event in
                Task { @MainActor in self?.received(event) }
            }
        }
        read.refresh()
    }

    public func stop() {
        if let eventSink {
            container.removeStateEventSink(eventSink)
            self.eventSink = nil
        }
        read.stop()
    }

    public func refresh() { read.refresh() }

    /// A push arrived: re-read on the web's one.
    public func received(_ event: StateEvent) {
        guard Self.events.contains(event.type), eventSink != nil else { return }
        read.refresh()
    }

    // MARK: - a room card's choice

    /// Share, join or leave, from a room card's menu: the sentence to say
    /// ("play in garage too · done", "kitchen left the group", "share failed:
    /// nothing is playing in kitchen"), or nil when the app moved to another
    /// server while it was out.
    public func perform(_ choice: GroupChoice) async -> GroupSaid? {
        let server = self.server
        let api = self.api
        do throws(DomovoiError) {
            try await api.perform(choice)
            guard container.store.serverURL == server else { return nil }
            read.refresh()
            return GroupSaid(text: choice.doneText, ok: true)
        } catch let trouble {
            guard container.store.serverURL == server else { return nil }
            noteRefusal(trouble, on: server)
            read.refresh()
            return GroupSaid(text: groupFailureText(choice.failVerb, trouble), ok: false)
        }
    }

    // MARK: - Settings → Rooms

    /// Make a group (`editing` nil) or save one: the name trimmed, two or
    /// more rooms. A 400 is the server's sentence, as it is.
    public func save(editing: Int64?, name: String, rooms: [String]) async -> GroupSaid? {
        let clean = name.trimmingCharacters(in: .whitespacesAndNewlines)
        guard !busy, groupFormReady(name: clean, rooms: Set(rooms), busy: false) else { return nil }
        let server = self.server
        let api = self.api
        let picked = Array(Set(rooms)).sorted()
        busy = true
        defer { busy = false }
        do throws(DomovoiError) {
            if let editing {
                try await api.update(editing, name: clean, rooms: picked)
            } else {
                try await api.create(name: clean, rooms: picked)
            }
            guard container.store.serverURL == server else { return nil }
            read.refresh()
            return GroupSaid(text: editing == nil ? GroupStrings.made(clean) : GroupStrings.saved(clean), ok: true)
        } catch let trouble {
            guard container.store.serverURL == server else { return nil }
            noteRefusal(trouble, on: server)
            read.refresh()
            return GroupSaid(text: groupSaveFailureText(trouble), ok: false)
        }
    }

    /// Delete a group. Music playing in its rooms plays on.
    public func delete(_ group: MusicGroup) async -> GroupSaid? {
        guard !busy else { return nil }
        let server = self.server
        let api = self.api
        busy = true
        defer { busy = false }
        do throws(DomovoiError) {
            try await api.delete(group.id)
            guard container.store.serverURL == server else { return nil }
            read.refresh()
            return GroupSaid(text: GroupStrings.deleted(group.name), ok: true)
        } catch let trouble {
            guard container.store.serverURL == server else { return nil }
            noteRefusal(trouble, on: server)
            read.refresh()
            return GroupSaid(text: groupFailureText(GroupStrings.deleteVerb, trouble), ok: false)
        }
    }

    private func noteRefusal(_ trouble: DomovoiError, on server: String) {
        if groupsRefused(trouble) { refusedOn = server }
    }
}
