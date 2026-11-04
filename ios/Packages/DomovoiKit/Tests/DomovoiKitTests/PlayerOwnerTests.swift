import XCTest
@testable import DomovoiKit

/// Who owns the app's player, and who it saves under.
///
/// D49 / D2 (Kamron, 2026-11-03: "yes, as the next batch") and D11 ("yes,
/// match Android: build the 'listening as' picker"). Both are about plumbing
/// rather than about audio, and both broke the same way before the batch:
/// nothing owned a ``PlayerModel`` and nothing stored a listener, so five
/// screens were browse-only and every position written from a phone landed in
/// the server's *unidentified listener* slot.
///
/// The three properties this file is for are the three a sentence in a comment
/// cannot carry:
///
/// 1. **One player.** Two would be two things heard at once.
/// 2. **Built on first use, never at launch.** An app that activates a
///    `.playback` session at launch stops whatever the person was already
///    listening to (`PlayerAudioSession.swift:11-18`). Nothing may build a
///    player because the shell was built.
/// 3. **Told when the house moves.** A player stamped with the last house
///    saves the next ten seconds of a book to a server that has never heard of
///    it — [[read-after-await-wrong-key]] in its simplest form.
///
/// Every case drives ``ShellModel`` with a **counted builder** rather than the
/// real ``ShellModel/systemPlayer(_:_:)``, which is nil on the macOS this
/// package's tests build for: the counting is half of what is asserted, and a
/// builder that could not be counted could not say "once".
@MainActor
final class PlayerOwnerTests: XCTestCase {

    private let houseA = "http://10.0.0.42:6369"
    private let houseB = "http://10.0.0.77:6369"

    override func setUp() {
        super.setUp()
        StubURLProtocol.reset()
        PlayerMicArbiter.shared.reset()
    }

    /// **Leave nothing running.** Every case here builds a ``PlayerModel``,
    /// which registers with ``PlayerMicArbiter`` and the notification centre,
    /// and most leave a position read or a resume Task in the air. XCTest runs
    /// classes in one process, one after another, so work this file leaves
    /// behind runs *during somebody else's cases* — on the same main actor,
    /// against the dispatcher they installed, and into the
    /// `StubURLProtocol.recorded` they assert on.
    ///
    /// Measured, not supposed: before this tearDown, eight runs of the package
    /// suite at the pre-review commit were clean and one run in six of the
    /// fixed branch failed in `AudiobooksModelTests` and `RoomsModelTests` —
    /// two classes with nothing to do with the player, both failing on waits
    /// that are counted in yields. The yields below are what the leftovers get
    /// instead of theirs.
    override func tearDown() async throws {
        for _ in 0..<settleYieldLimit { await Task.yield() }
        StubURLProtocol.reset()
        PlayerMicArbiter.shared.reset()
        try await super.tearDown()
    }

    // MARK: - the rig

    /// A shell whose player is a counted fake.
    ///
    /// The engine, the session and the Now Playing slot are the same fakes
    /// every other player case uses (`PlayerFakes.swift`), so a case here can
    /// also assert about what the session was asked to do — which is how
    /// "never at launch" is measured rather than asserted.
    @MainActor
    private final class Rig {
        let store: InMemoryShellStore
        let container: AppContainer
        let model: ShellModel
        let listeners: PlayerListenerStore
        let place: SavedStateBox

        /// How many players this shell has been handed.
        let built: Shared<Int>
        /// Each one's session, so a case can ask whether it was ever activated.
        let sessions: Shared<[RecordingPlayerAudioSession]>
        /// The device ids the builder was asked for.
        let askedFor: Shared<[String]>

        init(serverURL: String, deviceID: String? = "phone-1", listener: String? = nil) {
            let store = InMemoryShellStore(serverURL: serverURL, token: "household-abc")
            store.trustServer(serverURL, identity: TestIdentity.real.pin)
            let container = AppContainer(
                store: store,
                vault: TokenVault(items: InMemoryKeychain()),
                lock: HouseholdLock(),
                clock: { 0 },
                configuration: StubURLProtocol.session(timeout: 1)
            )
            let place = SavedStateBox()
            let listeners = PlayerListenerStore(place: place)
            listeners.set(listener)
            let built = Shared<Int>(0)
            let sessions = Shared<[RecordingPlayerAudioSession]>([])
            let askedFor = Shared<[String]>([])
            // Spelt out rather than `deviceID.map { … }`: the result is a
            // `@Sendable` closure and the type checker will not infer one
            // through `Optional.map`.
            var who: (@Sendable () -> DeviceSelf)?
            if let deviceID {
                who = { DeviceSelf(deviceID: deviceID, name: "phone", userAgent: "ua") }
            }
            let model = ShellModel(
                container: container,
                deviceSelf: who,
                listeners: listeners,
                player: { box, id in
                    built.mutate { $0 += 1 }
                    askedFor.mutate { $0.append(id) }
                    let session = RecordingPlayerAudioSession()
                    sessions.mutate { $0.append(session) }
                    return PlayerModel(
                        client: box.client,
                        deviceID: id,
                        engine: FakePlayerEngine(),
                        session: session,
                        nowPlaying: RecordingNowPlayingPublisher(),
                        clock: PlayerTestClock(nowMs: 10_000)
                    )
                },
                probe: { true },
                // Parked, so nothing here waits on the reachability loop.
                wait: { _ in try await Task.sleep(for: .seconds(3600)) },
                tick: { _ in try await Task.sleep(for: .seconds(3600)) }
            )
            self.store = store
            self.container = container
            self.place = place
            self.listeners = listeners
            self.built = built
            self.sessions = sessions
            self.askedFor = askedFor
            self.model = model
        }

        /// Move house the way the app does: the store is written, then the
        /// shell is told to re-read it. That order is the real one — the
        /// connection picker writes the store through ``ServerTrustGate`` and
        /// the selection sink calls ``ShellModel/refresh()``.
        ///
        /// - Parameter identity: whose key the new address proves. **A
        ///   different house needs a different one**, and the first version of
        ///   these cases got that wrong: both addresses were trusted with
        ///   `TestIdentity.real`, so ``PlayerServerScope/isSameServer(as:)``
        ///   was right to call them one house — a house keeps its pinned key
        ///   across a new address — and two cases failed against correct code.
        ///   `.real` is the default so that a case about *the same house at a
        ///   new address* reads as the one line it is.
        func move(to url: String, identity: TestIdentity = .real) {
            store.trustServer(url, identity: identity.pin)
            _ = store.setServerURL(url)
            model.refresh()
        }
    }

    // MARK: - 1. one player, built lazily

    /// A shell that has just been built has **no player**, and nothing has
    /// touched the audio session.
    ///
    /// This is the case the whole lazy design exists for. `PlayerModel.init`
    /// wires an `MPRemoteCommandCenter` handler, four `NotificationCenter`
    /// observers and the microphone arbiter's callbacks; its `play` is what
    /// activates `AVAudioSession`. A player built at launch would hold all of
    /// that for somebody who never asked to play anything, and the session it
    /// eventually activates would stop their podcast.
    ///
    /// Asserted through ``ShellModel/playerIsBuilt``, which answers **without
    /// building one** — reading `player` is what builds it, so a case that
    /// read `player` to check it was absent would be the thing that made it
    /// present.
    func testTheShellBuildsNoPlayerUntilSomethingAsksForOne() {
        let rig = Rig(serverURL: houseA)
        XCTAssertFalse(rig.model.playerIsBuilt, "a player was built at launch")
        XCTAssertEqual(0, rig.built.current)

        // The lifecycle, all of it, with nothing asking to play: still none.
        rig.model.refresh()
        rig.model.scenePhaseChanged(to: .background)
        rig.model.scenePhaseChanged(to: .active)
        rig.model.socketChanged(connected: true)
        XCTAssertFalse(rig.model.playerIsBuilt, "the lifecycle built a player by itself")
        XCTAssertEqual(0, rig.built.current)
        XCTAssertTrue(rig.sessions.current.isEmpty, "an audio session exists before any play")
    }

    /// Ten screens asking for the player get **one** player.
    ///
    /// The measured fact behind "share one player rather than one each": two
    /// would be two `AVQueuePlayer`s heard at once, which is the bug
    /// ``PlayerModel/play(_:startAt:resumeSec:speed:)``'s own `leaveRoom`
    /// exists for.
    func testEveryScreenGetsTheSamePlayer() {
        let rig = Rig(serverURL: houseA)
        guard let first = rig.model.player else { return XCTFail("no player was built") }
        for _ in 0..<9 { _ = rig.model.player }
        XCTAssertEqual(1, rig.built.current, "the shell built \(rig.built.current) players")
        XCTAssertEqual(1, rig.model.playerBuilds)
        XCTAssertTrue(first === rig.model.player, "a second ask got a different player")
        // Still nothing activated: building one is silent, which is what makes
        // asking for it from a screen's body safe.
        XCTAssertNil(rig.sessions.current.first?.active)
    }

    /// A phone with no device id gets **no player at all**, however often it
    /// is asked.
    ///
    /// Every position a player saves is a slot the server keys by device
    /// (`audiobooks.py:599-604`), and one built with an empty id writes every
    /// listener's place into a row belonging to nobody. Nil is what makes the
    /// five screens draw no play control, which is the honest answer to a
    /// phone that has not introduced itself.
    func testAPhoneThatHasNotIntroducedItselfGetsNoPlayer() {
        let none = Rig(serverURL: houseA, deviceID: nil)
        XCTAssertNil(none.model.player, "a player was built with no device id")
        XCTAssertEqual(0, none.built.current, "the builder was called for a phone with no id")

        let blank = Rig(serverURL: houseA, deviceID: "   ")
        XCTAssertNil(blank.model.player, "a player was built on a blank device id")
        XCTAssertEqual(0, blank.built.current)
    }

    /// The player is handed **this phone's** id, not a placeholder.
    func testThePlayerIsBuiltWithThisPhonesDeviceID() {
        let rig = Rig(serverURL: houseA, deviceID: "iphone-kamron")
        XCTAssertNotNil(rig.model.player)
        XCTAssertEqual(["iphone-kamron"], rig.askedFor.current)
    }

    // MARK: - 2. which house it belongs to

    /// A player built while house A is connected is **already stamped with
    /// house A** before anything can be queued.
    ///
    /// Not cosmetic: ``PlayerModel/serverChanged(to:)`` is what gives the
    /// model an `activeServer`, and without one the first item queued is
    /// tagged with nothing — so the move to house B would not recognise it as
    /// A's and would leave A's episode in B's queue.
    func testThePlayerKnowsWhichHouseItWasBuiltIn() {
        let rig = Rig(serverURL: houseA)
        guard let player = rig.model.player else { return XCTFail("no player") }
        player.play([PlayerSeed.episode])
        XCTAssertEqual(1, player.queue.count)

        rig.move(to: houseB, identity: .rogue)
        XCTAssertTrue(
            player.queue.isEmpty,
            "house A's episode is still queued on house B — the player was "
                + "never told which house it was built in, so nothing "
                + "recognised the episode as A's"
        )
    }

    /// Moving house **tells** the one player rather than replacing it, and the
    /// phone's own song plays on.
    ///
    /// The whole reason ``PlayerModel/serverChanged(to:)`` exists. A teardown
    /// here would stop a song that lives on the handset because the phone
    /// changed house, which has nothing to do with it; what *is* dropped is
    /// everything of the old server.
    func testMovingHouseKeepsOnePlayerAndKeepsThePhonesOwnSong() {
        let rig = Rig(serverURL: houseA)
        guard let player = rig.model.player else { return XCTFail("no player") }
        let phoneSong = PlayerItem.fromDeviceAudio(
            id: 1, title: "Shadowplay", uri: "file:///var/mobile/shadowplay.m4a"
        )
        // House A's track first, so the play head is on the one that goes.
        player.play([PlayerSeed.transmission, phoneSong])

        rig.move(to: houseB, identity: .rogue)

        XCTAssertEqual(1, rig.built.current, "the move built a second player")
        XCTAssertTrue(player === rig.model.player, "the move replaced the player")
        XCTAssertEqual(
            ["Shadowplay"], player.queue.map(\.title),
            "the queue after the move is \(player.queue.map(\.title)) — house "
                + "A's library track should have gone and the phone's own song "
                + "should have stayed"
        )
    }

    /// The **same** house at a new address is not a move.
    ///
    /// A house keeps its pinned identity across a DHCP lease, which is exactly
    /// what ``PlayerServerScope/isSameServer(as:)`` is for. Treating it as a
    /// move would empty the queue every time the router handed out a new
    /// address.
    func testTheSameHouseAtANewAddressKeepsTheQueue() {
        let rig = Rig(serverURL: houseA)
        guard let player = rig.model.player else { return XCTFail("no player") }
        player.play([PlayerSeed.transmission])

        // A second spelling of the same house: a different address, the same
        // pinned identity.
        rig.move(to: "http://domovoi.local:6369")

        XCTAssertEqual(
            ["Transmission"], player.queue.map(\.title),
            "the same house at a new address emptied the queue"
        )
    }

    // MARK: - 3. who it saves under (D11)

    /// A player is handed the stored listener **at the moment it is built**.
    ///
    /// Order matters and it bit the first version of this: a phone whose
    /// listener was picked in Settings last week builds its player on today's
    /// first tap, and if the id were only pushed on a *change* that player
    /// would save the whole of today's listening to the unidentified slot.
    func testAPlayerIsBornKnowingWhoIsListening() {
        let rig = Rig(serverURL: houseA, listener: "7")
        XCTAssertEqual("7", rig.model.player?.listenerPersonID)
    }

    /// Picking a person in Settings reaches a player that is **already
    /// playing**, and is remembered for the next launch.
    func testPickingAListenerReachesTheLivePlayerAndIsRemembered() {
        let rig = Rig(serverURL: houseA)
        guard let player = rig.model.player else { return XCTFail("no player") }
        XCTAssertNil(player.listenerPersonID, "a fresh phone already claims a listener")

        rig.model.listenerChanged(to: "7")
        XCTAssertEqual("7", player.listenerPersonID)
        XCTAssertEqual("7", rig.model.listeners.personID)
        // The next launch, over the same saved bytes.
        XCTAssertEqual("7", PlayerListenerStore(place: rig.place).personID)

        rig.model.listenerChanged(to: nil)
        XCTAssertNil(player.listenerPersonID, "going back to this device left the person set")
        XCTAssertNil(PlayerListenerStore(place: rig.place).personID)
    }

    /// The store treats a blank as "me (this device)".
    ///
    /// Not defensive for its own sake: the id is a `String?` because Android's
    /// is, and an empty string sent as `person_id=` is a *different key* to the
    /// server rather than an absent one.
    func testABlankListenerIsThisDevice() {
        let place = SavedStateBox()
        let store = PlayerListenerStore(place: place)
        store.set("  ")
        XCTAssertNil(store.personID)
        store.set("7")
        XCTAssertEqual("7", store.personID)
        store.set(nil)
        XCTAssertNil(store.personID)
        XCTAssertNil(PlayerListenerStore(place: place).personID)
    }

    /// A blob that is not a listener is **no listener**, not a crash.
    func testACorruptListenerBlobReadsAsThisDevice() {
        let place = SavedStateBox()
        place.write(Data([0xFF, 0xFE, 0x00]))
        XCTAssertNil(PlayerListenerStore(place: place).personID)
    }

    // MARK: - the picker's rows

    /// The dropdown is "me (this device)" and then the household, in the
    /// server's order (`ConnectionPanel.kt:106`).
    func testTheListenerDropdownLeadsWithThisDevice() {
        let rows = listenerOptions(people: [
            Person(id: 7, name: "Kamron"),
            Person(id: 9, name: "Chevy"),
        ])
        XCTAssertEqual(
            [PlayerStrings.listenerThisDevice, "Kamron", "Chevy"], rows.map(\.name)
        )
        XCTAssertEqual([nil, "7", "9"], rows.map(\.personID))
    }

    /// A people list that has not answered yet is **just the nil row**: the
    /// picker still works, and the one thing it can offer needs no server.
    func testTheDropdownWorksBeforeThePeopleListAnswers() {
        let rows = listenerOptions(people: [])
        XCTAssertEqual([PlayerStrings.listenerThisDevice], rows.map(\.name))
        XCTAssertNil(selectedListener(rows, personID: nil)?.personID)
    }

    /// A stored person who has since been removed from the household selects
    /// **this device**, not an empty row.
    ///
    /// `people.firstOrNull { … }` lands on the same answer on Android. The
    /// alternative — a dropdown showing nothing selected — is a control that
    /// cannot say what it is doing.
    func testAListenerWhoIsNoLongerInTheHouseholdReadsAsThisDevice() {
        let rows = listenerOptions(people: [Person(id: 9, name: "Chevy")])
        XCTAssertEqual("9", selectedListener(rows, personID: "9")?.personID)
        XCTAssertNil(
            selectedListener(rows, personID: "7")?.personID,
            "a person who has left the household is still shown as selected"
        )
        XCTAssertEqual(
            PlayerStrings.listenerThisDevice,
            selectedListener(rows, personID: "7")?.name
        )
    }

    /// The chip says the name when there is one and an ellipsis while the
    /// people list is on its way (`AudiobooksScreen.kt:343`).
    func testTheChipSaysAnEllipsisUntilTheNameArrives() {
        XCTAssertEqual("listening as Kamron", PlayerStrings.listeningAsChip("Kamron"))
        XCTAssertEqual("listening as …", PlayerStrings.listeningAsChip(nil))
    }

    // MARK: - 4. the player's late sentences reach a person (review 1)

    /// The shell gives the player somewhere to speak, and that somewhere is
    /// the shell's own toast.
    ///
    /// Review 1's S2. ``PlayerModel/play(_:startAt:resumeSec:speed:)`` returns
    /// the room it paused "so a consumer can say 'paused office'", and every
    /// one of the five new callers discarded it; the room's answer to the pause
    /// landed in ``PlayerModel/trouble``, whose own doc says it is "taken by
    /// the screen", and **no screen took it**. So a person casting to the
    /// kitchen tapped play on an episode, the kitchen went silent, and nothing
    /// said why — where Android's `playHereAndSay` says it
    /// (`MusicScreen.kt:575-588`).
    ///
    /// The fix is one wire rather than five call sites, because the sentence
    /// arrives **after** the tap: the shell's toast is the one surface that
    /// outlives the screen the tap was on.
    func testThePlayerIsGivenTheShellsToastToSpeakThrough() {
        let rig = Rig(serverURL: houseA)
        guard let player = rig.model.player else { return XCTFail("no player was built") }

        XCTAssertNotNil(
            player.say,
            "the player has nowhere to say what the room answered, so a room "
                + "paused by a play here goes quiet with nothing said"
        )
        player.say?("playing \"Decades\" on this device · paused kitchen")
        XCTAssertEqual(
            "playing \"Decades\" on this device · paused kitchen",
            rig.model.toast?.text
        )
    }

    /// End to end: a play here **while casting** names the room it paused, and
    /// names what started.
    ///
    /// The sentence is `CastPlan.kt:169`'s, composed in
    /// ``PlayerStrings/playHereNote(playing:room:paused:)`` — and the
    /// `playing` half is the title of the thing that started, which is what
    /// Android passes (`MusicScreen.kt:576`). It said a bare "playing on this
    /// device" until this review, a sentence a person cannot place.
    ///
    /// It arrives only once the room has answered the pause, which is the
    /// whole reason it is a toast and not a return value: *"so it never says
    /// 'paused office' over a pause the core says didn't happen"*
    /// (`MusicScreen.kt:567-570`).
    func testAPlayHereWhileCastingNamesTheRoomAndTheTrack() async {
        let house = PlayerHouse()
        house.install()
        let rig = Rig(serverURL: houseA)
        guard let player = rig.model.player else { return XCTFail("no player was built") }

        player.play([PlayerSeed.transmission])
        let went = await player.cast(to: "kitchen")
        guard case .toRoom = went else {
            return XCTFail("the cast did not go to the room: \(went)")
        }

        // The play here. `Decades` is a different song from the one the room
        // has, so the sentence cannot read right by naming the wrong item.
        let left = player.play([PlayerSeed.decades])
        XCTAssertEqual("kitchen", left, "the play here did not report the room it left")

        await settle(
            until: { rig.model.toast?.text.contains("paused kitchen") == true },
            "the room's answer to the pause"
        )
        let said = rig.model.toast?.text ?? ""
        XCTAssertTrue(
            said.contains("Decades"),
            "the sentence does not name what started: \(said)"
        )
        XCTAssertTrue(
            said.hasPrefix("playing \"Decades\" on this device"),
            "the sentence is not `MusicScreen.kt:576`'s: \(said)"
        )
    }

    /// A play here that left **no** room says nothing.
    ///
    /// The other half of the same rule, and the one that keeps a tap to one
    /// sentence: the screen's own "playing X on this device" is what a person
    /// reads when nothing was paused, and a second toast from the player would
    /// be the app saying the same thing twice.
    func testAPlayHereThatLeftNoRoomSaysNothing() async {
        let house = PlayerHouse()
        house.install()
        let rig = Rig(serverURL: houseA)
        guard let player = rig.model.player else { return XCTFail("no player was built") }

        XCTAssertNil(player.play([PlayerSeed.decades]), "a room was reported where there was none")
        for _ in 0..<settleYieldLimit { await Task.yield() }
        XCTAssertNil(
            rig.model.toast,
            "the player spoke over a play here that paused nothing: "
                + (rig.model.toast?.text ?? "")
        )
    }

    /// Yield until `done`, rather than sleeping.
    private func settle(
        until done: @MainActor () -> Bool,
        _ what: String,
        file: StaticString = #filePath,
        line: UInt = #line
    ) async {
        for _ in 0..<settleYieldLimit {
            if done() { return }
            await Task.yield()
        }
        XCTFail("\(what) never settled in \(settleYieldLimit) yields", file: file, line: line)
    }
}
