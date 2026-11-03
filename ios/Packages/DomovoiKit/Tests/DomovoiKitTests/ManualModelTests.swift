import Foundation
import XCTest
@testable import DomovoiKit

/// The Manual screen's model against a server that is not a server: which of
/// the four load states is on screen, where in the document we are, which
/// pieces a plugin's absence hides, what each kind of link opens, and whether
/// an answer belongs to the house that is connected now.
///
/// Android specifies none of this: `ManualScreen.kt` is composables and its
/// JVM suite covers `ManualContent.kt` only, so the screen's own decisions —
/// the level machine, the link resolution, the sessions read keyed on the
/// rooms — are tested here for the first time on either phone.
/// At file scope, not inside the test class: a `static let` on a
/// `@MainActor` type is main-actor isolated, and a `Sendable` house cannot
/// take one as a default value.
/// A `domovoi-manual/2` with everything the screen's branches need: three
/// topics (one of them the centre's), an alias, a plugin's piece, a
/// `route:` link of each kind, and a say block with a feature on it.
private let manualSeededDocument = """
{"schema":"domovoi-manual/2","doc_base":"https://example.com/blob/{ref}/",
 "labels":{"start_here":"start here","map":"map","checklist_unknown":"not sure"},
 "wake_word":{"default":"hey_jarvis","names":{"hey_jarvis":"Hey Jarvis","alexa":"Alexa"},
              "quoted":"\\u201c{phrase}\\u201d",
              "note":"Some rooms answer to another word: {list}.",
              "note_item":"\\u201c{phrase}\\u201d in {rooms}","note_join":"; "},
 "handler_needs_feature":{"double_check":"ai"},
 "routes":{"music":{"dashboard":"#music","app":{"screen":"Music"}},
           "chat":{"dashboard":"#chat","app":{"screen":"Chat"}},
           "settings/rooms":{"dashboard":"#settings/rooms",
                             "app":{"screen":"Settings","tab":"rooms"}},
           "settings/voices":{"dashboard":"#settings/voices","app":{"dashboard":true}},
           "nowhere":{"app":{}}},
 "centre":{"id":"domovoi","title":"Domovoi","subtitle":"your home","opens":"how-it-works"},
 "start":{"title":"Start here",
          "blocks":[{"type":"p","text":"the manual of this house"},
                    {"type":"live","slot":"wake-word"},
                    {"type":"say","phrases":[
                       {"text":"what time is it?","handler":"clock"},
                       {"text":"it is nine","handler":"clock","then":true},
                       {"text":"are you sure?","handler":"double_check","needs":"ai"}]},
                    {"type":"checklist"}],
          "checklist":[
            {"id":"claim-admin","done_when":"admin_claimed","platforms":["dashboard"],
             "text":"Claim the admin account"},
            {"id":"first-room","done_when":"rooms","platforms":["dashboard","app"],
             "text":"Add your first room"},
            {"id":"wake-word","done_when":"room_heard","platforms":["dashboard","app"],
             "text":"Say the wake word"},
            {"id":"introduce","done_when":"people","platforms":["dashboard","app"],
             "text":"Introduce yourself"},
            {"id":"music","done_when":"music","platforms":["dashboard","app"],
             "text":"Add your music"},
            {"id":"app","done_when":"phone_app","platforms":["dashboard"],
             "text":"Put the app on your phones","app_text":"Pair this phone",
             "app_done_when":"this_phone_paired"}]},
 "topics":[{"id":"how-it-works","title":"How it works","centre":true,
            "pieces":["what-it-is"],"intro":"start at the middle"},
           {"id":"music-and-media","title":"Music and media",
            "pieces":["playlists","internet-radio","pl-alias"]},
           {"id":"rooms-and-devices","title":"Rooms and devices","pieces":["pi-room"]}],
 "pieces":[
   {"id":"what-it-is","topic":"how-it-works","title":"What it is",
    "blocks":[{"type":"p","text":"[Music](route:music) and [Chat](route:chat)"},
              {"type":"p","text":"[Rooms](route:settings/rooms), [Voices](route:settings/voices)"},
              {"type":"p","text":"[nowhere](route:nowhere) and [a piece](node:pi-room)"}],
    "more":{"text":"More detail","target":"README.md#what"}},
   {"id":"playlists","topic":"music-and-media","title":"Playlists","subtitle":"make a list",
    "keywords":["queue"],"blocks":[{"type":"live","slot":"things-to-say"}]},
   {"id":"internet-radio","topic":"music-and-media","title":"Internet radio",
    "subtitle":"stations","keywords":["stream"],"plugin":"radio",
    "blocks":[{"type":"p","text":"a plugin's page"}]},
   {"id":"pl-alias","topic":"music-and-media","title":"Lists","alias_of":"playlists"},
   {"id":"pi-room","topic":"rooms-and-devices","title":"A Raspberry Pi room",
    "subtitle":"a speaker and a mic","keywords":["satellite"],
    "blocks":[{"type":"live","slot":"rooms"}]}]}
"""

private final class ManualTestHouse: Sendable {
    let manual = Shared<StubURLProtocol.Answer>(.json(manualSeededDocument))
    let rooms = Shared<String>("[]")
    let handlers = Shared<String>(#"{"handlers":[],"stale":false}"#)
    let plugins = Shared<String>(#"{"plugins":[]}"#)
    let version = Shared<String>(#"{"version":"1.0.0"}"#)
    let people = Shared<String>("[]")
    let stats = Shared<String>(#"{"total_tracks":0}"#)
    /// room id → that room's sessions body.
    let sessions = Shared<[String: String]>([:])
    /// Which rooms were asked for their sessions, in order.
    let sessionsAsked = Shared<[String]>([])

    func install() {
        StubURLProtocol.dispatch { [self] request in
            guard let parts = request.url.flatMap({
                URLComponents(url: $0, resolvingAgainstBaseURL: false)
            }) else { return .json("{}") }
            let path = parts.path
            if path == "/api/health" {
                let challenge = (parts.queryItems ?? []).first { $0.name == "challenge" }?.value
                return .json((try? TestIdentity.real.healthBody(challenge: challenge)) ?? "{}")
            }
            if path == manualPath { return manual.current }
            if path == "/api/satellites" { return .json(rooms.current) }
            if path == "/api/capabilities/manual" { return .json(handlers.current) }
            if path == "/api/plugins" { return .json(plugins.current) }
            if path == "/api/config/version" { return .json(version.current) }
            if path == "/api/people" { return .json(people.current) }
            if path == "/api/music/library/stats" { return .json(stats.current) }
            if path.hasPrefix("/api/satellites/"), path.hasSuffix("/sessions") {
                let room = path
                    .dropFirst("/api/satellites/".count)
                    .dropLast("/sessions".count)
                sessionsAsked.mutate { $0.append(String(room)) }
                return .json(sessions.current[String(room)] ?? "[]")
            }
            return .json("{}")
        }
    }
}

@MainActor
final class ManualModelTests: XCTestCase {

    private let server = "http://10.0.0.42:6369"
    private let otherServer = "http://10.0.0.99:6369"

    override func setUp() {
        super.setUp()
        StubURLProtocol.reset()
    }

    override func tearDown() {
        StubURLProtocol.reset()
        super.tearDown()
    }

    // MARK: - the house

    private func rig(
        server address: String? = nil,
        visibility: RouteVisibility? = nil
    ) -> (ManualModel, ManualTestHouse, InMemoryShellStore) {
        let house = ManualTestHouse()
        house.install()
        let url = address ?? server
        let store = InMemoryShellStore(serverURL: url, token: "household-abc")
        store.trustServer(url, identity: TestIdentity.real.pin)
        store.trustServer(otherServer, identity: TestIdentity.real.pin)
        let container = AppContainer(
            store: store,
            vault: TokenVault(items: InMemoryKeychain()),
            lock: HouseholdLock(),
            clock: { 0 },
            configuration: StubURLProtocol.session(timeout: 5)
        )
        let seen = visibility ?? RouteVisibility(
            capabilities: Capabilities(features: ["ai": true]),
            sharedScreen: false,
            phoneCanChat: false
        )
        let model = ManualModel(
            in: container,
            visibility: { seen },
            cache: ManualCacheBox()
        )
        return (model, house, store)
    }

    private func settleUntil(
        _ done: @MainActor () -> Bool,
        _ what: String = "the model",
        file: StaticString = #filePath,
        line: UInt = #line
    ) async {
        for _ in 0..<settleYieldLimit {
            if done() { return }
            await Task.yield()
        }
        XCTFail("\(what) never settled in \(settleYieldLimit) yields", file: file, line: line)
    }

    private func everythingLands() async {
        for _ in 0..<settleYieldLimit { await Task.yield() }
    }

    // MARK: - the load

    func testTheManualIsTheConnectedServersOwn() async {
        let (model, _, _) = rig()
        XCTAssertNil(model.load, "nothing is drawn until the document lands")
        model.start()
        await settleUntil({ model.doc != nil }, "the document")
        XCTAssertEqual("the manual of this house", model.doc?.start.blocks.first?.text)
        XCTAssertFalse(model.isCachedCopy)
        XCTAssertEqual(.start, model.place)
        model.stop()
    }

    // MARK: - where we are

    func testTheThreeLevelsAndTheWayBackUp() async {
        let (model, _, _) = rig()
        model.start()
        await settleUntil({ model.doc != nil })

        model.go("map")
        XCTAssertEqual(.map, model.place)
        model.go("music-and-media")
        XCTAssertEqual(.topic("music-and-media"), model.place)
        model.go("playlists")
        XCTAssertEqual(.piece("playlists"), model.place)

        model.up()
        XCTAssertEqual(.topic("music-and-media"), model.place, "a piece goes up to its topic")
        model.up()
        XCTAssertEqual(.map, model.place, "a topic zooms out to the map")
        model.up()
        XCTAssertEqual(.start, model.place, "and the map goes back to Start here")
        model.stop()
    }

    func testAnAliasOpensThePageItNames() async {
        let (model, _, _) = rig()
        model.start()
        await settleUntil({ model.doc != nil })
        model.go("pl-alias")
        XCTAssertEqual(
            .piece("playlists"),
            model.place,
            "an alias is another name for a page, not a page of its own"
        )
        model.go("no-such-piece")
        XCTAssertEqual(.start, model.place, "an id the document has not got is Start here")
        model.stop()
    }

    /// The level is a string in `@SceneStorage`, so it has to survive the
    /// round trip through one.
    func testTheLevelSurvivesBeingSavedAndRestored() {
        for place in [ManualPlace.start, .map, .topic("music-and-media"), .piece("playlists")] {
            XCTAssertEqual(place, ManualPlace(saved: place.saved))
        }
        XCTAssertEqual(["start", "map", "t:x", "p:y"], [
            ManualPlace.start.saved, ManualPlace.map.saved,
            ManualPlace.topic("x").saved, ManualPlace.piece("y").saved,
        ])
        XCTAssertEqual(
            .start,
            ManualPlace(saved: "whatever a newer build wrote"),
            "a saved string this build does not know is Start here, not a crash"
        )
    }

    // MARK: - the plugin gate

    func testAPluginsPieceIsHiddenWhileThePluginIsNot() async {
        let (model, house, _) = rig()
        model.start()
        await settleUntil({ model.doc != nil && model.plugins.data != nil })
        XCTAssertFalse(
            model.visiblePieces.contains("internet-radio"),
            "no plugins installed, so the radio page is not part of this server's manual"
        )
        XCTAssertTrue(model.visiblePieces.contains("playlists"))
        model.query = "radio"
        XCTAssertEqual([], model.results.map(\.id), "and it cannot be found either")

        house.plugins.set(
            #"{"plugins":[{"slug":"radio","name":"Radio","version":"1.2.0","enabled":true,"status":"ok"}]}"#
        )
        model.plugins.refresh()
        await settleUntil({ model.visiblePieces.contains("internet-radio") }, "the plugin")
        XCTAssertEqual(["internet-radio"], model.results.map(\.id))

        house.plugins.set(
            #"{"plugins":[{"slug":"radio","name":"Radio","enabled":false,"status":"ok"}]}"#
        )
        model.plugins.refresh()
        await settleUntil({ !model.visiblePieces.contains("internet-radio") }, "the switch-off")
        model.stop()
    }

    /// A piece's footer offers the next **visible** sibling, so switching a
    /// plugin off closes the gap rather than offering a page that is not
    /// there.
    func testTheNextSiblingSkipsWhatThisServerDoesNotShow() async {
        let (model, house, _) = rig()
        model.start()
        await settleUntil({ model.doc != nil && model.plugins.data != nil })
        let playlists = try? XCTUnwrap(model.doc?.piece("playlists"))
        XCTAssertEqual(
            "pl-alias",
            model.next(after: playlists ?? ManualPiece())?.id,
            "the hidden plugin page is stepped over — the walk is the topic's list filtered "
                + "by what this server shows, aliases included (Kotlin's `siblings`, "
                + "which excludes an alias from search but not from the ring)"
        )
        house.plugins.set(
            #"{"plugins":[{"slug":"radio","enabled":true,"status":"degraded"}]}"#
        )
        model.plugins.refresh()
        await settleUntil({ model.visiblePieces.contains("internet-radio") })
        XCTAssertEqual(
            "internet-radio",
            model.next(after: playlists ?? ManualPiece())?.id,
            "degraded still carries its pages"
        )
        model.stop()
    }

    // MARK: - links

    func testEachKindOfLinkOpensItsOwnThing() async {
        let (model, _, _) = rig()
        model.start()
        await settleUntil({ model.doc != nil })

        XCTAssertEqual(.screen(.music), model.openRoute("music"))
        XCTAssertEqual(
            .settingsTab("rooms"),
            model.openRoute("settings/rooms"),
            "Rooms is a section of Settings on this platform, never a screen"
        )
        XCTAssertEqual(
            .web("\(server)/#settings/voices"),
            model.openRoute("settings/voices"),
            "a route the app has no screen for opens the dashboard at its hash"
        )
        XCTAssertEqual(
            .nothing,
            model.openRoute("nowhere"),
            "no app screen and not a dashboard route"
        )
        XCTAssertEqual(
            .nothing,
            model.openRoute("not-in-the-table"),
            "a target the document does not define"
        )
        model.stop()
    }

    func testADocLinkOpensTheDocsOfTheVersionThisServerRuns() async {
        let (model, house, _) = rig()
        model.start()
        await settleUntil({ model.doc != nil && model.version.data != nil })
        XCTAssertEqual(
            .web("https://example.com/blob/v1.0.0/README.md#what"),
            model.openDoc("doc:README.md#what")
        )
        house.version.set(#"{"version":"1.0.0 + 14 (abc1234)"}"#)
        model.version.refresh()
        await settleUntil({ model.version.data?.version?.contains("14") == true })
        XCTAssertEqual(
            .web("https://example.com/blob/main/README.md#what"),
            model.openDoc("doc:README.md#what"),
            "past a release there is no tag, so the docs are main's"
        )
        model.stop()
    }

    /// The bug the Kotlin's comment at `:895` exists for: a link that looks
    /// like a link and lands back on Home.
    func testALinkToAScreenThisServerDoesNotOfferIsNotALink() async {
        let aiOff = RouteVisibility(
            capabilities: Capabilities(features: ["ai": false]),
            sharedScreen: false,
            phoneCanChat: false
        )
        let (model, _, _) = rig(visibility: aiOff)
        model.start()
        await settleUntil({ model.doc != nil })
        XCTAssertFalse(model.linkIsLive(kind: "route", target: "chat"))
        XCTAssertTrue(
            model.linkIsLive(kind: "route", target: "settings/rooms"),
            "the link beside it is still a link"
        )
        XCTAssertTrue(model.linkIsLive(kind: "node", target: "pi-room"))
        XCTAssertTrue(model.linkIsLive(kind: "doc", target: "README.md"))
        XCTAssertTrue(
            model.linkIsLive(kind: "route", target: "not-in-the-table"),
            "a route the document does not define stays a link that does nothing, "
                + "as Android's does — a document naming a route it has not got is a document bug"
        )
        model.stop()
    }

    func testTheDashboardsOwnManualIsOfferedWhenThisServerHasNone() async {
        let (model, house, _) = rig()
        house.manual.set(.json(#"{"detail":"Not Found"}"#, status: 404))
        model.start()
        await settleUntil({ model.load != nil })
        XCTAssertEqual(.noManual, model.load)
        XCTAssertEqual("\(server)/#manual", model.dashboardManualURL)
        model.stop()
    }

    // MARK: - what the server knows

    func testTheChecklistTicksWhatTheReadsFound() async {
        let (model, house, _) = rig()
        house.rooms.set(#"[{"room_id":"kitchen","status":"online","wake_word":"hey_jarvis"}]"#)
        house.people.set(#"[{"id":1}]"#)
        house.stats.set(#"{"total_tracks":412}"#)
        house.sessions.set(["kitchen": "[]"])
        model.start()
        await settleUntil({ model.roomHeard != nil }, "the sessions read")

        let lines = model.checklist
        XCTAssertEqual(
            ["first-room", "wake-word", "introduce", "music", "app"],
            lines.map(\.id),
            "the dashboard's own line is dropped and the pairing line is the app's wording"
        )
        XCTAssertFalse(
            lines.contains { $0.text.contains("Claim the admin") },
            "a line this app cannot act on is not drawn at all"
        )
        let done = Dictionary(uniqueKeysWithValues: lines.map { ($0.id, $0.done) })
        XCTAssertEqual(true, done["first-room"])
        XCTAssertEqual(false, done["wake-word"], "the room has heard nobody yet")
        XCTAssertEqual(true, done["introduce"])
        XCTAssertEqual(true, done["music"])
        XCTAssertEqual(true, done["app"], "the app is reading the line")
        model.stop()
    }

    /// `heard` asks the **first three** rooms and stops at the first that has
    /// a session (`ManualScreen.kt:262`). A house of twelve rooms is not
    /// twelve requests for one tick.
    func testTheRoomHeardReadAsksAtMostThreeRooms() async {
        let (model, house, _) = rig()
        let ids = ["a", "b", "c", "d"]
        house.rooms.set(
            "[" + ids.map { #"{"room_id":"\#($0)","status":"online"}"# }.joined(separator: ",") + "]"
        )
        house.sessions.set(["c": #"[{"room_id":"c"}]"#])
        model.start()
        await settleUntil({ model.roomHeard != nil }, "the sessions read")
        XCTAssertEqual(true, model.roomHeard)
        XCTAssertEqual(
            ["a", "b", "c"],
            house.sessionsAsked.current,
            "the fourth room was never asked, and the read stopped at the one that answered"
        )
        model.stop()
    }

    func testAHouseWithNoRoomsHasHeardNobodyWithoutAsking() async {
        let (model, house, _) = rig()
        model.start()
        await settleUntil({ model.roomHeard != nil })
        XCTAssertEqual(false, model.roomHeard)
        XCTAssertEqual([], house.sessionsAsked.current)
        model.stop()
    }

    func testTheWakeNoteNamesOnlyTheOtherWords() async {
        let (model, house, _) = rig()
        house.rooms.set("""
        [{"room_id":"kitchen","room_label":"Kitchen","status":"online","wake_word":"hey_jarvis"},
         {"room_id":"shed","status":"offline","wake_word":"alexa"}]
        """)
        model.start()
        await settleUntil({ model.rooms.data?.count == 2 && model.doc != nil })
        XCTAssertEqual("Hey Jarvis", model.wake.lead)
        XCTAssertEqual(
            "Some rooms answer to another word: \u{201C}Alexa\u{201D} in shed.",
            model.wakeNote
        )

        house.rooms.set(#"[{"room_id":"kitchen","status":"online","wake_word":"hey_jarvis"}]"#)
        model.rooms.refresh()
        await settleUntil({ model.rooms.data?.count == 1 })
        XCTAssertNil(
            model.wakeNote,
            "one word in the house: the slot draws nothing, not an empty box"
        )
        model.stop()
    }

    func testThingsToSayListsOnlyWhatTheServerCanAnswer() async {
        let (model, house, _) = rig(
            visibility: RouteVisibility(
                capabilities: Capabilities(features: ["ai": false]),
                sharedScreen: false,
                phoneCanChat: false
            )
        )
        house.handlers.set("""
        {"handlers":[
          {"name":"clock","display":{"label":"The time"},"example_phrases":["what time is it?"],
           "requires_network":"no","origin":"core"},
          {"name":"radio","display":{"label":"Radio"},"example_phrases":[" "],
           "requires_network":"yes","origin":"radio"},
          {"name":"double_check","display":{"label":"Double check"},"origin":"core"}],
         "stale":false}
        """)
        model.start()
        await settleUntil({ model.handlers.data != nil && model.doc != nil })

        XCTAssertEqual(
            ["clock", "radio"],
            model.listedHandlers.map(\.name),
            "double_check needs features.ai, which this server has off"
        )
        let clock = model.listedHandlers[0]
        XCTAssertEqual("The time", clock.title)
        XCTAssertEqual(["what time is it?"], clock.said(fallback: []))
        let radio = model.listedHandlers[1]
        XCTAssertEqual(
            ["what time is it?"],
            radio.said(fallback: model.phrasesByHandler["clock"] ?? []),
            "a handler that lists only a blank example falls back to the manual's own phrases"
        )
        XCTAssertEqual("yes", radio.requiresNetwork, "worded by labels.net.yes")
        model.stop()
    }

    /// A body with `stale` and no `handlers` is where Android's
    /// `jsonObject["handlers"]!!` crashes. Here it decodes to no handlers and
    /// the slot draws nothing.
    func testAHandlersBodyWithNoHandlersIsNotACrash() async {
        let (model, house, _) = rig()
        house.handlers.set(#"{"stale":true,"unavailable":true}"#)
        model.start()
        await settleUntil({ model.handlers.data != nil })
        XCTAssertEqual([], model.listedHandlers.map(\.name))
        XCTAssertTrue(model.handlers.data?.unavailable == true)
        model.stop()
    }

    func testAPhraseTheServerCannotAnswerIsNotShown() async {
        let (model, _, _) = rig(
            visibility: RouteVisibility(
                capabilities: Capabilities(features: ["ai": false]),
                sharedScreen: false,
                phoneCanChat: false
            )
        )
        model.start()
        await settleUntil({ model.doc != nil })
        let say = try? XCTUnwrap(model.doc?.start.blocks.first { $0.type == "say" })
        let rows = manualSayRows(say?.phrases ?? [], features: model.features)
        XCTAssertEqual(["what time is it?"], rows.map(\.phrase.text))
        XCTAssertEqual("it is nine", rows.first?.then?.text, "and it keeps its own reply")
        model.stop()
    }

    // MARK: - the read-after-await class

    /// **The top defect class on this track.** The manual read is held open,
    /// the saved server is changed *inside* the round trip, and the first
    /// house's answer must land nowhere — then the second house's must.
    func testAnAnswerForTheHouseWeHaveLeftLandsNowhere() async {
        let arrived = Shared<Bool>(false)
        let release = Shared<Bool>(false)
        let first = manualSeededDocument.replacingOccurrences(
            of: "the manual of this house",
            with: "the first house's sentence"
        )
        let second = manualSeededDocument.replacingOccurrences(
            of: "the manual of this house",
            with: "the second house's sentence"
        )
        StubURLProtocol.dispatch { request in
            guard let parts = request.url.flatMap({
                URLComponents(url: $0, resolvingAgainstBaseURL: false)
            }) else { return .json("{}") }
            if parts.path == "/api/health" {
                let challenge = (parts.queryItems ?? []).first { $0.name == "challenge" }?.value
                return .json((try? TestIdentity.real.healthBody(challenge: challenge)) ?? "{}")
            }
            guard parts.path == manualPath else { return .json("{}") }
            // The first house's read is held open; the second answers at once.
            guard request.url?.absoluteString.contains("10.0.0.42") == true else {
                return .json(second)
            }
            arrived.set(true)
            for _ in 0..<400 {
                if release.current { break }
                Thread.sleep(forTimeInterval: 0.002)
            }
            return .json(first)
        }
        let store = InMemoryShellStore(serverURL: server, token: "household-abc")
        store.trustServer(server, identity: TestIdentity.real.pin)
        store.trustServer(otherServer, identity: TestIdentity.real.pin)
        let container = AppContainer(
            store: store,
            vault: TokenVault(items: InMemoryKeychain()),
            lock: HouseholdLock(),
            clock: { 0 },
            configuration: StubURLProtocol.session(timeout: 5)
        )
        let model = ManualModel(
            in: container,
            visibility: {
                RouteVisibility(capabilities: .empty, sharedScreen: false, phoneCanChat: false)
            },
            cache: ManualCacheBox()
        )

        model.start()
        await settle(for: arrived, what: "the first house's manual read")
        // Inside the round trip: the phone moves house, exactly as
        // ``ShellModel/refresh()`` would have it.
        _ = store.setServerURL(otherServer)
        container.serverAddressChanged()
        release.set(true)
        await everythingLands()

        XCTAssertNotEqual(
            "the first house's sentence",
            model.doc?.start.blocks.first?.text,
            "the answer for the house we left was drawn as this house's manual"
        )
        await settleUntil(
            { model.doc?.start.blocks.first?.text == "the second house's sentence" },
            "the second house's manual"
        )
        model.stop()
    }

    // MARK: - the server key is a value, not an event

    /// ``AppContainer/serverAddressChanged()`` is fired by
    /// ``ShellModel/refresh()`` on every foreground return, on every
    /// token-health change, and **once a second** while the token-trouble
    /// countdown runs. A sink that reloaded on the event rather than on the
    /// address blanked this page to its loading state each time, so the
    /// shouting is reproduced here: the document must stay on screen and the
    /// manual must be fetched once.
    func testAShoutWithTheSameAddressNeitherBlanksNorRefetches() async {
        let fetches = Shared<Int>(0)
        StubURLProtocol.dispatch { request in
            guard let parts = request.url.flatMap({
                URLComponents(url: $0, resolvingAgainstBaseURL: false)
            }) else { return .json("{}") }
            if parts.path == "/api/health" {
                let challenge = (parts.queryItems ?? []).first { $0.name == "challenge" }?.value
                return .json((try? TestIdentity.real.healthBody(challenge: challenge)) ?? "{}")
            }
            guard parts.path == manualPath else { return .json("{}") }
            fetches.mutate { $0 += 1 }
            return .json(manualSeededDocument)
        }
        let (model, container, _) = keyedRig()

        model.start()
        await settleUntil({ model.doc != nil }, "the document")
        XCTAssertEqual(1, fetches.current)

        for _ in 0..<5 {
            container.serverAddressChanged()
            XCTAssertNotNil(
                model.load,
                "a redraw's shout blanked the manual to its loading state"
            )
        }
        await everythingLands()

        XCTAssertNotNil(model.load, "the manual did not come back after the shouting")
        XCTAssertEqual(
            1, fetches.current,
            "the document was read again for an address that had not changed"
        )
        model.stop()
    }

    /// ``ManualModel/stop()`` takes the model off the server registry, and
    /// SwiftUI does not promise that `@State` dies on every disappearance — a
    /// tab switch may keep the model, so ``ManualModel/start()`` has to
    /// re-join (``ApiRead``'s `joinTheServerKey()`). Without it the live slots
    /// re-key to the new house while the page keeps the previous server's
    /// document, which is the one thing the per-server copy exists to prevent.
    func testTheServerKeyIsRejoinedWhenTheScreenComesBack() async {
        let first = manualSeededDocument.replacingOccurrences(
            of: "the manual of this house",
            with: "the first house's sentence"
        )
        let second = manualSeededDocument.replacingOccurrences(
            of: "the manual of this house",
            with: "the second house's sentence"
        )
        StubURLProtocol.dispatch { request in
            guard let parts = request.url.flatMap({
                URLComponents(url: $0, resolvingAgainstBaseURL: false)
            }) else { return .json("{}") }
            if parts.path == "/api/health" {
                let challenge = (parts.queryItems ?? []).first { $0.name == "challenge" }?.value
                return .json((try? TestIdentity.real.healthBody(challenge: challenge)) ?? "{}")
            }
            guard parts.path == manualPath else { return .json("{}") }
            let here = request.url?.absoluteString.contains("10.0.0.42") == true
            return .json(here ? first : second)
        }
        let (model, container, store) = keyedRig()

        model.start()
        await settleUntil({ model.doc != nil }, "the first house's manual")
        XCTAssertEqual("the first house's sentence", model.doc?.start.blocks.first?.text)

        // The screen goes away and comes back, which is all a tab switch is.
        model.stop()
        model.start()
        await everythingLands()
        XCTAssertEqual(
            "the first house's sentence",
            model.doc?.start.blocks.first?.text,
            "coming back re-read a document that had not changed"
        )

        _ = store.setServerURL(otherServer)
        container.serverAddressChanged()
        await settleUntil(
            { model.doc?.start.blocks.first?.text == "the second house's sentence" },
            "the second house's manual after the screen came back"
        )
        model.stop()
    }

    /// ``rig()`` keeps its container to itself; these two cases drive it.
    private func keyedRig() -> (ManualModel, AppContainer, InMemoryShellStore) {
        let store = InMemoryShellStore(serverURL: server, token: "household-abc")
        store.trustServer(server, identity: TestIdentity.real.pin)
        store.trustServer(otherServer, identity: TestIdentity.real.pin)
        let container = AppContainer(
            store: store,
            vault: TokenVault(items: InMemoryKeychain()),
            lock: HouseholdLock(),
            clock: { 0 },
            configuration: StubURLProtocol.session(timeout: 5)
        )
        let model = ManualModel(
            in: container,
            visibility: {
                RouteVisibility(capabilities: .empty, sharedScreen: false, phoneCanChat: false)
            },
            cache: ManualCacheBox()
        )
        return (model, container, store)
    }
}
