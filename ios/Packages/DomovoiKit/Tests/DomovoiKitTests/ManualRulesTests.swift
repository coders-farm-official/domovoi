import XCTest
@testable import DomovoiKit

/// Every rule of `ManualContent.kt` at its boundaries — the table in
/// `design-notes/ios/overnight/manual/MAP.md` §2, row by row.
///
/// These are the tests Android has on the JVM (`ManualContentTest`) and the
/// reason the whole content model is in DomovoiKit rather than beside the
/// views: a wake-word sentence, a search ranking and a checklist tick are
/// decisions, and none of them needs a Simulator to be wrong.
final class ManualRulesTests: XCTestCase {

    // MARK: - what this server shows

    func testAPhraseIsDroppedOnlyWhenItsFeatureIsExplicitlyOff() {
        let phrases = [
            ManualPhrase(text: "what time is it?"),
            ManualPhrase(text: "why is the sky blue?", needs: "ai"),
            ManualPhrase(text: "because of the air", then: true, needs: "ai"),
            ManualPhrase(text: "read my letter", needs: "office"),
        ]
        XCTAssertEqual(
            ["what time is it?", "read my letter"],
            shownManualPhrases(phrases, features: ["ai": false, "office": true]).map(\.text),
            "the AI phrase and its reply both go"
        )
        XCTAssertEqual(
            4,
            shownManualPhrases(phrases, features: [:]).count,
            "a server that says nothing about a feature keeps the phrase — an older server, "
                + "and a manifest that failed to load, both show every phrase"
        )
        XCTAssertEqual(4, shownManualPhrases(phrases, features: ["ai": true, "office": true]).count)
    }

    func testOnlyTheDashboardsBlocksAreDropped() {
        XCTAssertTrue(shownInApp(nil))
        XCTAssertTrue(shownInApp("app"))
        XCTAssertFalse(shownInApp("dashboard"))
        XCTAssertFalse(
            shownInApp("kiosk"),
            "`only` is an allow-list of one reader, not a deny-list: a block addressed to a "
                + "reader this app is not is not drawn here, which is Kotlin's `only == \"app\"`"
        )
    }

    // MARK: - the wake word

    func testTitleWordsSpellsOutASlug() {
        XCTAssertEqual("Living Room", manualTitleWords("living_room"))
        XCTAssertEqual("Hey Jarvis", manualTitleWords("hey-jarvis"))
        XCTAssertEqual("Living Room", manualTitleWords("  living   room  "))
        XCTAssertEqual("Hey Jarvis", manualTitleWords("hey__-__jarvis"))
        XCTAssertEqual("", manualTitleWords(nil))
        XCTAssertEqual("", manualTitleWords("___"))
    }

    private let wake = ManualWakeWord(
        names: ["hey_jarvis": "Hey Jarvis", "alexa": "Alexa", "hey_mycroft": "Hey Mycroft"]
    )

    func testARoomsWordComesFromItsOwnPhraseFirst() {
        let own = ManualRoom(roomID: "kitchen", wakeWord: "custom", wakePhrase: "hey_domo")
        XCTAssertEqual("Hey Domo", manualWakePhrase(wake, own))
        let blank = ManualRoom(roomID: "kitchen", wakeWord: "alexa", wakePhrase: "   ")
        XCTAssertEqual("Alexa", manualWakePhrase(wake, blank), "a blank phrase falls to the names")
        let unknown = ManualRoom(roomID: "kitchen", wakeWord: "hey_domovoi")
        XCTAssertEqual("Hey Domovoi", manualWakePhrase(wake, unknown), "an unlisted slug is spelt out")
        XCTAssertNil(
            manualWakePhrase(wake, ManualRoom(roomID: "kitchen")),
            "a room with no detector answers to nothing"
        )
    }

    /// The default word is named while **any** room answers to it — the
    /// common house, where one room has been changed and the manual should
    /// still teach the word most people will use.
    func testTheDefaultWordIsNamedWhileAnyRoomUsesIt() {
        let rooms = [
            ManualRoom(roomID: "kitchen", roomLabel: "Kitchen", wakeWord: "hey_jarvis"),
            ManualRoom(roomID: "shed", wakeWord: "alexa"),
            ManualRoom(roomID: "loft", wakeWord: "alexa"),
        ]
        let info = manualWakeInfo(wake, rooms: rooms)
        XCTAssertEqual("Hey Jarvis", info.lead)
        XCTAssertEqual([ManualWakeOther(phrase: "Alexa", rooms: ["shed", "loft"])], info.others)
        XCTAssertEqual(
            "Some rooms answer to another word: \u{201C}Alexa\u{201D} in shed, loft.",
            manualWakeNote(wake, info)
        )
    }

    /// Only when every known room has moved off the default does the manual
    /// name another word — the most used one, ties broken alphabetically.
    func testTheMostUsedWordIsNamedWhenNobodyUsesTheDefault() {
        let rooms = [
            ManualRoom(roomID: "shed", wakeWord: "hey_mycroft"),
            ManualRoom(roomID: "loft", wakeWord: "alexa"),
            ManualRoom(roomID: "attic", wakeWord: "alexa"),
        ]
        let info = manualWakeInfo(wake, rooms: rooms)
        XCTAssertEqual("Alexa", info.lead, "two rooms to one")
        XCTAssertEqual(["Hey Mycroft"], info.others.map(\.phrase))

        let tied = [
            ManualRoom(roomID: "shed", wakeWord: "hey_mycroft"),
            ManualRoom(roomID: "loft", wakeWord: "alexa"),
        ]
        XCTAssertEqual(
            "Alexa",
            manualWakeInfo(wake, rooms: tied).lead,
            "a tie is broken by the phrase, so the sentence is the same every time it is drawn"
        )
    }

    func testTheWakeNoteIsAbsentUntilThereIsASecondWord() {
        XCTAssertNil(
            manualWakeNote(wake, manualWakeInfo(wake, rooms: nil)),
            "nothing read yet: the slot draws nothing, not an empty box"
        )
        let oneWord = [
            ManualRoom(roomID: "kitchen", wakeWord: "hey_jarvis"),
            ManualRoom(roomID: "shed", wakeWord: "hey_jarvis"),
        ]
        XCTAssertNil(manualWakeNote(wake, manualWakeInfo(wake, rooms: oneWord)))
        XCTAssertEqual(
            "Hey Jarvis",
            manualWakeInfo(wake, rooms: []).lead,
            "a house with no rooms is taught the out-of-the-box word"
        )
        XCTAssertEqual(
            "Hey Domo",
            manualWakeInfo(ManualWakeWord(default: "hey_domo", names: [:]), rooms: nil).lead,
            "a document whose default is not in its own names list still words the line"
        )
    }

    /// Two words besides the lead, joined by the document's `note_join`.
    func testTheNoteNamesEveryOtherWordMostUsedFirst() {
        let rooms = [
            ManualRoom(roomID: "kitchen", wakeWord: "hey_jarvis"),
            ManualRoom(roomID: "shed", wakeWord: "hey_mycroft"),
            ManualRoom(roomID: "loft", wakeWord: "alexa"),
            ManualRoom(roomID: "attic", wakeWord: "alexa"),
        ]
        let info = manualWakeInfo(wake, rooms: rooms)
        XCTAssertEqual(["Alexa", "Hey Mycroft"], info.others.map(\.phrase))
        XCTAssertEqual(
            "Some rooms answer to another word: \u{201C}Alexa\u{201D} in loft, attic; "
                + "\u{201C}Hey Mycroft\u{201D} in shed.",
            manualWakeNote(wake, info)
        )
    }

    func testFillLeavesANameNobodySuppliedAsItWasWritten() {
        XCTAssertEqual("\u{201C}Hey Jarvis\u{201D}", manualFill(wake.quoted, ["phrase": "Hey Jarvis"]))
        XCTAssertEqual("{rooms}", manualFill("{rooms}", ["phrase": "x"]))
        XCTAssertEqual("a { b } c", manualFill("a { b } c", ["b": "no"]), "a brace with spaces is text")
        XCTAssertEqual("{}", manualFill("{}", [:]))
        XCTAssertEqual(
            "room",
            manualFill("{Zimmer}", ["Zimmer": "room"]),
            "ASCII letters, digits and underscores are a name — Java's \\w"
        )
        XCTAssertEqual("x_1 and y", manualFill("{a_1} and {b}", ["a_1": "x_1", "b": "y"]))
        XCTAssertEqual(
            "{комната}",
            manualFill("{комната}", ["комната": "room"]),
            "ICU's \\w would have substituted this one and Java's does not"
        )
    }

    // MARK: - links out

    func testTheDocsRefIsATagOnlyForARelease() {
        XCTAssertEqual("v1.0.0", manualDocRef("1.0.0"))
        XCTAssertEqual("v1.0.0-rc2", manualDocRef("1.0.0-rc2"))
        XCTAssertEqual("v10.20.30-alpha.1", manualDocRef("10.20.30-alpha.1"))
        XCTAssertEqual("main", manualDocRef(nil), "before boot captured a label")
        XCTAssertEqual(
            "main",
            manualDocRef("1.0.0 + 14 (abc1234)"),
            "past a release there is no tag to link to"
        )
        XCTAssertEqual("main", manualDocRef("1.0"))
        XCTAssertEqual("main", manualDocRef("1.0.0-"))
        XCTAssertEqual("main", manualDocRef("v1.0.0"))
        XCTAssertEqual("main", manualDocRef(" 1.0.0"))
        XCTAssertEqual(
            "main",
            manualDocRef("\u{0661}.\u{0660}.\u{0660}"),
            "ICU's \\d matches Arabic-Indic digits; this label is not a release and must not become a tag"
        )
    }

    func testTheDocsUrlFillsInTheRefAndStripsThePrefix() {
        let doc = ManualDoc(docBase: "https://example.com/blob/{ref}/")
        XCTAssertEqual(
            "https://example.com/blob/v1.0.0/docs/rooms.md#pi",
            manualDocURL(doc, version: "1.0.0", target: "doc:docs/rooms.md#pi")
        )
        XCTAssertEqual(
            "https://example.com/blob/main/README.md",
            manualDocURL(doc, version: nil, target: "README.md"),
            "a target passed without its prefix works too"
        )
    }

    func testTheDashboardUrlIsTheServersOwn() {
        XCTAssertEqual(
            "http://10.0.0.42:6369/#manual",
            manualDashboardURL("http://10.0.0.42:6369", hash: "#manual")
        )
        XCTAssertEqual(
            "http://10.0.0.42:6369/#manual",
            manualDashboardURL("  http://10.0.0.42:6369///  ", hash: "manual"),
            "a trailing slash and a hash without its # both land on one address"
        )
        XCTAssertNil(manualDashboardURL(nil, hash: "#manual"), "no server, so no button")
        XCTAssertNil(manualDashboardURL("   ", hash: "#manual"))
    }

    // MARK: - the checklist

    private func checklistDocument() -> ManualDoc {
        ManualDoc(
            start: ManualStart(checklist: [
                ManualCheck(id: "claim-admin", doneWhen: "admin_claimed", platforms: ["dashboard"],
                            text: "Claim the admin account"),
                ManualCheck(id: "first-room", doneWhen: "rooms", platforms: ["dashboard", "app"],
                            text: "Add your first room"),
                ManualCheck(id: "wake-word", doneWhen: "room_heard", platforms: ["dashboard", "app"],
                            text: "Say the wake word"),
                ManualCheck(id: "introduce", doneWhen: "people", platforms: ["dashboard", "app"],
                            text: "Introduce yourself"),
                ManualCheck(id: "music", doneWhen: "music", platforms: ["dashboard", "app"],
                            text: "Add your music"),
                ManualCheck(id: "app", doneWhen: "phone_app", platforms: ["dashboard"],
                            text: "Put the app on your phones",
                            appText: "Pair this phone", appDoneWhen: "this_phone_paired"),
            ]),
            topics: [ManualTopic(id: "t")]
        )
    }

    func testTheChecklistShowsTheAppsLinesAndTheAppsWordingForThePhone() {
        let lines = manualAppChecklist(checklistDocument(), ManualSignals())
        XCTAssertEqual(
            ["first-room", "wake-word", "introduce", "music", "app"],
            lines.map(\.id),
            "a dashboard-only line with no app wording is dropped; the pairing line is kept"
        )
        XCTAssertEqual(
            "Pair this phone",
            lines.last?.text,
            "the app's own wording, not the dashboard's"
        )
        XCTAssertEqual(
            true,
            lines.last?.done,
            "the app is reading the line, so this phone is paired"
        )
        XCTAssertEqual(
            [nil, nil, nil, nil],
            lines.dropLast().map(\.done),
            "nothing read yet is four unknowns, and never four crosses"
        )
    }

    func testEachSignalTicksItsOwnLine() {
        let signals = ManualSignals(
            rooms: [ManualRoom(roomID: "kitchen")],
            roomHeard: false,
            people: 0,
            tracks: 412
        )
        let done = Dictionary(
            uniqueKeysWithValues: manualAppChecklist(checklistDocument(), signals)
                .map { ($0.id, $0.done) }
        )
        XCTAssertEqual(true, done["first-room"])
        XCTAssertEqual(false, done["wake-word"], "read, and the answer was no")
        XCTAssertEqual(false, done["introduce"])
        XCTAssertEqual(true, done["music"])

        let empty = ManualSignals(rooms: [], roomHeard: nil, people: nil, tracks: 0)
        let second = Dictionary(
            uniqueKeysWithValues: manualAppChecklist(checklistDocument(), empty)
                .map { ($0.id, $0.done) }
        )
        XCTAssertEqual(false, second["first-room"], "a house with no rooms is not done")
        XCTAssertEqual(
            Bool?.none,
            second["wake-word"] ?? nil,
            "a read that has not answered is unknown, not undone"
        )
        XCTAssertEqual(false, second["music"])
    }

    // MARK: - search

    private func searchDocument() -> ManualDoc {
        ManualDoc(
            topics: [
                ManualTopic(id: "music-and-media", title: "Music and media"),
                ManualTopic(id: "rooms-and-devices", title: "Rooms and devices"),
            ],
            pieces: [
                ManualPiece(id: "playlists", topic: "music-and-media", title: "Playlists",
                            subtitle: "make a list", keywords: ["queue", "shuffle"]),
                ManualPiece(id: "radio", topic: "music-and-media", title: "Internet radio",
                            subtitle: "stations", keywords: ["stream"], plugin: "radio"),
                ManualPiece(id: "pi-room", topic: "rooms-and-devices", title: "A Raspberry Pi room",
                            subtitle: "a speaker and a mic", keywords: ["satellite", "queue sheet"]),
                ManualPiece(id: "pi-room-alias", topic: "rooms-and-devices", title: "Pi",
                            keywords: ["queue"], aliasOf: "pi-room"),
            ]
        )
    }

    private var everything: Set<String> {
        Set(searchDocument().pieces.map(\.id))
    }

    func testEveryWordOfTheQueryHasToHitSomething() {
        let doc = searchDocument()
        XCTAssertEqual(
            ["playlists"],
            searchManual(doc, visible: everything, query: "playlists shuffle").map(\.id)
        )
        XCTAssertEqual(
            [],
            searchManual(doc, visible: everything, query: "playlists satellite").map(\.id),
            "two words from two different pieces match neither"
        )
        XCTAssertEqual([], searchManual(doc, visible: everything, query: "   ").map(\.id))
    }

    func testATitleBeatsAKeywordAndTiesGoByTitle() {
        let doc = searchDocument()
        XCTAssertEqual(
            ["pi-room", "playlists"],
            searchManual(doc, visible: everything, query: "queue").map(\.id),
            "both score 2 on a keyword prefix (queue, queue sheet), so the tie is the title: "
                + "\"A Raspberry Pi room\" before \"Playlists\""
        )
        XCTAssertEqual(
            ["playlists"],
            searchManual(doc, visible: everything, query: "playlists queue").map(\.id),
            "a title prefix scores 4 where the keyword scored 2"
        )
        XCTAssertEqual(
            ["pi-room"],
            searchManual(doc, visible: everything, query: "raspberry").map(\.id),
            "a word inside the title and nowhere else"
        )
        XCTAssertEqual(
            ["playlists"],
            searchManual(doc, visible: everything, query: "list").map(\.id),
            "found in the subtitle, which no title contains"
        )
    }

    /// The gate the plugin pieces sit behind: a piece whose plugin is not
    /// installed cannot be found any more than it can be seen.
    func testAnInvisiblePieceIsNotFound() {
        let doc = searchDocument()
        let without = everything.subtracting(["radio"])
        XCTAssertEqual([], searchManual(doc, visible: without, query: "radio").map(\.id))
        XCTAssertEqual(["radio"], searchManual(doc, visible: everything, query: "radio").map(\.id))
    }

    func testAnAliasIsNotASecondResult() {
        let doc = searchDocument()
        let found = searchManual(doc, visible: everything, query: "pi")
        XCTAssertFalse(
            found.contains { $0.id == "pi-room-alias" },
            "an alias is another name for a page, not another page"
        )
    }

    func testTheResultListIsBounded() {
        let pieces = (0..<20).map {
            ManualPiece(id: "p\($0)", topic: "t", title: "Rooms \($0)")
        }
        let doc = ManualDoc(topics: [ManualTopic(id: "t", title: "T")], pieces: pieces)
        let visible = Set(pieces.map(\.id))
        XCTAssertEqual(8, searchManual(doc, visible: visible, query: "rooms").count)
        XCTAssertEqual(3, searchManual(doc, visible: visible, query: "rooms", max: 3).count)
    }

    // MARK: - the handlers

    private func sayDocument() -> ManualDoc {
        ManualDoc(
            handlerNeedsFeature: ["double_check": "ai", "chat_mode": "chat"],
            start: ManualStart(blocks: [
                ManualBlock(type: "p", text: "first"),
                ManualBlock(type: "say", phrases: [
                    ManualPhrase(text: "what time is it?", handler: "clock"),
                    ManualPhrase(text: "it is nine", handler: "clock", then: true),
                ]),
            ]),
            topics: [ManualTopic(id: "t")],
            pieces: [
                ManualPiece(id: "p", topic: "t", blocks: [
                    ManualBlock(type: "say", phrases: [
                        ManualPhrase(text: "what time is it?", handler: "clock"),
                        ManualPhrase(text: "set a timer for \u{2026} minutes", handler: "timer"),
                        ManualPhrase(text: "set a timer for ten minutes", handler: "timer"),
                        ManualPhrase(text: "play the news", handler: "news", pending: "soon"),
                    ]),
                ]),
            ]
        )
    }

    func testTheManualsOwnPhrasesPerHandlerSkipTheOnesNobodyCanSay() {
        let by = manualPhrasesByHandler(sayDocument())
        XCTAssertEqual(["what time is it?"], by["clock"], "a reply is not a phrase, and not a duplicate")
        XCTAssertEqual(
            ["set a timer for ten minutes"],
            by["timer"],
            "a phrase written with an ellipsis is a shape, not a sentence to repeat"
        )
        XCTAssertNil(by["news"], "a pending phrase: this server may not answer to it")
    }

    func testHomesFirstRunHintIsStartHeresFirstPhrase() {
        XCTAssertEqual("what time is it?", manualStartPhrase(sayDocument()))
        XCTAssertNil(manualStartPhrase(nil))
        XCTAssertNil(
            manualStartPhrase(ManualDoc(start: ManualStart(blocks: [
                ManualBlock(type: "say", phrases: [
                    ManualPhrase(text: "later", pending: "soon"),
                ]),
            ]))),
            "a page whose only phrase is pending offers nothing"
        )
    }

    func testAHandlerIsListedOnlyWhileItsFeatureIsOn() {
        let doc = sayDocument()
        let all = ["clock", "double_check", "chat_mode"]
        XCTAssertEqual(
            ["clock", "double_check"],
            manualListedHandlers(doc, all, features: ["ai": true, "chat": false])
        )
        XCTAssertEqual(
            ["clock"],
            manualListedHandlers(doc, all, features: [:]),
            "a handler in handler_needs_feature is listed only while the feature is TRUE — "
                + "the opposite asymmetry to shownManualPhrases, and the Kotlin's"
        )
    }

    // MARK: - a say block's rows

    func testAPhraseKeepsItsOwnReplyAfterTheFeatureFilter() {
        let phrases = [
            ManualPhrase(text: "what time is it?"),
            ManualPhrase(text: "it is nine", then: true),
            ManualPhrase(text: "why is the sky blue?", needs: "ai"),
            ManualPhrase(text: "because of the air", then: true, needs: "ai"),
            ManualPhrase(text: "play jazz"),
        ]
        let on = manualSayRows(phrases, features: ["ai": true])
        XCTAssertEqual(["what time is it?", "why is the sky blue?", "play jazz"], on.map(\.phrase.text))
        XCTAssertEqual(["it is nine", "because of the air", nil], on.map { $0.then?.text })

        let off = manualSayRows(phrases, features: ["ai": false])
        XCTAssertEqual(["what time is it?", "play jazz"], off.map(\.phrase.text))
        XCTAssertEqual(
            ["it is nine", nil],
            off.map { $0.then?.text },
            "the dropped phrase's reply is not inherited by the one above it"
        )
    }

    // MARK: - a room's line

    /// The Android defect this port does not copy: the server carries the
    /// track under `now_playing.song`, and Android decodes
    /// `now_playing.title`, so its rooms slot has never said what is playing.
    func testARoomsLineNamesWhatIsPlaying() throws {
        let body = """
        {"room_id":"kitchen","room_label":"Kitchen","status":"online","wake_word":"alexa",
         "now_playing":{"state":"play","song":{"title":"Blue in Green","file":"jazz/01.flac"}}}
        """
        let room = try JSONDecoder().decode(ManualRoom.self, from: Data(body.utf8))
        XCTAssertEqual("Kitchen", room.name)
        XCTAssertEqual("Blue in Green", room.song)
        XCTAssertEqual(
            "online \u{00B7} \u{201C}Alexa\u{201D} \u{00B7} Blue in Green",
            manualRoomLine(room, wake)
        )

        let fileOnly = """
        {"room_id":"shed","status":"offline","now_playing":{"song":{"file":"jazz/02.flac"}}}
        """
        let second = try JSONDecoder().decode(ManualRoom.self, from: Data(fileOnly.utf8))
        XCTAssertEqual("jazz/02.flac", second.song, "no title: the file names the track")
        XCTAssertEqual(
            "offline \u{00B7} jazz/02.flac",
            manualRoomLine(second, wake),
            "a room with no detector contributes no word, and the line closes up"
        )
        XCTAssertEqual("shed", second.name, "no label: the id is the name")
    }

    // MARK: - which links are links

    private let visible = RouteVisibility(
        capabilities: Capabilities(features: ["ai": true]),
        sharedScreen: false,
        phoneCanChat: false
    )

    func testARouteTheAppCannotOpenIsNotALink() {
        XCTAssertTrue(
            manualRouteOpens(ManualRoute(app: ManualAppRoute(screen: "Music")), visible)
        )
        XCTAssertTrue(
            manualRouteOpens(
                ManualRoute(dashboard: "#settings/voices", app: ManualAppRoute(dashboard: true)),
                visible
            ),
            "a route that opens the dashboard always opens"
        )
        XCTAssertFalse(
            manualRouteOpens(ManualRoute(dashboard: "#x", app: ManualAppRoute()), visible),
            "no app screen and not a dashboard route: there is nowhere to go"
        )
        XCTAssertTrue(
            manualRouteOpens(ManualRoute(app: ManualAppRoute(screen: "Holodeck")), visible),
            "a document newer than this build names a screen it has not got: the link stays alive"
        )
    }

    func testAScreenThisServerDoesNotOfferIsNotALink() {
        let aiOff = RouteVisibility(
            capabilities: Capabilities(features: ["ai": false]),
            sharedScreen: false,
            phoneCanChat: false
        )
        let chat = ManualRoute(dashboard: "#chat", app: ManualAppRoute(screen: "Chat"))
        XCTAssertTrue(manualRouteOpens(chat, visible))
        XCTAssertFalse(
            manualRouteOpens(chat, aiOff),
            "with the house's AI off and no engine on this phone, a Chat link would land on Home"
        )
        let stations = ManualRoute(app: ManualAppRoute(screen: "Stations"))
        XCTAssertFalse(
            manualRouteOpens(stations, visible),
            "a plugin screen the manifest does not list"
        )
        XCTAssertTrue(
            manualRouteOpens(
                stations,
                RouteVisibility(
                    capabilities: Capabilities(plugins: [
                        CapabilityPlugin(slug: "stations", appCapabilities: [capStations]),
                    ]),
                    sharedScreen: false,
                    phoneCanChat: false
                )
            )
        )
    }
}
