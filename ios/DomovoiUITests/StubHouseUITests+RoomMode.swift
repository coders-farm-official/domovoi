import XCTest

/// Room mode on the iPhone (I7, ROOMMODE-MAP §7.2): setup, the two ways in,
/// and the room screen against the stub house's room routes (R2's
/// `r2/stubhouse.patch`).
///
/// **Never the connection sheet, never a LAN sweep** (RULES): the setup
/// cases reach setup from the no-server shell's own line, and never press
/// **Become this room** on a real-network code — the engine would dial it.
/// The room cases launch straight into a room with `-domovoi-room` (in
/// memory, loopback allowed only under that flag) and `-domovoi-room-feed`
/// (quiet noise for a microphone, no prompts, fixed lock facts so no Face ID
/// or passcode sheet can stop the run).
///
/// Every case prints the tree on a miss.
extension StubHouseUITests {

    /// The stub's rooms and the words this file matches on, in one place
    /// (map §7.3). If R2's patch renames the rooms (to keep clear of the
    /// queues unit's music rooms), this enum is the only thing to change.
    enum RoomModeUnderTest {
        static let port = 6601
        /// The stub's server-identity fingerprint, from the header of R2's
        /// `r2/stubhouse.patch` (a fixed test seed: sha256 of
        /// "domovoi stub house room identity").
        static let fingerprint = "SHA256:BWNQsKOoufCmvpTu0RjoU4Gc3zrZuDi6L8WD+AbPKiQ"
        /// `ready`.
        static let ready = "Kitchen"
        /// `awaiting_approval` with the code below, until approved.
        static let awaiting = "Porch"
        static let awaitingCode = "482913"
        static let awaitingCodeShown = "482 913"
        /// `pairing_rejected`.
        static let rejected = "Garage"
        /// `approval_conflict`.
        static let conflict = "Attic"
        static let pairingKey = "r3-ui-test-pairing-key-0000000000000000000000"

        /// A real home-network code, for the paste cases only (never
        /// becomes a room: the engine would dial 192.168.1.20).
        static let homeCode =
            "domovoi-room:1?core=http%3A%2F%2F192.168.1.20%3A6370"
                + "&fp=SHA256%3A4gq%2BpNW%2FlnGijI3MZ4vGYczKK6r6YgmCylej3GSSoOA&room=Kitchen"
        static let homeServer = "Server: 192.168.1.20:6370"
        static let homeKey = "Server key: SHA256:4gq+pNW/lnGij…"
        static let wrongVersion = "domovoi-room:2?core=x"
        static let wrongVersionWhy = "this room code is version 2; this app understands 1"

        static let entry = "room-mode-entry"
        static let explainerTitle = "Use this device as a room"
        static let readToTheEnd = "Read to the end to continue."
        static let start = "Start room mode"
        static let notNow = "Not now"
        static let codeTitle = "The room code"
        static let scan = "Scan the room code"
        static let become = "Become this room"
        static let mute = "mute"
        static let unmute = "unmute"
        static let resume = "resume"
        static let checkNow = "check now"
        static let ring = "room-ring"
        static let chip = "room-mic-chip"
        static let detail = "room-detail"
        static let exit = "room-exit"
        static let exitTitle = "Exit room mode?"
        static let stayARoom = "Stay a room"
        static let micOff = "microphone off"
        static let say = "Say “Hey Jarvis”"
        static let noModels = "The server has no wake-word models yet"
        static let rejectedWords = "reset its pairing"

        static func payload(room: String) -> String {
            let fp = fingerprint
                .replacingOccurrences(of: ":", with: "%3A")
                .replacingOccurrences(of: "+", with: "%2B")
                .replacingOccurrences(of: "/", with: "%2F")
            return "domovoi-room:1?core=http%3A%2F%2F127.0.0.1%3A\(port)&fp=\(fp)&room=\(room)"
        }
    }

    // MARK: - getting there

    /// Prints the tree and fails, for a miss.
    @MainActor
    func roomModeMiss(_ app: XCUIApplication, _ what: String, file: StaticString = #filePath, line: UInt = #line) {
        print("=== ROOM MODE MISS: \(what) — tree follows ===")
        print(app.debugDescription)
        XCTFail(what, file: file, line: line)
    }

    @MainActor
    func roomModeWait(_ app: XCUIApplication, _ element: XCUIElement, _ what: String, timeout: TimeInterval = 20,
                      file: StaticString = #filePath, line: UInt = #line) -> Bool {
        if element.waitForExistence(timeout: timeout) { return true }
        roomModeMiss(app, what, file: file, line: line)
        return false
    }

    /// The phone is a room from launch: the stub's room `room`, in memory.
    @MainActor
    func roomModeLaunch(room: String, alsoAHousehold: Bool = false) -> XCUIApplication {
        control("room-reset", "{}", port: RoomModeUnderTest.port, what: "the rooms back to their seed")
        let app = freshApp()
        app.launchArguments = [
            "-domovoi-room", RoomModeUnderTest.payload(room: room),
            "-domovoi-room-key", RoomModeUnderTest.pairingKey,
            "-domovoi-room-feed",
        ]
        if alsoAHousehold {
            app.launchArguments += [
                "-domovoi-server", "127.0.0.1:\(RoomModeUnderTest.port)",
                "-domovoi-token", Self.token,
            ]
        }
        app.launchArguments += ["-UIPreferredContentSizeCategoryName", Self.ordinaryTypeSize]
        app.launch()
        XCTAssertTrue(app.wait(for: .runningForeground, timeout: 30))
        return app
    }

    /// Setup, opened from the no-server shell's line (D11). Nothing is kept:
    /// `-domovoi-room-feed` enrols into memory and asks for nothing.
    @MainActor
    func roomModeSetup() -> XCUIApplication? {
        let app = launch(server: false, args: ["-domovoi-room-feed"])
        let entry = app.buttons[RoomModeUnderTest.entry]
        guard roomModeWait(app, entry, "the no-server shell does not offer “Use this device as a room”") else { return nil }
        entry.tap()
        let start = app.buttons[RoomModeUnderTest.start]
        guard roomModeWait(app, start, "setup did not open on the explainer") else { return nil }
        return app
    }

    /// Past the explainer (scrolled to its end) to the room code.
    @MainActor
    func roomModeReachTheCode(_ app: XCUIApplication) -> Bool {
        let start = app.buttons[RoomModeUnderTest.start]
        var swipes = 0
        while !start.isEnabled && swipes < 10 {
            app.swipeUp()
            swipes += 1
        }
        guard start.isEnabled else {
            roomModeMiss(app, "Start room mode never enabled after \(swipes) swipes to the end")
            return false
        }
        start.tap()
        return roomModeWait(app, app.staticTexts[RoomModeUnderTest.codeTitle], "Start did not reach the room code")
    }

    // MARK: - setup

    @MainActor
    func testRoomModeSetup_ExplainerStartsOnlyAtTheEnd() throws {
        guard let app = roomModeSetup() else { return }
        evidence(app, "roommode-explainer")
        let start = app.buttons[RoomModeUnderTest.start]
        let lastPoint = app.staticTexts["6. How to leave"]
        if start.isEnabled {
            // A screen tall enough for all six points: the end is on screen.
            if !lastPoint.isHittable {
                roomModeMiss(app, "Start room mode is enabled before the last point is on screen")
            }
        } else {
            if !app.staticTexts[RoomModeUnderTest.readToTheEnd].exists {
                roomModeMiss(app, "Start is disabled and nothing says why")
            }
            start.tap()
            if app.staticTexts[RoomModeUnderTest.codeTitle].waitForExistence(timeout: 2) {
                roomModeMiss(app, "a tap on a disabled Start still went on")
            }
        }
        guard roomModeReachTheCode(app) else { return }
        evidence(app, "roommode-code")
    }

    @MainActor
    func testRoomModeSetup_APastedCodeShowsServerKeyAndRoom() throws {
        guard let app = roomModeSetup(), roomModeReachTheCode(app) else { return }
        let field = app.textFields["room-code-field"]
        guard roomModeWait(app, field, "no field to paste the room code into") else { return }
        field.tap()
        field.typeText(RoomModeUnderTest.homeCode)
        guard roomModeWait(app, app.staticTexts[RoomModeUnderTest.homeServer], "the server is not shown back") else { return }
        if !app.staticTexts[RoomModeUnderTest.homeKey].exists {
            roomModeMiss(app, "the server key's first 20 characters are not shown")
        }
        let name = app.textFields["room-name-field"]
        if !name.exists || (name.value as? String) != "Kitchen" {
            roomModeMiss(app, "the room name is not shown back, editable: \(String(describing: name.value))")
        }
        let become = app.buttons[RoomModeUnderTest.become]
        if !become.exists || !become.isEnabled {
            roomModeMiss(app, "Become this room is not offered for a good code")
        }
        evidence(app, "roommode-pasted")
        // Never pressed: this code names a real-network address.
    }

    @MainActor
    func testRoomModeSetup_AWrongCodeSaysWhy() throws {
        guard let app = roomModeSetup(), roomModeReachTheCode(app) else { return }
        let field = app.textFields["room-code-field"]
        guard roomModeWait(app, field, "no field to paste the room code into") else { return }
        field.tap()
        field.typeText(RoomModeUnderTest.wrongVersion)
        guard roomModeWait(app, startingWith(app, RoomModeUnderTest.wrongVersionWhy),
                           "a code of another version does not say why it was refused") else { return }
        if app.buttons[RoomModeUnderTest.become].exists {
            roomModeMiss(app, "Become this room is offered for a refused code")
        }
        evidence(app, "roommode-wrong-code")
    }

    @MainActor
    func testRoomModeSetup_ScannerIsOfferedNotOpened() throws {
        guard let app = roomModeSetup(), roomModeReachTheCode(app) else { return }
        // Asserted, never tapped: CI has no camera to open.
        _ = roomModeWait(app, app.buttons[RoomModeUnderTest.scan], "the scanner is not offered")
        evidence(app, "roommode-scanner-offered")
    }

    // MARK: - the ways in

    @MainActor
    func testRoomModeEntry_RoomsOffersUseThisDevice() throws {
        let app = roomsOpen()
        let entry = app.buttons[RoomModeUnderTest.entry]
        var swipes = 0
        while !entry.exists && swipes < 12 {
            app.swipeUp()
            swipes += 1
        }
        guard roomModeWait(app, entry, "Settings → Rooms does not offer “Use this device as a room”", timeout: 5) else { return }
        evidence(app, "roommode-entry-rooms")
        while !entry.isHittable && swipes < 16 {
            app.swipeUp()
            swipes += 1
        }
        entry.tap()
        guard roomModeWait(app, app.buttons[RoomModeUnderTest.start], "the card did not open setup") else { return }
        app.buttons[RoomModeUnderTest.notNow].firstMatch.tap()
        if !app.buttons[RoomModeUnderTest.entry].waitForExistence(timeout: 10) {
            roomModeMiss(app, "Not now did not go back to Rooms")
        }
    }

    @MainActor
    func testRoomModeEntry_NoServerShellOffersUseThisDevice() throws {
        guard let app = roomModeSetup() else { return }
        if !app.staticTexts[RoomModeUnderTest.explainerTitle].exists {
            roomModeMiss(app, "setup's explainer has no heading")
        }
        app.buttons[RoomModeUnderTest.notNow].firstMatch.tap()
        _ = roomModeWait(app, app.buttons[RoomModeUnderTest.entry], "Not now did not go back to the no-server shell")
        evidence(app, "roommode-entry-local")
    }

    // MARK: - the room screen

    @MainActor
    func testRoomModeKiosk_AwaitingApprovalShowsTheCode() throws {
        let app = roomModeLaunch(room: RoomModeUnderTest.awaiting)
        let code = app.staticTexts["room-approval-code"]
        guard roomModeWait(app, code, "a room waiting for approval does not show its code", timeout: 30) else { return }
        if code.label != "approval code 4 8 2 9 1 3" {
            roomModeMiss(app, "the code reads “\(code.label)”, not the core's 482913 digit by digit")
        }
        if !app.buttons[RoomModeUnderTest.checkNow].exists {
            roomModeMiss(app, "no check now beside the code")
        }
        evidence(app, "roommode-awaiting")
        control("room-approve/porch", "{}", port: RoomModeUnderTest.port, what: "the porch approved")
        app.buttons[RoomModeUnderTest.checkNow].tap()
        let gone = NSPredicate(format: "exists == false")
        let waited = XCTWaiter().wait(for: [expectation(for: gone, evaluatedWith: code)], timeout: 30)
        if waited != .completed {
            roomModeMiss(app, "the code stayed after the room was approved and checked")
        }
    }

    @MainActor
    func testRoomModeKiosk_ApprovedRoomSaysTheWakeWordOrWhy() throws {
        let app = roomModeLaunch(room: RoomModeUnderTest.ready)
        guard roomModeWait(app, app.staticTexts[RoomModeUnderTest.ready], "the room screen does not name the room") else { return }
        let detail = app.staticTexts[RoomModeUnderTest.detail]
        let deadline = Date().addingTimeInterval(30)
        var said = ""
        while Date() < deadline {
            said = detail.exists ? detail.label : ""
            if said == RoomModeUnderTest.say || said.hasPrefix(RoomModeUnderTest.noModels) { break }
            Thread.sleep(forTimeInterval: 1)
        }
        if !(said == RoomModeUnderTest.say || said.hasPrefix(RoomModeUnderTest.noModels)) {
            roomModeMiss(app, "a ready room says “\(said)”, neither the wake word nor why there is none")
        }
        let ring = app.descendants(matching: .any)[RoomModeUnderTest.ring]
        if ring.exists && ring.label != "room state: idle" {
            roomModeMiss(app, "a ready room's ring says “\(ring.label)”, not idle (map §13.3)")
        }
        evidence(app, "roommode-ready")
    }

    @MainActor
    func testRoomModeKiosk_RejectedAndConflictSayWhatToDo() throws {
        let rejected = roomModeLaunch(room: RoomModeUnderTest.rejected)
        _ = roomModeWait(rejected, containing(rejected, RoomModeUnderTest.rejectedWords),
                         "a rejected room does not say to reset its pairing", timeout: 30)
        evidence(rejected, "roommode-rejected")
        rejected.terminate()

        let conflict = roomModeLaunch(room: RoomModeUnderTest.conflict)
        let detail = conflict.staticTexts[RoomModeUnderTest.detail]
        guard roomModeWait(conflict, detail, "a room in conflict says nothing", timeout: 30) else { return }
        let deadline = Date().addingTimeInterval(20)
        while Date() < deadline, detail.label.hasPrefix("Connecting") { Thread.sleep(forTimeInterval: 1) }
        if detail.label.hasPrefix("Connecting") || detail.label.isEmpty {
            roomModeMiss(conflict, "a room in conflict never said what to do: “\(detail.label)”")
        }
        evidence(conflict, "roommode-conflict")
    }

    @MainActor
    func testRoomModeKiosk_MuteClosesTheMicChip() throws {
        let app = roomModeLaunch(room: RoomModeUnderTest.ready)
        let mute = app.buttons[RoomModeUnderTest.mute]
        guard roomModeWait(app, mute, "no mute on a running room") else { return }
        // **Tap only once the room has settled.** Mute is drawn while the
        // line above it still says "Connecting to the server…"; a moment
        // later the ready room's sentence (the wake word, or the stub's
        // five-line "no wake-word models") pushes it about 78 points down.
        // I037 and I038 tapped in between: the synthesized tap was at
        // (196.5, 586.7), Mute's centre when it was found, and by the time
        // it landed Mute was at 628.3–700.3, so the tap hit the sentence
        // (the I038 recording shows both frames).
        let detail = app.staticTexts[RoomModeUnderTest.detail]
        let deadline = Date().addingTimeInterval(30)
        var said = ""
        while Date() < deadline {
            said = detail.exists ? detail.label : ""
            if said == RoomModeUnderTest.say || said.hasPrefix(RoomModeUnderTest.noModels) { break }
            Thread.sleep(forTimeInterval: 1)
        }
        guard said == RoomModeUnderTest.say || said.hasPrefix(RoomModeUnderTest.noModels) else {
            roomModeMiss(app, "the room never settled before mute: “\(said)”")
            return
        }
        mute.tap()
        guard roomModeWait(app, app.buttons[RoomModeUnderTest.unmute], "mute did not turn into unmute") else { return }
        let chip = app.descendants(matching: .any)[RoomModeUnderTest.chip]
        let closed = NSPredicate(format: "label == %@", RoomModeUnderTest.micOff)
        if XCTWaiter().wait(for: [expectation(for: closed, evaluatedWith: chip)], timeout: 10) != .completed {
            roomModeMiss(app, "muted, the mic chip still says “\(chip.label)”")
        }
        evidence(app, "roommode-muted")
        app.buttons[RoomModeUnderTest.unmute].tap()
        _ = roomModeWait(app, app.buttons[RoomModeUnderTest.mute], "unmute did not turn back into mute")
    }

    @MainActor
    func testRoomModeKiosk_ExitIsAHoldNotATap() throws {
        let app = roomModeLaunch(room: RoomModeUnderTest.ready)
        let exit = app.descendants(matching: .any)[RoomModeUnderTest.exit]
        guard roomModeWait(app, exit, "no exit on the room screen") else { return }
        exit.tap()
        if app.alerts[RoomModeUnderTest.exitTitle].waitForExistence(timeout: 3) {
            roomModeMiss(app, "a TAP on exit asked to leave: a room is not left by accident")
            return
        }
        exit.press(forDuration: 1.5)
        // The launch's lock facts are fixed: no passcode, no household
        // token — the confirmation, never a Face ID sheet.
        let alert = app.alerts[RoomModeUnderTest.exitTitle]
        guard roomModeWait(app, alert, "holding exit did not ask to leave", timeout: 10) else { return }
        evidence(app, "roommode-exit-asked")
        alert.buttons[RoomModeUnderTest.stayARoom].tap()
        _ = roomModeWait(app, app.descendants(matching: .any)[RoomModeUnderTest.ring], "Stay a room did not stay")
    }

    @MainActor
    func testRoomModeKiosk_BackgroundStopsAndForegroundResumes() throws {
        let app = roomModeLaunch(room: RoomModeUnderTest.ready)
        guard roomModeWait(app, app.buttons[RoomModeUnderTest.mute], "the room did not start on screen") else { return }
        XCUIDevice.shared.press(.home)
        if !app.wait(for: .runningBackground, timeout: 10) && app.state != .runningBackgroundSuspended {
            roomModeMiss(app, "the app did not leave the foreground")
            return
        }
        app.activate()
        XCTAssertTrue(app.wait(for: .runningForeground, timeout: 15))
        // Back on screen, the room was running, so it resumes by itself:
        // Mute (a running room), never Resume (a stopped one).
        guard roomModeWait(app, app.buttons[RoomModeUnderTest.mute], "the room did not resume on its return") else { return }
        if app.buttons[RoomModeUnderTest.resume].exists {
            roomModeMiss(app, "the room came back stopped, asking for Resume")
        }
        evidence(app, "roommode-resumed")
    }

    @MainActor
    func testRoomModeKiosk_HouseholdAppIsLocked() throws {
        // A phone that is ALSO a household device: the household app must
        // not be reachable while it is a room.
        let app = roomModeLaunch(room: RoomModeUnderTest.ready, alsoAHousehold: true)
        guard roomModeWait(app, app.descendants(matching: .any)[RoomModeUnderTest.ring], "the room screen did not draw") else { return }
        if app.tabBars.count > 0 {
            roomModeMiss(app, "a tab bar is on screen: the household app is reachable from the room")
        }
        for household in ["connect", "Home", "Settings"] where app.buttons[household].exists {
            roomModeMiss(app, "the household app's “\(household)” is on the room screen")
        }
        evidence(app, "roommode-locked")
    }
}
