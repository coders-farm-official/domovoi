import XCTest
import Synchronization
@testable import DomovoiKit

/// Private chats on the iPhone (1.5.0, PRIVATE-CHATS.md §2.5–§2.7): the
/// thread's access fields decide what is offered (a control the server would
/// refuse is not), a new chat says what it will be and asks for exactly that,
/// a shared screen gets no private option, a 404 on an open chat is "isn't
/// shared with this device any more" and closes it, and the words are §2.5's.
/// The twin of Android's `ChatPrivacyTest.kt`.
@MainActor
final class ChatPrivacyTests: XCTestCase {

    private let server = "http://10.0.0.42:6369"

    override func setUp() {
        super.setUp()
        StubURLProtocol.reset()
    }

    /// What the house answers, and what it was asked.
    private final class House: Sendable {
        let threads = Shared<[String]>([])
        let messages = Shared<(status: Int, body: String)>((200, #"{"messages":[]}"#))
        let create = Shared<(status: Int, body: String)>((200, #"{"id":77,"title":null,"shared":false,"mine":true,"can_write":true,"can_share":true,"can_delete":true}"#))
        let patch = Shared<(status: Int, body: String)>((200, "{}"))
        let asked = Shared<[(line: String, body: String, key: String?)]>([])

        func install() {
            StubURLProtocol.dispatch { [self] request in
                guard let url = request.url, let parts = URLComponents(url: url, resolvingAgainstBaseURL: false)
                else { return .json("{}", status: 404) }
                if parts.path == "/api/health" {
                    let challenge = (parts.queryItems ?? []).first { $0.name == "challenge" }?.value
                    return .json((try? TestIdentity.real.healthBody(challenge: challenge)) ?? "{}")
                }
                let method = request.httpMethod ?? "GET"
                // The recorded copy has the body bytes (the stub reads the stream).
                let body = StubURLProtocol.recorded.last?.httpBody.flatMap { String(data: $0, encoding: .utf8) } ?? ""
                let line = parts.query.map { "\(method) \(parts.path)?\($0)" } ?? "\(method) \(parts.path)"
                asked.mutate { $0.append((line, body, request.value(forHTTPHeaderField: deviceKeyHeader))) }
                switch (method, parts.path) {
                case ("GET", "/api/chat/threads"):
                    return .json(#"{"threads":[\#(threads.current.joined(separator: ","))]}"#)
                case ("GET", "/api/chat/models"):
                    return .json("{}")
                case ("POST", "/api/chat/threads"):
                    return .json(create.current.body, status: create.current.status)
                default:
                    break
                }
                if method == "GET", parts.path.hasSuffix("/messages") {
                    return .json(messages.current.body, status: messages.current.status)
                }
                if method == "PATCH" { return .json(patch.current.body, status: patch.current.status) }
                if method == "POST", parts.path.hasSuffix("/messages") {
                    return .json("event: done\ndata: {\"id\":5,\"role\":\"assistant\",\"content\":\"ok\"}\n\n",
                                 headers: ["Content-Type": "text/event-stream"])
                }
                return .json("{}")
            }
        }
    }

    private func rig(
        house: House = House(),
        key: Bool = true,
        sharedScreen: Shared<Bool> = Shared(false),
        note: ChatPrivateNoteBook = ChatPrivateNoteBook()
    ) -> (ChatModel, House) {
        house.install()
        let store = InMemoryShellStore(serverURL: server, token: "household-abc")
        store.trustServer(server, identity: TestIdentity.real.pin)
        let keys = DeviceKeyStore()
        if key { keys.rememberDeviceKey("dk-chat-GGGGGGGGGGGGGGGGGGGGGGGGGGGGGGGGGGG", for: server) }
        let container = AppContainer(
            store: store,
            vault: TokenVault(items: InMemoryKeychain()),
            lock: HouseholdLock(),
            clock: { 0 },
            configuration: StubURLProtocol.session(timeout: 1),
            deviceKeys: keys
        )
        let model = ChatModel(
            in: container, deviceID: "ios-1",
            pins: ChatPinBook(place: SavedStateBox()),
            sharedScreen: { sharedScreen.current },
            onSharedScreen: { sharedScreen.set(true) },
            privateNote: note
        )
        return (model, house)
    }

    private func settleUntil(_ done: @MainActor () -> Bool, _ what: String, file: StaticString = #filePath, line: UInt = #line) async {
        for _ in 0..<settleYieldLimit {
            if done() { return }
            await Task.yield()
        }
        XCTFail("\(what) never settled", file: file, line: line)
    }

    private static func thread(_ id: Int64, _ fields: String) -> String {
        #"{"id":\#(id),"title":"T\#(id)","updated_at":"2026-11-25T10:00:00+00:00","message_count":1,\#(fields)}"#
    }

    // MARK: - the can_* flags hide the controls they deny

    func testTheCanFlagsHideTheControlsTheyDeny() async {
        let house = House()
        house.threads.set([
            Self.thread(1, #""shared":true,"mine":false,"can_write":true,"can_share":false,"can_delete":false"#),
            Self.thread(2, #""shared":false,"mine":false,"can_write":false,"can_share":true,"can_delete":true,"admin_view":true,"owner_name":"Sam's tablet""#),
            Self.thread(3, #""shared":false,"mine":true,"can_write":true,"can_share":true,"can_delete":true"#),
        ])
        let (model, _) = rig(house: house)
        model.start()
        await settleUntil({ model.rows.count == 3 }, "the list")
        let byID = Dictionary(uniqueKeysWithValues: model.rows.map { ($0.id, $0) })
        // Another device's shared chat: no delete.
        XCTAssertFalse(model.actions(for: byID["1"]!).contains(ChatStrings.actionDelete))
        XCTAssertTrue(model.actions(for: byID["1"]!).contains(ChatStrings.actionRename))
        // An admin in another device's private chat: no writes.
        let admin = model.actions(for: byID["2"]!)
        XCTAssertFalse(admin.contains(ChatStrings.actionRename))
        XCTAssertFalse(admin.contains(ChatStrings.actionArchive))
        XCTAssertTrue(admin.contains(ChatStrings.actionDelete))
        XCTAssertEqual(byID["2"]?.subtitle?.hasPrefix("Sam's tablet · "), true)
        // Mine: everything.
        XCTAssertEqual(model.actions(for: byID["3"]!).count, 5)
        XCTAssertEqual(model.deleteText(byID["3"]!), "Delete \"T3\"? This can't be undone.")
        XCTAssertEqual(model.deleteText(byID["1"]!), "Delete \"T1\"? Deletes it for everyone in the house. This can't be undone.")
        XCTAssertTrue(model.offersScope, "an owner_name in the list: this phone is an admin")

        // Opened, the admin's view: banner, no composer, the switch.
        model.open(byID["2"]!)
        XCTAssertEqual(model.adminBanner,
                       "You're reading Sam's tablet's private chat as an admin. You can share it or delete it, not write in it.")
        XCTAssertFalse(model.composerOffered)
        XCTAssertFalse(model.canSend)
        XCTAssertTrue(model.showsShareSwitch)
        XCTAssertEqual(model.privacyLine, ChatPrivacyStrings.privateLine)

        // Another device's shared chat: composer, no switch.
        model.closeChat()
        model.open(byID["1"]!)
        XCTAssertTrue(model.composerOffered)
        XCTAssertFalse(model.showsShareSwitch)
        XCTAssertNil(model.adminBanner)
        XCTAssertEqual(model.privacyLine, ChatPrivacyStrings.sharedLine)
    }

    func testAnOlderServerDeniesNothingAndSaysNothing() async {
        let house = House()
        house.threads.set([#"{"id":4,"title":"Old","message_count":1}"#])
        let (model, _) = rig(house: house, key: false)
        model.start()
        await settleUntil({ model.rows.count == 1 }, "the list")
        model.open(model.rows[0])
        XCTAssertNil(model.privacyLine)
        XCTAssertFalse(model.showsShareSwitch)
        XCTAssertTrue(model.composerOffered)
        XCTAssertFalse(model.offersScope)
        XCTAssertEqual(model.actions(for: model.rows[0]).count, 5)
    }

    // MARK: - a new chat asks for what it says

    func testANewChatIsPrivateWithAKeyAndAsksForExactlyThat() async {
        let (model, house) = rig()
        model.startNewChat()
        XCTAssertEqual(model.privacyLine, ChatPrivacyStrings.privateLine)
        XCTAssertTrue(model.showsShareSwitch)
        model.send("hello")
        await settleUntil({ house.asked.current.contains { $0.line == "POST /api/chat/threads" } }, "the create")
        let create = house.asked.current.first { $0.line == "POST /api/chat/threads" }
        XCTAssertEqual(create?.body, #"{"shared":false}"#)
        XCTAssertEqual(create?.key, "dk-chat-GGGGGGGGGGGGGGGGGGGGGGGGGGGGGGGGGGG", "the create carries the device key")
    }

    func testTheSwitchBeforeTheFirstMessageMakesItShared() async {
        let (model, house) = rig()
        model.startNewChat()
        model.shareSwitched(true)
        XCTAssertEqual(model.privacyLine, ChatPrivacyStrings.sharedLine)
        model.send("hello")
        await settleUntil({ house.asked.current.contains { $0.line == "POST /api/chat/threads" } }, "the create")
        XCTAssertEqual(house.asked.current.first { $0.line == "POST /api/chat/threads" }?.body, #"{"shared":true}"#)
    }

    func testAKeylessPhoneLeavesItToTheServer() async {
        let (model, house) = rig(key: false)
        model.startNewChat()
        XCTAssertNil(model.privacyLine, "the household's, as before")
        XCTAssertFalse(model.showsShareSwitch)
        model.send("hello")
        await settleUntil({ house.asked.current.contains { $0.line == "POST /api/chat/threads" } }, "the create")
        XCTAssertEqual(house.asked.current.first { $0.line == "POST /api/chat/threads" }?.body, "{}")
    }

    // MARK: - shared screens

    func testASharedScreenGetsNoPrivateOptionAndNoSwitch() async {
        let flag = Shared(true)
        let (model, house) = rig(sharedScreen: flag)
        model.startNewChat()
        XCTAssertEqual(model.privacyLine, ChatPrivacyStrings.sharedScreenLine)
        XCTAssertFalse(model.showsShareSwitch)
        model.send("hello")
        await settleUntil({ house.asked.current.contains { $0.line == "POST /api/chat/threads" } }, "the create")
        XCTAssertEqual(house.asked.current.first { $0.line == "POST /api/chat/threads" }?.body, #"{"shared":true}"#)
    }

    func testA409SharedScreenTurnsTheChipAndSaysWhy() async {
        let flag = Shared(false)
        let house = House()
        house.create.set((409, #"{"detail":"chats on a shared screen are shared with the household; this one can't be private","code":"shared_screen"}"#))
        let (model, _) = rig(house: house, sharedScreen: flag)
        model.startNewChat()
        model.send("hello")
        await settleUntil({ !model.sending }, "the refused create")
        XCTAssertTrue(flag.current, "the shell learns it is a shared screen")
        XCTAssertEqual(model.privacyLine, ChatPrivacyStrings.sharedScreenLine)
        XCTAssertFalse(model.showsShareSwitch, "no private option any more")
        XCTAssertEqual(model.transcript.messages.last?.error, ChatPrivacyStrings.sharedScreenLine)
    }

    // MARK: - losing access

    func testAFourOhFourOnAnUnsharedChatSaysSoAndCloses() async {
        let house = House()
        house.threads.set([Self.thread(5, #""shared":true,"mine":false,"can_write":true,"can_share":false,"can_delete":false"#)])
        let (model, _) = rig(house: house)
        model.start()
        await settleUntil({ model.rows.count == 1 }, "the list")
        house.messages.set((404, #"{"detail":"thread not found"}"#))
        model.open(model.rows[0])
        await settleUntil({ !model.conversationOpen }, "the chat closing")
        XCTAssertEqual(model.toast, ChatPrivacyStrings.goneLine)
        XCTAssertTrue(ChatPrivacyRules.isGone(status: 404, body: #"{"detail":"thread not found"}"#))
        XCTAssertFalse(ChatPrivacyRules.isGone(status: 404, body: #"{"detail":"no such lookup card"}"#))
    }

    func testUnsharingAsksFirstAndSendsFalse() async {
        let house = House()
        house.threads.set([Self.thread(6, #""shared":true,"mine":true,"can_write":true,"can_share":true,"can_delete":true"#)])
        house.patch.set((200, #"{"ok":true,"id":6,"shared":false,"mine":true,"can_write":true,"can_share":true,"can_delete":true}"#))
        let (model, _) = rig(house: house)
        model.start()
        await settleUntil({ model.rows.count == 1 }, "the list")
        model.open(model.rows[0])
        XCTAssertTrue(model.showsShareSwitch)
        model.shareSwitched(false)
        XCTAssertTrue(model.unsharing, "turning sharing off asks first")
        XCTAssertFalse(house.asked.current.contains { $0.line.hasPrefix("PATCH") })
        model.confirmUnshare()
        await settleUntil({ model.privacyLine == ChatPrivacyStrings.privateLine }, "the unshare")
        XCTAssertEqual(house.asked.current.first { $0.line.hasPrefix("PATCH") }?.body, #"{"shared":false}"#)
        XCTAssertEqual(model.toast, ChatPrivacyStrings.unsharedToast)
    }

    // MARK: - the note, the scope, the words

    func testTheTrulyPrivateNoteShowsOnceAndOnlyWhereThePhonesChatIs() {
        let book = ChatPrivateNoteBook()
        let (model, _) = rig(note: book)
        model.startNewChat()
        model.showPrivateNoteIfDue()
        XCTAssertNil(model.privateNote, "no phone chat here: no note")
        model.phoneChatHere = true
        model.showPrivateNoteIfDue()
        XCTAssertEqual(model.privateNote, "Want it truly private? Chat with Domovoi on this phone: it never leaves the phone.")
        model.closeChat()
        model.startNewChat()
        model.showPrivateNoteIfDue()
        XCTAssertNil(model.privateNote, "once")
    }

    func testTheAdminScopeAsksForAll() async {
        let house = House()
        house.threads.set([Self.thread(1, #""shared":true,"mine":true,"owner_name":"Pixel""#)])
        let (model, _) = rig(house: house)
        model.start()
        await settleUntil({ model.offersScope }, "the list")
        model.showAllChats(true)
        await settleUntil({ house.asked.current.contains { $0.line == "GET /api/chat/threads?scope=all" } }, "the scope=all read")
        XCTAssertEqual(ChatPrivacyRules.threadsPath(archived: true, all: true), "/api/chat/threads?archived=true&scope=all")
    }

    func testTheStringsAreTheDesignNotes() {
        XCTAssertEqual(ChatPrivacyStrings.privateLine, "Private · only this device and the house's admins can see it")
        XCTAssertEqual(ChatPrivacyStrings.shareSwitch, "Share with household")
        XCTAssertEqual(ChatPrivacyStrings.shareHelp, "Everyone's phones and browsers in the house can read and add to it.")
        XCTAssertEqual(ChatPrivacyStrings.unshareAsk,
                       "Stop sharing? Other phones and browsers won't see this chat any more, including anything they wrote in it.")
        XCTAssertEqual(ChatPrivacyStrings.sharedLine, "Shared with the household")
        XCTAssertEqual(ChatPrivacyStrings.sharedScreenLine, "Shared · chats on this screen are shared with the household")
        XCTAssertEqual(ChatPrivacyStrings.scopeMine, "My chats")
        XCTAssertEqual(ChatPrivacyStrings.scopeAll, "All chats in the house")
        XCTAssertEqual(ChatPrivacyStrings.savePrivateAsk, "Saves a copy to Documents, where the household can see it.")
        XCTAssertEqual(ChatPrivacyStrings.goneLine, "This chat isn't shared with this device any more.")
    }
}
