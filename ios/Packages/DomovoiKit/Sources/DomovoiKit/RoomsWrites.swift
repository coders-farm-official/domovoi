import Foundation

// ---------------------------------------------------------------------------
// The Rooms screen's paths and its seven writes — the ports of
// `SatellitesScreen.kt:310-340`, `SatOverviewTab.kt:256-508`,
// `SatHistoryTabs.kt:294-317, 437-449`, `SatQuietHours.kt:96-124` and
// `SatModels.kt:330`.
//
// **Every one is device tier**, and a refusal raises the pairing screen the
// ordinary way: ``DomovoiClient/raw(method:path:…)`` sets `pairing` on a
// device-token refusal and the shell watches it, so nothing here inspects or
// re-raises pairing itself. Going around that is how a screen ends up with its
// own idea of whether this phone is paired.
//
// The writes Android has and this port does not — `restart`, `upgrade`,
// `save & restart`, `dropin/start`, the display controls — are listed with
// their gates on ``RoomsModel``. They are not here, and no half of them is.
//
// **The read-after-await rule holds in every one**: the room id, the body and
// the house epoch are captured *before* the `await` and the epoch is checked
// after it. The defect class this guards against is the one that cost this
// track most — an answer for server A, or for room A, landing under B — and
// the test for it switches inside the round trip.
// ---------------------------------------------------------------------------

/// Every path this screen reads or writes, with the tier that decides whether
/// the phone may use it at all.
///
/// A room id is a person's words (`front room`, `Mum's office`) and goes in a
/// **path segment**, so each one is escaped through
/// ``encodedRoomPathSegment(_:)`` — the same function Home's transport uses,
/// and for its reason: Java's form encoder writes a space as `+`, which in a
/// path segment addresses a room that does not exist.
public enum RoomsApi {

    /// `GET` — device read (`satellites.py:133-136`).
    public static let roster = "/api/satellites"

    /// `GET` — device read, the core's own SHA (`config.py:258`).
    public static let version = "/api/config/version"

    /// `GET` — open (``Capabilities/fetch(using:)``).
    public static let capabilities = "/api/capabilities"

    /// `POST {"message": …}` — device (`satellites.py:1477-1480`).
    public static let announceAll = "/api/satellites/announce-all"

    /// `POST {"room_id","url","title"}` — the outbound-fetch tier: an admin
    /// session **or** a URL a registered fulfiller's matcher allows,
    /// rate-limited (`music.py:1053`, gate at `domovoi/main.py:5782-5788`).
    public static let addByURL = "/api/music/add-by-url"

    static func room(_ id: String) -> String {
        "/api/satellites/\(encodedRoomPathSegment(id))"
    }

    /// `GET` — device read (`satellites.py:1659-1667`).
    public static func health(_ id: String) -> String { room(id) + "/health" }

    /// `GET` — device read (`:1639-1647`). **The PATCH is admin** (`:1712`),
    /// which is why this screen only reads it.
    public static func config(_ id: String) -> String { room(id) + "/config" }

    /// `GET` device read, `PUT` device (`:1406`, `:1425-1432`).
    public static func quietHours(_ id: String) -> String { room(id) + "/quiet-hours" }

    /// `GET` — device read (`:876`). The limit is Android's
    /// (`SatHistoryTabs.kt:64`).
    public static func sessions(_ id: String) -> String { room(id) + "/sessions?limit=100" }

    /// `GET` — device read (`:916-922`), limit 300 (`SatHistoryTabs.kt:67`).
    public static func conversations(_ id: String) -> String { room(id) + "/conversations?limit=300" }

    /// `GET` — device read (`:958-962`).
    public static func notes(_ id: String) -> String { room(id) + "/notes" }

    /// `GET` — device read (`:984-990`), limit 100 (`SatHistoryTabs.kt:284`).
    public static func recentlyPlayed(_ id: String) -> String { room(id) + "/recently-played?limit=100" }

    /// `GET` — **open**, with rule M1 masking a reminder's words for a caller
    /// with no household token (`:1063`, `_apply_m1` at `:1090`).
    public static func timers(_ id: String) -> String { room(id) + "/timers" }

    /// `DELETE` — device, 204 (`:1093-1097`).
    public static func timer(_ id: String, _ timerID: Int64) -> String {
        room(id) + "/timers/\(timerID)"
    }

    /// `PUT {"own_only": …}` — device (`:1319-1330`).
    public static func timerScope(_ id: String) -> String { room(id) + "/timer-announcements" }

    /// `POST {"message": …}` — device (`:1461-1466`; the core's
    /// `/v1/admin/announce` is device tier, `main.py:2602-2604`).
    public static func announce(_ id: String) -> String { room(id) + "/announce" }

    /// `POST {"level": …}` — device (`:1557-1561`).
    public static func volume(_ id: String) -> String { room(id) + "/volume" }

    /// `POST` — no web gate; the core's `/v1/admin/dropin/end` is **device**
    /// tier (`:1507`; `main.py:2718-2721`). Ending a call somebody else began
    /// is the one thing a phone needs from the admin pair.
    public static func dropInEnd(_ id: String) -> String { room(id) + "/dropin/end" }

    /// `GET` — device tier (`:1520-1530`): where the core's drop-in socket
    /// listens, so this phone can open one itself (D54,
    /// ``DropInCallModel``). "Requires the household device token …; the
    /// phone presents the same token again on the WebSocket upgrade"
    /// (`:1537-1538`).
    public static func dropInPhoneInfo(_ id: String) -> String {
        room(id) + "/dropin/phone-info"
    }

    /// A JSON body, built **before** the await so that what is sent cannot
    /// change under the request.
    static func json(_ object: [String: Any]) -> Data {
        (try? JSONSerialization.data(withJSONObject: object)) ?? DomovoiClient.emptyObject
    }
}

/// ``failureText(_:_:)`` for an error a `Task` closure has erased to
/// `any Error`.
///
/// A `Task { }` body is not typed-throws, so its `catch` binds `any Error`
/// even though every call inside these ones throws ``DomovoiError`` and
/// nothing else. ``ChatModel/write(verb:method:path:body:said:)`` spells that
/// out as two catch clauses at each site; this says it once, and keeps Chat's
/// fallback — an error nobody expected is reported as the transport's, with
/// its own text kept rather than swallowed.
func roomsFailure(_ action: String, _ error: any Error) -> String {
    guard let trouble = error as? DomovoiError else {
        return failureText(action, .unreachable("\(error)"))
    }
    return failureText(action, trouble)
}

extension RoomsModel {

    // MARK: - broadcast to every room

    /// Say something in every connected room (`BroadcastComposer.send`,
    /// `SatellitesScreen.kt:310`).
    ///
    /// **Reuses Home's write and both its rules** —
    /// ``HomeApi/announceAll(_:message:)``,
    /// ``announceAttempt(message:onlineCount:sending:)`` and
    /// ``announceToast(delivered:onlineCount:)`` — rather than porting the
    /// Kotlin twice. The three branches (nothing typed, nothing online, a send
    /// already in flight) and the three outcomes (nothing took it, a partial
    /// delivery, everything) are one implementation for both screens, which is
    /// how the phone's two broadcast buttons come to say the same words.
    public func broadcast() {
        let onlineCount = self.onlineCount
        switch announceAttempt(message: broadcastDraft, onlineCount: onlineCount, sending: broadcasting) {
        case .nothing:
            return
        case .toast(let said):
            say(said)
        case .send(let message):
            broadcasting = true
            let client = container.client
            let epoch = houseEpoch
            Task { @MainActor [weak self] in
                do {
                    let delivered = try await HomeApi.announceAll(client, message: message)
                    guard let self, self.stillHome(epoch) else { return }
                    self.broadcasting = false
                    self.say(announceToast(delivered: delivered, onlineCount: onlineCount))
                    self.broadcastDraft = ""
                } catch {
                    guard let self, self.stillHome(epoch) else { return }
                    self.broadcasting = false
                    self.say(roomsFailure("broadcast", error))
                }
            }
        }
    }

    // MARK: - announce to one room

    /// Speak in one room (`AnnounceSection.send`, `SatOverviewTab.kt:342`).
    ///
    /// The toast is decided by **`announced_to`, not by the status code**: a
    /// 200 with an empty list means the socket under the server's
    /// active-sessions map is dead, and
    /// ``RoomsStrings/acceptedButDidNotPlay(_:)`` is the sentence that says so
    /// (`SatOverviewTab.kt:360-366`). The branch order is Android's — an empty
    /// message is answered before an offline room.
    public func announce(to room: RoomsSatellite) {
        let reads = reads(for: room.roomID)
        let message = reads.announceDraft.trimmingCharacters(in: .whitespacesAndNewlines)
        if message.isEmpty { say(RoomsStrings.typeAMessageFirst); return }
        if !room.online { say(RoomsStrings.roomIsOffline(room.roomID)); return }
        guard reads.busy == nil else { return }
        reads.busy = "announce"
        let client = container.client
        // **Before the await**, all of it: the room this announcement is for,
        // the path, the body, and the house it is all in.
        let roomID = room.roomID
        let path = RoomsApi.announce(roomID)
        let body = RoomsApi.json(["message": message])
        let epoch = houseEpoch
        Task { @MainActor [weak self] in
            do {
                let answer = try await client.post(path, body: body, as: HomeAnnounceResult.self)
                // `roomID` and never `self.openRoomID`, which a tap during the
                // round trip may have moved: this answer is about the room the
                // message was sent to, whatever is on screen by now.
                guard let self, self.stillHome(epoch) else { return }
                let reads = self.reads(for: roomID)
                reads.busy = nil
                self.say(
                    answer.announcedTo.contains(roomID)
                        ? RoomsStrings.announcedTo(roomID)
                        : RoomsStrings.acceptedButDidNotPlay(roomID)
                )
                reads.announceDraft = ""
            } catch {
                guard let self, self.stillHome(epoch) else { return }
                self.reads(for: roomID).busy = nil
                self.say(roomsFailure("announce", error))
            }
        }
    }

    // MARK: - volume

    /// A drag moved the slider: show it now, send it **once** when it settles
    /// (`VolumeSection.slide`, `SatOverviewTab.kt:270`).
    ///
    /// The debounce is the point. A drag across the slider is dozens of
    /// values, and a POST per value would both flood the room and arrive out
    /// of order — the level that stuck would be whichever request the server
    /// happened to handle last.
    public func volumeDragged(_ room: RoomsSatellite, to level: Int) {
        let reads = reads(for: room.roomID)
        reads.volumeDraft = min(max(level, 0), 100)
        reads.volumeDirty = true
        volumeDebounce?.cancel()
        let roomID = room.roomID
        let wait = self.wait
        let settle = Self.volumeDebounceMs
        volumeDebounce = Task { @MainActor [weak self] in
            do { try await wait(settle) } catch { return }
            guard let self else { return }
            // The level that is sent is the one on the slider when the drag
            // settled, read back after the wait — not the one this call
            // carried, which is a value from somewhere in the middle of the
            // gesture.
            self.commitVolume(roomID, level: self.reads(for: roomID).volumeDraft ?? level)
        }
    }

    /// A nudge of ±5, which commits **at once**: it is one discrete value, not
    /// a gesture (`VolumeSection.nudge`, `SatOverviewTab.kt:283`).
    public func volumeNudged(_ room: RoomsSatellite, by delta: Int) {
        let reads = reads(for: room.roomID)
        let level = roomsNudge(reads.volumeLevel(room).level, by: delta)
        reads.volumeDraft = level
        reads.volumeDirty = true
        volumeDebounce?.cancel()
        volumeDebounce = nil
        commitVolume(room.roomID, level: level)
    }

    /// `POST …/volume {"level": n}` (`SatOverviewTab.kt:257`).
    func commitVolume(_ roomID: String, level: Int) {
        let client = container.client
        let path = RoomsApi.volume(roomID)
        let body = RoomsApi.json(["level": level])
        let epoch = houseEpoch
        Task { @MainActor [weak self] in
            var said: String
            do {
                try await client.send(method: "POST", path: path, body: body)
                said = RoomsStrings.volumeSet(room: roomID, level: level)
            } catch {
                said = roomsFailure("volume", error)
            }
            guard let self, self.stillHome(epoch) else { return }
            self.say(said)
            // Dirty is cleared whichever way it went, as Android's
            // `dirty = false` after the call is: a drag that failed must still
            // hand the slider back to the server's reading, or it would sit on
            // a level the room never took.
            self.reads(for: roomID).volumeDirty = false
        }
    }

    // MARK: - "Only reminders for this device"

    /// Flip the switch optimistically, `PUT …/timer-announcements`, and roll
    /// back if it fails (`TimerScopeSection.set`, `SatOverviewTab.kt:416`).
    public func setTimerScope(_ room: RoomsSatellite, on next: Bool) {
        let reads = reads(for: room.roomID)
        guard reads.busy == nil else { return }
        let previous = reads.timerScopeOn(room)
        reads.timerScopeOverride = next
        reads.busy = "timer-scope"
        let client = container.client
        let roomID = room.roomID
        let path = RoomsApi.timerScope(roomID)
        let body = RoomsApi.json(["own_only": next])
        let epoch = houseEpoch
        Task { @MainActor [weak self] in
            do {
                try await client.send(method: "PUT", path: path, body: body)
                guard let self, self.stillHome(epoch) else { return }
                self.reads(for: roomID).busy = nil
                self.say(RoomsStrings.timerScopeToast(room: roomID, on: next))
                // The roster is what the switch reads from, and only it can
                // say the change stuck.
                self.roster.refresh()
            } catch {
                guard let self, self.stillHome(epoch) else { return }
                let reads = self.reads(for: roomID)
                reads.busy = nil
                // `null` and not `previous` when the roster already says
                // that: the override's job is to differ from the server, and
                // one left equal to it would pin the switch for good
                // (`SatOverviewTab.kt:415-417`).
                reads.timerScopeOverride = (previous == room.timersOwnOnly) ? nil : previous
                self.say(roomsFailure("change timer announcements", error))
            }
        }
    }

    // MARK: - quiet hours

    /// Save this room's quiet hours (`SatQuietHoursSection.save`,
    /// `SatQuietHours.kt:111`).
    ///
    /// **Nothing is sent when custom hours are not both clock times**:
    /// ``roomsQuietBody(mode:start:end:)`` returns nil and the honest sentence
    /// goes up instead. A screen that posted `7:5` would have the server
    /// decide what it meant.
    public func saveQuietHours(_ room: RoomsSatellite, mode: String) {
        let reads = reads(for: room.roomID)
        guard let asked = roomsQuietBody(
            mode: mode,
            start: reads.quietStartDraft,
            end: reads.quietEndDraft
        ) else {
            say(RoomsStrings.timesLookLike)
            return
        }
        guard reads.busy == nil else { return }
        reads.busy = "quiet-hours"
        let client = container.client
        let roomID = room.roomID
        let path = RoomsApi.quietHours(roomID)
        let body = RoomsApi.json(asked)
        let start = reads.quietStartDraft
        let end = reads.quietEndDraft
        let epoch = houseEpoch
        Task { @MainActor [weak self] in
            do {
                try await client.send(method: "PUT", path: path, body: body)
                guard let self, self.stillHome(epoch) else { return }
                let reads = self.reads(for: roomID)
                reads.busy = nil
                self.say(RoomsStrings.quietToast(room: roomID, mode: mode, start: start, end: end))
                // The draft is dropped so the radios follow the server again
                // (`SatQuietHours.kt:105`): what is on screen afterwards is
                // what the house says, not what was asked for.
                reads.quietModeDraft = nil
                reads.quietHours.refresh()
            } catch {
                guard let self, self.stillHome(epoch) else { return }
                self.reads(for: roomID).busy = nil
                self.say(roomsFailure("change quiet hours", error))
            }
        }
    }

    // MARK: - timers

    /// `DELETE …/timers/{id}` and re-read (`SatTimersTab.cancel`,
    /// `SatHistoryTabs.kt:437`).
    ///
    /// **A 404 is not a failure** — it means the timer fired, or somebody else
    /// cancelled it, between the read and the tap. Android toasts
    /// "cancel failed: HTTP 404" for that; this says
    /// ``RoomsStrings/timerAlreadyFinished``, which is the judgement
    /// ``CancelOutcome/gone`` already records for Home's timers. The row goes
    /// either way, which is why the double-tap guard above matters.
    public func cancelTimer(_ room: RoomsSatellite, _ timer: RoomsTimer) {
        let reads = reads(for: room.roomID)
        guard !reads.cancelling.contains(timer.id) else { return }
        reads.cancelling.insert(timer.id)
        let client = container.client
        let roomID = room.roomID
        let timerID = timer.id
        let path = RoomsApi.timer(roomID, timerID)
        let kind = roomsTimerKind(timer)
        let epoch = houseEpoch
        Task { @MainActor [weak self] in
            var said: String
            do {
                try await client.send(method: "DELETE", path: path)
                said = RoomsStrings.cancelled(kind: kind, id: timerID)
            } catch {
                said = (error as? DomovoiError)?.failure?.status == 404
                    ? RoomsStrings.timerAlreadyFinished
                    : roomsFailure("cancel", error)
            }
            guard let self, self.stillHome(epoch) else { return }
            let reads = self.reads(for: roomID)
            reads.cancelling.remove(timerID)
            self.say(said)
            reads.timers.refresh()
        }
    }

    // MARK: - drop in

    /// End the call this room is in (`DropInSection.hangUp`,
    /// `SatOverviewTab.kt:502`).
    ///
    /// The one drop-in control the phone draws: the core's
    /// `/v1/admin/dropin/end` is device tier (`domovoi/main.py:2718-2721`).
    /// Starting a call is admin security there, and the peer picker Android
    /// draws for it is not ported — see ``RoomsModel``.
    public func hangUp(_ room: RoomsSatellite) {
        let client = container.client
        let roomID = room.roomID
        let path = RoomsApi.dropInEnd(roomID)
        let epoch = houseEpoch
        Task { @MainActor [weak self] in
            do {
                try await client.send(method: "POST", path: path)
                guard let self, self.stillHome(epoch) else { return }
                self.say(RoomsStrings.hungUp(roomID))
                self.roster.refresh()
            } catch {
                guard let self, self.stillHome(epoch) else { return }
                self.say(roomsFailure("hang up", error))
            }
        }
    }

    // MARK: - add a played row to the library

    /// Queue a row's URL for acquisition (`SatRecentlyPlayedTab.add`,
    /// `SatHistoryTabs.kt:294`).
    ///
    /// The row is marked queued optimistically and the four outcomes are
    /// ``roomsAddToast(_:title:)``'s.
    ///
    /// ### There is no missing-fulfiller path here any more (D55)
    ///
    /// Kamron, answer 55: "no fulfiller means no button and no error: the
    /// server computes `can_add` against the fulfiller allowlist, so both
    /// phones draw it honestly." `can_add` now consults the allowlist
    /// (`web/backend/api/satellites.py:985-1044`), so a row the server
    /// offered is a row a fulfiller recognises — and this method used to
    /// carry the other half of the old arrangement: it surfaced the server's
    /// own refusal text ("no fulfiller plugin is installed") in place of a
    /// generic failure, and took the optimistic mark back. **That path is
    /// dead**, so it is gone rather than kept as a sentence nothing can
    /// produce. What is left is the honest condition — ``RoomsPlayed/canAdd``
    /// and nothing else — and ``roomsFailure(_:_:)``'s ordinary wording for
    /// an add that really did fail (a house that went away mid-request, a
    /// 500).
    public func addToLibrary(_ room: RoomsSatellite, _ row: RoomsPlayed) {
        guard row.canAdd else { return }
        let reads = reads(for: room.roomID)
        guard !reads.queuedAdds.contains(row.id) else { return }
        let client = container.client
        let roomID = room.roomID
        let rowID = row.id
        let title = row.title
        var asked: [String: Any] = ["room_id": roomID]
        if let url = row.url { asked["url"] = url }
        if let title { asked["title"] = title }
        let body = RoomsApi.json(asked)
        let epoch = houseEpoch
        reads.queuedAdds.insert(rowID)
        Task { @MainActor [weak self] in
            do {
                let answer = try await client.post(
                    RoomsApi.addByURL,
                    body: body,
                    as: RoomsAddByUrlResult.self
                )
                guard let self, self.stillHome(epoch) else { return }
                self.say(roomsAddToast(answer, title: title))
                self.reads(for: roomID).played.refresh()
            } catch {
                guard let self, self.stillHome(epoch) else { return }
                self.say(roomsFailure("add", error))
                // Nothing was queued, so the optimistic mark goes back.
                self.reads(for: roomID).queuedAdds.remove(rowID)
            }
        }
    }
}
