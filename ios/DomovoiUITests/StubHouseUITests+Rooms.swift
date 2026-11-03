import XCTest

/// Settings → Rooms, driven against the stub house.
///
/// Every case here arrives the same way: `launch(push: "Satellites")`, which
/// is ``AuditStrip/seededRoute`` putting `Satellites` through
/// ``ShellModel/navigate(_:)`` — and that resolves to **Settings on its Rooms
/// section** (`Route.swift:137-139`), which is the entry point a person
/// actually reaches. The `satellites` route slot draws the same view and is
/// reachable only by a direct `go(.satellites)`, which nothing in the app
/// does.
///
/// ### What the house can and cannot say, and what that costs these cases
///
/// `/api/satellites` is the **house's** route (`stubhouse.py:705`) and Home's
/// cases audit its two rooms, so `fixtures/rooms.py` deliberately does not
/// claim it (NIGHT-RULES §2 forbids editing `stubhouse.py`, and a fixture that
/// owned that path would take it from Home). The roster therefore carries nine
/// fields, and the fields this screen would otherwise draw — `volume`,
/// `version`, `full_duplex`, `in_call_with`, `timers_own_only`, `mpd_ports`,
/// `voice`, `last_seen_at`, `upgrade` — are **absent**. That is a real state
/// of the screen, and these cases assert it honestly rather than pretending
/// otherwise:
///
///  * ``testRooms_OpeningARoomShowsItsOverview`` asserts the unknown branches
///    (`registry default`, the em dashes) because that is what an absent
///    `voice` and absent `mpd_ports` mean;
///  * ``testRooms_HangUpIsNotOfferedWhenNoRoomIsInACall`` is MAP's `hang up`
///    case turned the one way round the house can support — no room is ever in
///    a call here, so what is testable is that the control is **not** drawn and
///    the honest sentence is;
///  * ``testRooms_VolumeSendsTheLevelOnce`` drags from the fallback 50 rather
///    than from a reported level.
///
/// ### The other item that claims these paths stands down, and the house
/// really serves ours
///
/// `fixtures/manual.py` (the `manual` item) also claims `/api/satellites/`
/// and `/api/config/version`, and it sorts first — but both of its branches
/// open with `if _claimed_elsewhere(house, …): return False`
/// (`fixtures/manual.py:452`, `:494-499`), and a module that returns `False`
/// leaves the request to the next one (`stubhouse.py:818-830`). `rooms.py`
/// lists both in its `PREFIXES`, so it is the module that answers them.
/// Curled against a local house on this item's port, `…/kitchen/sessions`
/// returns this fixture's two sessions and `/api/config/version` returns its
/// `aaaa111`. An earlier revision of this file assumed the opposite and
/// asserted the sessions tab's **empty state**; that case would have failed
/// on the train, and it is now written against what the house serves.
extension StubHouseUITests {

    // MARK: - getting there

    /// Settings → Rooms, with the roster drawn.
    ///
    /// The fixtures are reset **first**: cases share one house for a whole run
    /// and this screen's fixture remembers volume levels, a flipped timer
    /// scope and a cancelled timer.
    @MainActor
    func roomsOpen(
        mode: String = StubHouseUITests.ordinaryTypeSize,
        pushDelay: Double = 3
    ) -> XCUIApplication {
        control("reset-fixtures", "{}", port: 6601, what: "the Rooms fixtures")
        let app = launch(mode: mode, push: "Satellites", pushDelay: pushDelay)
        let summary = roomsSummaryText(app)
        if !summary.waitForExistence(timeout: 30) {
            // The tree, not a sentence about what is missing: a claim about
            // something absent cannot say what is present, and this file's
            // header is about the six runs that cost.
            print("=== ROOMS DID NOT DRAW — tree follows ===")
            print(app.debugDescription)
        }
        return app
    }

    /// The summary line, which only a landed roster read can produce: the
    /// counts come from `status`, the key the house once sent as `online`.
    @MainActor
    func roomsSummaryText(_ app: XCUIApplication) -> XCUIElement {
        app.staticTexts[RoomsStringsUnderTest.summary]
    }

    /// Open the room whose card's label starts with its id.
    ///
    /// **Not `firstMatch` on a label that merely contains the name**: the
    /// detail's own header contains it too, and a query that matched either
    /// would pass on a screen that never opened.
    @MainActor
    func roomsOpenRoom(_ app: XCUIApplication, _ room: String) -> Bool {
        let card = app.buttons
            .matching(NSPredicate(format: "label BEGINSWITH %@", room))
            .firstMatch
        guard card.waitForExistence(timeout: 20) else {
            print("=== NO CARD FOR \(room) — tree follows ===")
            print(app.debugDescription)
            return false
        }
        card.tap()
        let closer = app.buttons[RoomsStringsUnderTest.closeRoom]
        guard closer.waitForExistence(timeout: 20) else {
            print("=== \(room) DID NOT OPEN — tree follows ===")
            print(app.debugDescription)
            return false
        }
        return true
    }

    /// The two scroll views an opened room has, told apart by their height.
    ///
    /// ``RoomsTabStrip`` is a **horizontal** `ScrollView` one row tall (44
    /// points at the ordinary size); the settings page it sits in is a
    /// vertical one that fills the window (676.7 points, measured on train
    /// S004). Neither carries an identifier — SwiftUI sets none on a scroll
    /// view — and `app.scrollViews.firstMatch` takes the page, so this picks
    /// the **shortest** for the strip and the **tallest** for the page rather
    /// than comparing against a constant: the strip grows with the type size
    /// and any threshold would be a number waiting to be crossed at AX5.
    @MainActor
    func roomsScrollView(_ app: XCUIApplication, strip: Bool) -> XCUIElement? {
        let all = app.scrollViews
        // **The strip is only the shortest of several.** With one scroll view
        // on screen the shortest *is* the page, and a caller that scrolled
        // that believing it was the strip would scroll the whole screen and
        // then tap a tab that had not moved — the S004 failure again, wearing
        // the fix's clothes. No strip, no answer.
        guard !strip || all.count > 1 else { return nil }
        var best: XCUIElement?
        var height = strip ? Double.greatestFiniteMagnitude : -1
        for index in 0..<all.count {
            let one = all.element(boundBy: index)
            guard one.exists else { continue }
            let its = Double(one.frame.height)
            if strip ? (its < height) : (its > height) {
                height = its
                best = one
            }
        }
        return best
    }

    /// Scroll `view` until `element` is **wholly inside it**, and say whether
    /// it got there.
    ///
    /// ### Why wholly, and not `isHittable`
    ///
    /// This is the fix for train S004's six `Failed to compute hit point`
    /// failures *and* for the one that was worse than a failure. Seven tab
    /// words are 652 points wide in a 361-point strip, so `notes`, `timers`
    /// and `settings` start off screen entirely — XCUITest refuses to tap
    /// those and says so. But `recently played` sits at x 319.3 with a width
    /// of 128.7: its **centre**, 383.7, is on the window and off the strip's
    /// clip, so `isHittable` is true, the tap is synthesised at that point,
    /// it lands outside the scroll view's clipped bounds — and nothing
    /// happens. The case then failed twenty rows later looking for a tab body
    /// that had never opened, with `overview` still `Selected` in the tree.
    /// A partially visible control is not a tappable one.
    ///
    /// The direction is chosen per flick rather than fixed, so this converges
    /// from either side.
    ///
    /// **`vertical: true` is a measurement, not a way to travel.** It is
    /// called on the settings page only to assert that something already *is*
    /// on the window, and its flicks are a fallback that should never run: a
    /// swipe on the page starts at the page's centre, which at the ordinary
    /// size is y 431 — inside ``RoomsTabStrip``, a horizontal scroll view,
    /// which consumes a pan rather than passing it to its parent. To bring
    /// something vertically into view, **tap an inert word next to it**:
    /// XCUITest scrolls to an element before touching it, which is how this
    /// file reaches a switch at y 1128 and the slider at y 922.
    ///
    /// ### Why the horizontal travel is a measured drag and not a flick
    ///
    /// Train S006's one failure. `swipeLeft()`/`swipeRight()` are flicks:
    /// they carry momentum, so each one travels a distance nobody chose. At
    /// AX5 the seven tab words are 1587 points wide in a 361-point strip, and
    /// the two ends of `timers` straddle the clip by a few points — so a flick
    /// aimed at it overshot, the next flick (direction chosen afresh) overshot
    /// back, and the strip oscillated through all ten flicks without ever
    /// coming to rest on the tab. The tree printed at the failure even showed
    /// `timers` wholly inside the strip: the last flick had landed, the loop
    /// had simply run out. A press-and-drag has no momentum, so it moves the
    /// content by the number of points asked for and converges in one or two
    /// steps from either side. The flicks stay as a fallback — if the drag
    /// gesture is not recognised at all, nothing else would move the strip.
    @MainActor
    func roomsBring(
        _ element: XCUIElement,
        into view: XCUIElement,
        vertical: Bool,
        flicks: Int = 10
    ) -> Bool {
        for _ in 0..<flicks {
            let seat = view.frame
            let it = element.frame
            if seat.contains(it) { return true }
            if vertical {
                if it.midY > seat.midY { view.swipeUp(velocity: .slow) } else { view.swipeDown(velocity: .slow) }
            } else if !roomsDrag(view, by: roomsShortfall(it, in: seat)) {
                break
            }
        }
        if view.frame.contains(element.frame) { return true }
        // Fallback: the old flicks, in case the drag gesture moved nothing.
        for _ in 0..<flicks {
            let seat = view.frame
            let it = element.frame
            if seat.contains(it) { return true }
            if vertical {
                if it.midY > seat.midY { view.swipeUp(velocity: .slow) } else { view.swipeDown(velocity: .slow) }
            } else {
                if it.midX > seat.midX { view.swipeLeft() } else { view.swipeRight() }
            }
        }
        return view.frame.contains(element.frame)
    }

    /// How far, in points, a strip's content must move for `it` to sit wholly
    /// inside `seat` — negative to move content left, positive to move it
    /// right, zero when it already fits.
    ///
    /// The eight-point margin is why this converges where the flicks did not:
    /// landing a tab exactly on the clip's edge is landing it outside, and a
    /// drag measured to the edge lands there about half the time.
    func roomsShortfall(_ it: CGRect, in seat: CGRect) -> CGFloat {
        let margin: CGFloat = 8
        if it.maxX > seat.maxX { return -(it.maxX - seat.maxX + margin) }
        if it.minX < seat.minX { return (seat.minX - it.minX) + margin }
        return 0
    }

    /// Drag a horizontal scroller by a measured number of points, and say
    /// whether a drag was attempted.
    ///
    /// The whole gesture has to stay inside the strip — a touch that leaves it
    /// is a touch on something else — so one drag travels at most 80% of the
    /// strip's width and ``roomsBring``'s loop does the rest.
    @MainActor
    func roomsDrag(_ view: XCUIElement, by dx: CGFloat) -> Bool {
        guard dx != 0 else { return true }
        let width = view.frame.width
        guard width > 40 else { return false }
        let travel = max(-width * 0.8, min(width * 0.8, dx))
        let from = view.coordinate(
            withNormalizedOffset: CGVector(dx: travel < 0 ? 0.9 : 0.1, dy: 0.5)
        )
        // The press before the move is what makes this a drag rather than a
        // flick; it is also long enough that the tab word under the finger
        // cannot read the gesture as a tap.
        from.press(
            forDuration: 0.4,
            thenDragTo: from.withOffset(CGVector(dx: travel, dy: 0))
        )
        return true
    }

    /// Pick one of the seven tabs by its word.
    ///
    /// Scrolls the strip until the word is wholly inside it (``roomsBring``),
    /// then taps — and then waits for the strip to **say** the tab is open.
    /// That last wait is not ceremony: the tap that silently missed its target
    /// on train S004 reported success here and failed in the body's
    /// assertions, which is the hardest kind of failure to read.
    @MainActor
    func roomsTab(_ app: XCUIApplication, _ label: String) -> Bool {
        let tab = app.buttons[label].firstMatch
        guard tab.waitForExistence(timeout: 20) else {
            print("=== NO \(label) TAB — tree follows ===")
            print(app.debugDescription)
            return false
        }
        guard let strip = roomsScrollView(app, strip: true) else {
            print("=== NO TAB STRIP TO SCROLL — tree follows ===")
            print(app.debugDescription)
            return false
        }
        guard roomsBring(tab, into: strip, vertical: false) else {
            print("=== \(label) WOULD NOT COME INTO THE STRIP — tree follows ===")
            print(app.debugDescription)
            return false
        }
        tab.tap()
        let deadline = Date().addingTimeInterval(10)
        while Date() < deadline {
            if tab.isSelected { return true }
            usleep(150_000)
        }
        // `.isSelected` is the `.isSelected` trait ``RoomsTabStrip`` adds to
        // the open tab, which is the only thing on screen that says which tab
        // is open in words rather than in a font weight.
        print("=== \(label) WAS TAPPED AND DID NOT OPEN — tree follows ===")
        print(app.debugDescription)
        return false
    }

    /// A field on this screen, picked by its accessibility label across every
    /// element type.
    ///
    /// ``composerField(_:)``'s reason: the announce and broadcast boxes are
    /// `TextField`s with a **vertical axis**, so that they grow with the type
    /// size instead of clipping, and a vertically-growing SwiftUI field does
    /// not always report as `.textField` in the query tree. A case that asked
    /// for `app.textFields[…]` would pass or fail on that detail rather than
    /// on the field.
    ///
    /// Both labels are words drawn **nowhere else** on their screen
    /// (``RoomsStrings/announcePlaceholder(room:)`` and
    /// ``RoomsStrings/broadcastFieldLabel``), which is what makes the match
    /// unambiguous: the section heading above the announce box says
    /// `announce to kitchen` too, and a query for that would take the heading
    /// and type into nothing.
    @MainActor
    func roomsField(_ app: XCUIApplication, _ label: String) -> XCUIElement {
        app.descendants(matching: .any)
            .matching(NSPredicate(format: "label == %@", label))
            .firstMatch
    }

    /// Wait for words to appear, polling faster than `waitForExistence` does.
    ///
    /// A toast lives for ``ShellStrings/toastMs`` — **2.4 seconds** — and
    /// `waitForExistence` polls about once a second, so a toast can be raised
    /// and gone between two of its looks. Every assertion about a toast in
    /// this file goes through here.
    @MainActor
    func roomsWaitFor(_ app: XCUIApplication, _ text: String, timeout: Double = 20) -> Bool {
        let deadline = Date().addingTimeInterval(timeout)
        while Date() < deadline {
            if containing(app, text).exists { return true }
            usleep(150_000)
        }
        print("=== NEVER SAW “\(text)” — tree follows ===")
        print(app.debugDescription)
        return false
    }

    /// Read one of the fixture's own readback routes, as text.
    ///
    /// ``control(_:_:port:what:)`` posts and asserts a 200; this is the other
    /// direction, and it is what makes "the app sent **one** POST with the
    /// final level" an assertion about the wire rather than about a toast.
    @MainActor
    func roomsAsk(_ route: String, port: Int = 6601) -> String {
        var request = URLRequest(url: URL(string: "http://127.0.0.1:\(port)/api/_test/\(route)")!)
        request.httpMethod = "GET"
        request.setValue(Self.token, forHTTPHeaderField: "X-Device-Token")
        let answered = expectation(description: "the stub answered \(route)")
        var said = ""
        URLSession.shared.dataTask(with: request) { data, _, _ in
            said = String(data: data ?? Data(), encoding: .utf8) ?? ""
            answered.fulfill()
        }.resume()
        wait(for: [answered], timeout: 15)
        print("ASK \(route) -> \(said)")
        return said
    }

    // MARK: - the roster

    /// The Settings slot is filled, and the roster decoded.
    ///
    /// Both room names **and** the summary: the counts come from `status`,
    /// which is the key the house once sent as `online: true` — and
    /// `HomeRoom.online` is computed from it, so every room decoded as offline
    /// for a week without a single test failing. A port that defaulted the
    /// roster would draw `0 online · 0 offline · 0 provisioned` here.
    @MainActor
    func testRooms_SettingsRoomsDrawsTheRoster() throws {
        let app = roomsOpen()
        evidence(app, "rooms-10-roster")
        XCTAssertTrue(
            roomsSummaryText(app).exists,
            "Settings → Rooms drew no summary line — either the slot is still "
                + "the placeholder or the roster read did not land"
        )
        XCTAssertTrue(
            containing(app, "kitchen").exists,
            "the online room is not on the roster"
        )
        XCTAssertTrue(
            containing(app, "study").exists,
            "the offline room is not on the roster"
        )
        XCTAssertFalse(
            app.staticTexts[ShellStringsUnderTest.notBuilt].exists,
            "the Rooms section still says `not built yet` — the slot was not filled"
        )
    }

    /// An offline room says when it was last heard from; an online one says
    /// `active now`.
    ///
    /// The house's `study` carries `last_connected_at` and **no**
    /// `last_seen_at`, so what this exercises is
    /// ``roomsLastSeenISO(_:)``'s fallback — the preference for `last_seen_at`
    /// itself is pinned in `RoomsRulesTests`, because the roster is the
    /// house's and cannot be given the field from here (see this extension's
    /// note).
    @MainActor
    func testRooms_OfflineRoomSaysWhenItWasLastSeen() throws {
        let app = roomsOpen()
        evidence(app, "rooms-11-last-seen")
        XCTAssertTrue(
            roomsWaitFor(app, RoomsStringsUnderTest.activeNow),
            "the online room's card does not say `active now`"
        )
        // `_iso(-9000)` in the house's seed: two and a half hours, which
        // `relTime` reports as **3h ago**, not 2h. Its hour branch is
        // `rounded(seconds / 3600.0)` and `rounded` is Kotlin's
        // `roundToInt()` — half away from zero (`Format.swift:147-153`,
        // `Format.kt:34`). This case asserted `2h ago` on train S004 and was
        // wrong about the rule, not about the screen; the tree it printed had
        // `study, offline, last seen 3h ago` on the card all along. Still a
        // discriminating pair: a card that drew nothing, or drew the raw
        // stamp, says neither.
        XCTAssertTrue(
            containing(app, "3h ago").exists,
            "the offline room's card does not say how long ago it was heard "
                + "from — `study` has a `last_connected_at` 9000s old"
        )
        XCTAssertFalse(
            containing(app, "2h ago").exists,
            "the card rounded 9000s down to 2h — `relTime` rounds half away "
                + "from zero"
        )
    }

    /// The playing room shows its song and its clock; the quiet one does not
    /// show the playing room's song.
    ///
    /// The second half is the discriminating one: `Harvest Moon` belongs to
    /// `kitchen` alone, and a card that drew the roster's first `now_playing`
    /// for every room would put it on `study` as well.
    @MainActor
    func testRooms_PlayingRoomShowsSongAndProgress() throws {
        let app = roomsOpen()
        evidence(app, "rooms-12-song")
        XCTAssertTrue(
            roomsWaitFor(app, "Harvest Moon"),
            "the playing room's card does not name its song"
        )
        XCTAssertTrue(
            containing(app, "Neil Young").exists,
            "the song line has no artist in it"
        )
        // `elapsed_sec: 97` of `duration_sec: 302` -> `1:37 / 5:02`, and the
        // ticker may have advanced it a second or two.
        XCTAssertTrue(
            containing(app, "/ 5:02").exists,
            "the song clock does not show the track's length"
        )
        let study = app.buttons
            .matching(NSPredicate(format: "label BEGINSWITH %@", "study"))
            .firstMatch
        XCTAssertTrue(study.exists, "no card for the offline room")
        XCTAssertFalse(
            study.label.contains("Harvest Moon"),
            "the offline room's card is showing the playing room's song: \(study.label)"
        )
        XCTAssertTrue(
            study.label.contains(RoomsStringsUnderTest.noMusic),
            "the quiet room's card does not say `no music`: \(study.label)"
        )
    }

    /// Opening a room shows **that room's** overview.
    ///
    /// The read rows name the room the card was for, and the two unknown
    /// branches are asserted as such: the house's roster sends no `voice` and
    /// no `mpd_ports`, so `registry default` and the em dash are the honest
    /// readings — and a port that invented a default would draw a voice name
    /// here.
    @MainActor
    func testRooms_OpeningARoomShowsItsOverview() throws {
        let app = roomsOpen()
        XCTAssertTrue(roomsOpenRoom(app, "kitchen"), "the kitchen card did not open")
        evidence(app, "rooms-13-overview")
        XCTAssertTrue(
            roomsWaitFor(app, RoomsStringsUnderTest.roomIDLabel),
            "the overview drew no read rows"
        )
        XCTAssertTrue(
            containing(app, RoomsStringsUnderTest.registryDefault).exists,
            "a room with no `voice` must read `registry default`"
        )
        XCTAssertTrue(
            containing(app, RoomsStringsUnderTest.lastConnected).exists,
            "the overview has no `last connected` row"
        )
        XCTAssertTrue(
            containing(app, "kitchen-pi").exists,
            "the health read's host name is not on screen — this is the value "
                + "only this room's own health document carries"
        )
    }

    /// Announcing into one room says **what happened**, which is not the
    /// status code.
    ///
    /// The second half is the whole reason the app reads `announced_to`: in the
    /// dead-socket state the server still answers 200 and the room is missing
    /// from the list, and the sentence is a different one.
    @MainActor
    func testRooms_AnnounceToOneRoomSaysWhatHappened() throws {
        let app = roomsOpen()
        XCTAssertTrue(roomsOpenRoom(app, "kitchen"), "the kitchen card did not open")
        let field = roomsField(app, RoomsStringsUnderTest.announceFieldLabel)
        XCTAssertTrue(field.waitForExistence(timeout: 20), "no announce field for the room")
        // The section heading is on screen as well, and says so: this is the
        // assertion that the two are different elements.
        XCTAssertTrue(
            containing(app, RoomsStringsUnderTest.announceToKitchen).exists,
            "the announce section is not labelled with the room it speaks in"
        )
        field.tap()
        app.typeText("the kettle is on")
        let send = app.buttons[RoomsStringsUnderTest.send].firstMatch
        XCTAssertTrue(send.waitForExistence(timeout: 10), "no send button beside the announce field")
        evidence(app, "rooms-14-announce")
        send.tap()
        XCTAssertTrue(
            roomsWaitFor(app, RoomsStringsUnderTest.announcedToKitchen),
            "sending an announcement said nothing"
        )

        // The same send against a house whose socket is dead: a 200 with an
        // empty `announced_to`.
        control("rooms-dead-ws", "{}", port: 6601, what: "the dead-socket state")
        field.tap()
        app.typeText("and again")
        send.tap()
        XCTAssertTrue(
            roomsWaitFor(app, RoomsStringsUnderTest.acceptedButDidNotPlay),
            "a 200 with nothing in `announced_to` was reported as a success"
        )
    }

    /// The broadcast card reports who took it.
    @MainActor
    func testRooms_BroadcastReportsWhoTookIt() throws {
        let app = roomsOpen()
        let field = roomsField(app, RoomsStringsUnderTest.broadcastFieldLabel)
        XCTAssertTrue(field.waitForExistence(timeout: 20), "no broadcast field on the roster")
        XCTAssertTrue(
            containing(app, RoomsStringsUnderTest.broadcastHeading).exists,
            "the broadcast card has no heading"
        )
        field.tap()
        app.typeText("dinner's ready")
        let send = app.buttons[RoomsStringsUnderTest.broadcastButton].firstMatch
        XCTAssertTrue(send.waitForExistence(timeout: 10), "no broadcast button")
        evidence(app, "rooms-15-broadcast")
        send.tap()
        // One room is online and one took it.
        XCTAssertTrue(
            roomsWaitFor(app, RoomsStringsUnderTest.broadcastedToOneRoom),
            "a delivered broadcast said nothing"
        )

        control("rooms-dead-ws", "{}", port: 6601, what: "the dead-socket state")
        field.tap()
        app.typeText("anyone there")
        send.tap()
        XCTAssertTrue(
            roomsWaitFor(app, RoomsStringsUnderTest.noRoomsAccepted),
            "a broadcast nothing accepted was reported as a success"
        )
    }

    /// A drag sends **one** level, not one per step.
    ///
    /// The readback is what makes this an assertion about the wire: a POST per
    /// slider value would both flood the room and arrive out of order, so the
    /// level that stuck would be whichever request the server handled last.
    @MainActor
    func testRooms_VolumeSendsTheLevelOnce() throws {
        let app = roomsOpen()
        XCTAssertTrue(roomsOpenRoom(app, "kitchen"), "the kitchen card did not open")
        let slider = app.sliders.firstMatch
        XCTAssertTrue(slider.waitForExistence(timeout: 20), "no volume slider on the overview")
        // **The slider has to be on the window before it can be dragged.**
        // It is built at y 922 in a page whose viewport ends at 770, and on
        // train S004 this case dragged it there: `adjust` does **not** scroll
        // to its element, so the gesture was synthesised off the window, the
        // thumb stayed at 50%, no POST went out, and the case failed on the
        // toast three lines later. Every other write on this screen is a
        // `tap` — which does scroll to its element, which is how
        // `testRooms_TimerScopeSwitchSticks` reaches a switch at y 1128 — so
        // this was the only case to pay for it.
        //
        // The anchor is the **help line under the slider**, tapped for its
        // scroll and nothing else: a tap on a `Text` does nothing, and
        // bringing a line that sits *below* the slider into view puts the
        // slider above it on the window. Not a swipe on the page: the page's
        // own centre at this size lands on ``RoomsTabStrip``, a horizontal
        // scroll view, and a vertical pan that starts inside one is consumed
        // rather than passed to its parent.
        let help = containing(app, RoomsStringsUnderTest.volumeHelpPrefix)
        XCTAssertTrue(help.waitForExistence(timeout: 20), "no volume help line to scroll to")
        help.tap()
        guard let page = roomsScrollView(app, strip: false) else {
            return XCTFail("no settings page to measure the slider against")
        }
        XCTAssertTrue(
            roomsBring(slider, into: page, vertical: true),
            "the volume slider is not on the window, so a drag of it would "
                + "be synthesised off the screen and quietly do nothing"
        )
        evidence(app, "rooms-16-volume")
        slider.adjust(toNormalizedSliderPosition: 0.8)
        // The thumb moved at all, asserted before the toast: a gesture that
        // misses is silence, and silence here reads exactly like an app that
        // never sent the level.
        XCTAssertNotEqual(
            slider.value as? String,
            "50%",
            "the drag did not move the thumb, so nothing about the write is under test"
        )
        XCTAssertTrue(
            roomsWaitFor(app, RoomsStringsUnderTest.volumeSetPrefix),
            "setting the volume said nothing"
        )
        let said = roomsAsk("rooms-volume")
        XCTAssertTrue(
            said.contains("\"count\": 1"),
            "the app sent more than one volume POST for one drag: \(said)"
        )
        // 0.8 of 0..100. A tolerance, because the gesture lands where it
        // lands; what is under test is that exactly one level went out and
        // that it is the one the thumb ended on, not a value from the middle
        // of the drag.
        let posted = said
            .split(separator: ":")
            .last?
            .trimmingCharacters(in: CharacterSet(charactersIn: " }\n"))
        let level = Int(posted ?? "") ?? -1
        XCTAssertTrue(
            (70...90).contains(level),
            "the level that went out was \(level), not the one the drag ended on: \(said)"
        )
    }

    /// The timer-scope switch flips, and the server is told.
    ///
    /// The toast and the readback, because the switch is **optimistic**: it
    /// moves before the PUT answers, so the position alone would pass against
    /// an app that never sent anything.
    @MainActor
    func testRooms_TimerScopeSwitchSticks() throws {
        let app = roomsOpen()
        XCTAssertTrue(roomsOpenRoom(app, "kitchen"), "the kitchen card did not open")
        let toggle = app.switches
            .matching(NSPredicate(format: "label CONTAINS %@", RoomsStringsUnderTest.timerScopeLabel))
            .firstMatch
        XCTAssertTrue(toggle.waitForExistence(timeout: 20), "no timer-scope switch on the overview")
        evidence(app, "rooms-17-timer-scope")
        toggle.tap()
        XCTAssertTrue(
            roomsWaitFor(app, RoomsStringsUnderTest.ownTimersOnly),
            "flipping the switch said nothing"
        )
        let said = roomsAsk("rooms-asked")
        XCTAssertTrue(
            said.contains("\"own_only\": true"),
            "the PUT did not ask for `own_only: true`: \(said)"
        )
    }

    /// Custom quiet hours need two clock times, and `7:5` is not one.
    ///
    /// Both halves: nothing is sent for a time that is not a 24-hour clock
    /// time, and a good one is **normalised** before it goes — `7:05` is sent
    /// as `07:05`, which is the readback's discriminating value.
    @MainActor
    func testRooms_QuietHoursCustomNeedsTwoClockTimes() throws {
        let app = roomsOpen()
        XCTAssertTrue(roomsOpenRoom(app, "kitchen"), "the kitchen card did not open")
        XCTAssertTrue(roomsTab(app, RoomsStringsUnderTest.tabSettings), "no settings tab")
        let custom = app.buttons[RoomsStringsUnderTest.quietCustom].firstMatch
        XCTAssertTrue(custom.waitForExistence(timeout: 20), "no custom quiet-hours choice")
        custom.tap()
        let from = app.textFields[RoomsStringsUnderTest.quietFrom].firstMatch
        XCTAssertTrue(from.waitForExistence(timeout: 20), "picking custom drew no `from` field")
        // A tap lands the caret where it falls, so the field is cleared
        // before anything is typed into it.
        from.tap()
        clearField(app)
        // The Return key dismisses a single-line field's keyboard, which is
        // what keeps `Save` reachable: it sits under the two fields, and a
        // keyboard over it would take the tap instead.
        app.typeText("7:5" + XCUIKeyboardKey.return.rawValue)
        let save = app.buttons[RoomsStringsUnderTest.quietSave].firstMatch
        XCTAssertTrue(save.waitForExistence(timeout: 10), "no Save on the custom hours")
        evidence(app, "rooms-18-quiet-bad")
        save.tap()
        XCTAssertTrue(
            roomsWaitFor(app, RoomsStringsUnderTest.timesLookLike),
            "a time that is not a clock time was accepted silently"
        )
        var said = roomsAsk("rooms-asked")
        XCTAssertFalse(
            said.contains("\"custom\""),
            "something was PUT for a time that is not a clock time: \(said)"
        )

        from.tap()
        clearField(app)
        app.typeText("7:05" + XCUIKeyboardKey.return.rawValue)
        save.tap()
        XCTAssertTrue(
            roomsWaitFor(app, RoomsStringsUnderTest.keepsQuiet),
            "saving good custom hours said nothing"
        )
        said = roomsAsk("rooms-asked")
        XCTAssertTrue(
            said.contains("\"start\": \"07:05\""),
            "`7:05` was not normalised to `07:05` before it was sent: \(said)"
        )
    }

    /// A masked reminder's words are **not** on screen.
    ///
    /// The one assertion on this screen that is about privacy rather than
    /// layout: the server held those words back on purpose (rule M1,
    /// `satellites.py:1090`), and the fixture keeps them in a constant it
    /// never serialises — so this fails if the app ever puts a name on a
    /// masked row from somewhere else.
    @MainActor
    func testRooms_TimersTabHidesMaskedReminderWords() throws {
        let app = roomsOpen()
        XCTAssertTrue(roomsOpenRoom(app, "kitchen"), "the kitchen card did not open")
        XCTAssertTrue(roomsTab(app, RoomsStringsUnderTest.tabTimers), "no timers tab")
        XCTAssertTrue(
            roomsWaitFor(app, RoomsStringsUnderTest.timerWordsHidden),
            "the masked reminder is not drawn as `reminder (words hidden)`"
        )
        evidence(app, "rooms-19-timers")
        XCTAssertFalse(
            containing(app, "take the pills").exists,
            "a masked reminder's words are on screen"
        )
        // The two unmasked rows are named, so this is not passing because the
        // tab drew nothing at all.
        XCTAssertTrue(containing(app, "pasta water").exists, "the plain timer is missing")
        XCTAssertTrue(containing(app, "laundry").exists, "the long timer is missing")
    }

    /// Cancelling a timer removes it, and cancelling one that has gone is not
    /// reported as a failure.
    @MainActor
    func testRooms_CancellingATimerRemovesIt() throws {
        let app = roomsOpen()
        XCTAssertTrue(roomsOpenRoom(app, "kitchen"), "the kitchen card did not open")
        XCTAssertTrue(roomsTab(app, RoomsStringsUnderTest.tabTimers), "no timers tab")
        let row = containing(app, "pasta water")
        XCTAssertTrue(row.waitForExistence(timeout: 20), "the timer to cancel is not there")
        // A query, not `app.buttons[...]`: the subscript hands back the single
        // matching element, which has no `count` and no `element(boundBy:)` —
        // and this case needs the FIRST of three same-labelled cancels.
        let cancels = app.buttons
            .matching(NSPredicate(format: "label == %@", RoomsStringsUnderTest.cancel))
        XCTAssertTrue(cancels.firstMatch.waitForExistence(timeout: 10), "no cancel button")
        evidence(app, "rooms-20-cancel")
        // The first row's own cancel: the rows are in the fixture's order and
        // `pasta water` is the first of the three.
        cancels.element(boundBy: 0).tap()
        XCTAssertTrue(
            roomsWaitFor(app, RoomsStringsUnderTest.cancelledTimer),
            "cancelling a timer said nothing"
        )
        XCTAssertTrue(
            waitUntilGone(row),
            "the cancelled timer is still on the list after the re-read"
        )
    }

    /// The conversations search keeps the turn that matched and drops the one
    /// that did not.
    ///
    /// `marmalade` is in one turn's words and `wellington` in another's, and
    /// in no other turn — so both directions are asserted and a search that
    /// did nothing fails on the second.
    @MainActor
    func testRooms_ConversationsSearchFiltersByWord() throws {
        let app = roomsOpen()
        XCTAssertTrue(roomsOpenRoom(app, "kitchen"), "the kitchen card did not open")
        XCTAssertTrue(roomsTab(app, RoomsStringsUnderTest.tabConversations), "no conversations tab")
        XCTAssertTrue(
            roomsWaitFor(app, "marmalade"),
            "the conversations tab drew no turns"
        )
        XCTAssertTrue(containing(app, "wellington").exists, "the second turn is missing")
        let search = app.textFields[RoomsStringsUnderTest.searchThisRoom].firstMatch
        XCTAssertTrue(search.waitForExistence(timeout: 20), "no search box on the conversations tab")
        search.tap()
        app.typeText("marmalade")
        evidence(app, "rooms-21-search")
        XCTAssertTrue(
            roomsWaitFor(app, RoomsStringsUnderTest.oneOfFour),
            "the count line does not say how many of how many are shown"
        )
        XCTAssertTrue(containing(app, "marmalade").exists, "the matching turn was filtered out")
        XCTAssertFalse(
            containing(app, "wellington").exists,
            "the search kept a turn that does not contain the word"
        )
    }

    /// A long answer is cut, and `more` reveals the rest.
    ///
    /// `sourdough` is past the 130-character cut and appears nowhere else in
    /// the house, so "the answer was truncated" and "the answer was whole" are
    /// different screens.
    @MainActor
    func testRooms_LongAnswerExpandsWithMore() throws {
        let app = roomsOpen()
        XCTAssertTrue(roomsOpenRoom(app, "kitchen"), "the kitchen card did not open")
        XCTAssertTrue(roomsTab(app, RoomsStringsUnderTest.tabConversations), "no conversations tab")
        XCTAssertTrue(roomsWaitFor(app, "Heat the oven"), "the long answer is not on screen")
        XCTAssertFalse(
            containing(app, "sourdough").exists,
            "a 168-character answer was drawn whole — nothing cut it at 130"
        )
        let more = app.buttons[RoomsStringsUnderTest.more].firstMatch
        XCTAssertTrue(more.waitForExistence(timeout: 20), "a truncated answer offers no `more`")
        evidence(app, "rooms-22-more")
        more.tap()
        XCTAssertTrue(
            roomsWaitFor(app, "sourdough"),
            "`more` did not reveal the rest of the answer"
        )
        XCTAssertTrue(
            app.buttons[RoomsStringsUnderTest.less].firstMatch.waitForExistence(timeout: 10),
            "an expanded answer offers no `less`"
        )
    }

    /// Recently played shows each row's source, and `+ add` only where the
    /// server said it may.
    ///
    /// The refused state is the second half: `can_add` is computed without
    /// consulting a fulfiller, so the honest thing when the house refuses is
    /// to say **what the house said**.
    @MainActor
    func testRooms_RecentlyPlayedShowsSourceAndAdd() throws {
        let app = roomsOpen()
        XCTAssertTrue(roomsOpenRoom(app, "kitchen"), "the kitchen card did not open")
        XCTAssertTrue(roomsTab(app, RoomsStringsUnderTest.tabRecentlyPlayed), "no recently-played tab")
        XCTAssertTrue(roomsWaitFor(app, "The Rite of Spring"), "the library row is missing")
        XCTAssertTrue(containing(app, "Lost Mariner").exists, "the external row is missing")
        XCTAssertTrue(containing(app, "Nightwaves").exists, "the external row's channel is missing")
        // A query, not the subscript: this case counts the `add` buttons, and
        // `app.buttons[...]` is one element with no `count`.
        let adds = app.buttons
            .matching(NSPredicate(format: "label == %@", RoomsStringsUnderTest.add))
        evidence(app, "rooms-23-played")
        // **One** row may be added, and the server says which: two buttons
        // would mean the library row had one too.
        XCTAssertEqual(
            adds.count,
            1,
            "there are \(adds.count) `add` buttons for one addable row"
        )
        // The server's own refusal wording, not a generic failure.
        control("rooms-add", "{\"how\":\"no-fulfiller\"}", port: 6601, what: "the add refusal")
        adds.element(boundBy: 0).tap()
        XCTAssertTrue(
            roomsWaitFor(app, RoomsStringsUnderTest.noFulfiller),
            "a refused add did not say what the house said about it"
        )
    }

    /// **The no-misleading-controls rule, as a test.**
    ///
    /// None of Android's admin controls is drawn anywhere in an opened room,
    /// at either type size. This is the case that fails if a later edit ports
    /// the buttons faithfully: each of them 401s, 403s or 501s every time from
    /// a phone that holds only the household token.
    @MainActor
    func testRooms_NoAdminControlsAreDrawn() throws {
        for mode in [Self.ordinaryTypeSize, "UICTContentSizeCategoryAccessibilityXXXL"] {
            let app = roomsOpen(mode: mode)
            XCTAssertTrue(roomsOpenRoom(app, "kitchen"), "the kitchen card did not open at \(mode)")
            evidence(app, "rooms-24-no-admin-\(mode)")
            for forbidden in RoomsStringsUnderTest.adminControls {
                XCTAssertFalse(
                    app.buttons[forbidden].exists,
                    "`\(forbidden)` is drawn at \(mode) — it is an admin-tier "
                        + "control and would fail every time from this phone"
                )
            }
            // The facts stay, and one line says where the buttons are.
            XCTAssertTrue(
                roomsWaitFor(app, RoomsStringsUnderTest.upgradeIsOnTheDashboard),
                "nothing says where restarting and upgrading are done"
            )
            app.terminate()
        }
    }

    /// `hang up` is offered only for a room that is in a call — and no room
    /// here is.
    ///
    /// MAP's case the one way round the house can support: `in_call_with` is
    /// not in the roster the house serves (see this extension's note), so what
    /// is testable is that the control is absent and the honest sentence is
    /// drawn. The present-case half is pinned in `RoomsRulesTests` against
    /// ``roomsDropInState(_:)``.
    @MainActor
    func testRooms_HangUpIsNotOfferedWhenNoRoomIsInACall() throws {
        let app = roomsOpen()
        XCTAssertTrue(roomsOpenRoom(app, "kitchen"), "the kitchen card did not open")
        XCTAssertTrue(
            roomsWaitFor(app, RoomsStringsUnderTest.dropInLabel),
            "the overview has no drop-in section"
        )
        evidence(app, "rooms-25-dropin")
        XCTAssertFalse(
            app.buttons[RoomsStringsUnderTest.hangUp].exists,
            "`hang up` is drawn for a room that is not in a call"
        )
        // The house's roster sends no `full_duplex`, so this room reads as one
        // whose mic cannot do drop-in at all — which is the honest sentence
        // for the data, and not the `starting a drop-in needs the dashboard`
        // one.
        XCTAssertTrue(
            containing(app, RoomsStringsUnderTest.noAecMic).exists,
            "a room with no echo-cancelling array does not say so"
        )
    }

    /// The health read's two maintenance lines, and the room that has neither.
    @MainActor
    func testRooms_RebootNeededSaysWhatToDo() throws {
        let app = roomsOpen()
        XCTAssertTrue(roomsOpenRoom(app, "kitchen"), "the kitchen card did not open")
        XCTAssertTrue(
            roomsWaitFor(app, RoomsStringsUnderTest.rebootNeeded),
            "a room whose health says a reboot is wanted does not say so"
        )
        evidence(app, "rooms-26-reboot")
        XCTAssertTrue(
            containing(app, "Unplug it for a few seconds").exists,
            "the reboot line does not say what to do about it"
        )
        XCTAssertTrue(
            containing(app, "kernel updated").exists,
            "the reason the satellite gave is not on screen"
        )
        XCTAssertTrue(
            containing(app, RoomsStringsUnderTest.bootNetworkStep).exists,
            "the boot network step is not drawn for a sample that has one"
        )
        XCTAssertTrue(
            containing(app, "wpa_supplicant").exists,
            "the boot step's own detail is not on screen"
        )
        // **The other room draws none of it**: `study` answers
        // `{"latest": null}`, so a panel built from a stale read would show
        // the kitchen's reason here.
        app.buttons[RoomsStringsUnderTest.closeRoom].firstMatch.tap()
        XCTAssertTrue(roomsOpenRoom(app, "study"), "the study card did not open")
        XCTAssertTrue(
            roomsWaitFor(app, RoomsStringsUnderTest.upgradeIsOnTheDashboard),
            "the study's maintenance section did not draw"
        )
        evidence(app, "rooms-26-reboot-other")
        XCTAssertFalse(
            containing(app, "kernel updated").exists,
            "a room with no health document is showing another room's reason"
        )
        XCTAssertFalse(
            containing(app, RoomsStringsUnderTest.rebootNeeded).exists,
            "a room with no health document is drawing the reboot pill"
        )
    }

    /// The settings tab shows what the room is set to and offers no way to
    /// change it.
    ///
    /// The values are the server's — `Hello Domo` is the `choice_labels` entry
    /// for `hello_domo`, so a row that printed the raw value would fail — and
    /// there is no switch, no dropdown and no save anywhere on the tab, which
    /// is what `PATCH …/config` being admin means for a phone.
    @MainActor
    func testRooms_ConfigFieldsAreReadOnly() throws {
        let app = roomsOpen()
        XCTAssertTrue(roomsOpenRoom(app, "kitchen"), "the kitchen card did not open")
        XCTAssertTrue(roomsTab(app, RoomsStringsUnderTest.tabSettings), "no settings tab")
        XCTAssertTrue(roomsWaitFor(app, "microphone"), "the config fields are not drawn")
        evidence(app, "rooms-27-config")
        XCTAssertTrue(
            containing(app, "Hello Domo").exists,
            "a `choice` field drew its raw value instead of its `choice_labels` entry"
        )
        XCTAssertTrue(containing(app, "6 dB").exists, "an `int` field dropped its unit")
        XCTAssertTrue(
            containing(app, RoomsStringsUnderTest.configIsReadOnly).exists,
            "nothing says where this room's settings are changed"
        )
        // Quiet hours is the one writable thing on this tab, and its radios
        // are buttons rather than switches — so a switch anywhere here is a
        // config field that was made editable.
        XCTAssertEqual(
            app.switches.count,
            0,
            "the settings tab has \(app.switches.count) switch(es) on it — "
                + "`PATCH …/config` is admin and nothing here may be edited"
        )
        for forbidden in RoomsStringsUnderTest.adminControls {
            XCTAssertFalse(
                app.buttons[forbidden].exists,
                "`\(forbidden)` is drawn on the settings tab"
            )
        }
    }

    /// The sessions tab draws the room's sessions, and a tap expands one into
    /// **its own** turns.
    ///
    /// The two decisions this tab makes are both asserted with data that
    /// discriminates:
    ///
    ///  * the subtitle shows the **first eight characters** of a uuid — the
    ///    fixture's ids are full-length on purpose, so a port that printed the
    ///    whole thing would not match `3fa85f64` as a prefix of the row's own
    ///    short form — and `person #4` is on the attributed session and absent
    ///    from the other;
    ///  * tapping expands `roomsTurns(of:in:)`, which filters the
    ///    conversations read by `session_id`. `marmalade` is in turn 101
    ///    (session `3fa85f64…`) and `turn the lamp off` is turn 104 (session
    ///    `7b1d2c48…`), so a filter that was dropped — or keyed on the room
    ///    instead — shows both and fails here.
    @MainActor
    func testRooms_SessionsTabExpandsOneSessionsOwnTurns() throws {
        let app = roomsOpen()
        XCTAssertTrue(roomsOpenRoom(app, "kitchen"), "the kitchen card did not open")
        XCTAssertTrue(roomsTab(app, RoomsStringsUnderTest.tabSessions), "no sessions tab")

        XCTAssertTrue(
            roomsWaitFor(app, "3fa85f64"),
            "the sessions tab never drew the first session's short id"
        )
        // Both rows are `accessibilityElement(children: .combine)`, so each
        // row's words are one label: `3 turns`, the subtitle and `last …`.
        XCTAssertTrue(
            containing(app, "3 turns").exists,
            "the attributed session's turn count is not drawn"
        )
        XCTAssertTrue(
            containing(app, "person #4").exists,
            "`person #4` is not drawn on the session that has a person"
        )
        XCTAssertTrue(
            containing(app, "7b1d2c48").exists,
            "the unattributed session is not drawn at all"
        )
        evidence(app, "rooms-28-sessions")

        let first = app.buttons
            .matching(NSPredicate(format: "label CONTAINS %@", "3fa85f64"))
            .firstMatch
        XCTAssertTrue(first.waitForExistence(timeout: 20), "the first session is not a button")
        first.tap()
        XCTAssertTrue(
            roomsWaitFor(app, "marmalade"),
            "tapping the session did not expand its turns"
        )
        XCTAssertFalse(
            containing(app, "turn the lamp off").exists,
            "the expanded session is showing the OTHER session's turn, so the "
                + "`session_id` filter is not being applied"
        )
        evidence(app, "rooms-28b-session-turns")
    }

    /// The timers tab's remaining column counts down between fetches.
    ///
    /// The clock is the point: `pasta water` is seeded 480 s out, so its
    /// remaining column is the only one on the tab in single-digit minutes —
    /// `laundry` is `1h 30m` and the masked reminder is `50m 00s`, so a
    /// `^<d>m <dd>s$` match picks the one row that matters without depending
    /// on the exact second the read landed. A `clockMs` that is not
    /// observable — a
    /// computed property over the injected closure, which is what shipped
    /// first — freezes the text at whatever it was when the tab was built, and
    /// this case is what fails. Android ticks for the same reason
    /// (`SatHistoryTabs.kt:447-453`).
    @MainActor
    func testRooms_TimerRemainingCountsDown() throws {
        let app = roomsOpen()
        XCTAssertTrue(roomsOpenRoom(app, "kitchen"), "the kitchen card did not open")
        XCTAssertTrue(roomsTab(app, RoomsStringsUnderTest.tabTimers), "no timers tab")
        XCTAssertTrue(roomsWaitFor(app, "pasta water"), "the timers tab drew no timer")

        let countdown = NSPredicate(format: "label MATCHES %@", "^[0-9]m [0-5][0-9]s$")
        let column = app.staticTexts.matching(countdown).firstMatch
        XCTAssertTrue(
            column.waitForExistence(timeout: 20),
            "no single-digit-minute remaining column for a timer seeded 480s out"
        )
        let before = column.label
        evidence(app, "rooms-33b-countdown-before")

        // Four seconds, not one: the tick's period is a second and the first
        // one may already have gone by when the label was read.
        Thread.sleep(forTimeInterval: 4)
        let frozen = app.staticTexts.matching(NSPredicate(format: "label == %@", before)).firstMatch
        if frozen.exists {
            print("=== THE COUNTDOWN DID NOT MOVE (still \(before)) — tree follows ===")
            print(app.debugDescription)
        }
        XCTAssertFalse(
            frozen.exists,
            "the remaining column still reads \(before) four seconds on, so nothing "
                + "in the timers tab is redrawn by the tick"
        )
        let after = app.staticTexts.matching(countdown).firstMatch
        XCTAssertTrue(after.exists, "the remaining column is gone, not counting down")
        XCTAssertNotEqual(after.label, before, "the countdown is frozen")
        evidence(app, "rooms-33c-countdown-after")
    }

    /// An offline room's volume controls are not live.
    ///
    /// `POST …/volume` 404s when the room isn't connected
    /// (`web/backend/api/satellites.py:1557-1573`), so a slider that could be
    /// dragged there is the control Kamron's rule forbids; Android gates all
    /// three on `enabled = online` (`SatOverviewTab.kt:304-320`). The house's
    /// `study` is offline and its `kitchen` is not, so this discriminates: the
    /// same assertion is made the other way round on `kitchen`.
    @MainActor
    func testRooms_VolumeIsNotLiveForAnOfflineRoom() throws {
        let app = roomsOpen()
        XCTAssertTrue(roomsOpenRoom(app, "study"), "the study card did not open")
        let slider = app.sliders.firstMatch
        XCTAssertTrue(slider.waitForExistence(timeout: 20), "the study has no volume slider")
        XCTAssertFalse(
            slider.isEnabled,
            "the volume slider is live for an offline room, and every POST it sends 404s"
        )
        XCTAssertFalse(
            app.buttons[RoomsStringsUnderTest.louder].isEnabled,
            "the `louder` nudge is live for an offline room"
        )
        evidence(app, "rooms-20b-volume-offline")

        let closer = app.buttons[RoomsStringsUnderTest.closeRoom]
        XCTAssertTrue(closer.exists, "no close control on the study")
        closer.tap()
        XCTAssertTrue(roomsOpenRoom(app, "kitchen"), "the kitchen card did not open")
        let live = app.sliders.firstMatch
        XCTAssertTrue(live.waitForExistence(timeout: 20), "the kitchen has no volume slider")
        XCTAssertTrue(live.isEnabled, "the volume slider is disabled for a room that IS online")
    }

    /// The notes tab draws the room's notes.
    @MainActor
    func testRooms_NotesTabDrawsWhatWasJottedDown() throws {
        let app = roomsOpen()
        XCTAssertTrue(roomsOpenRoom(app, "kitchen"), "the kitchen card did not open")
        XCTAssertTrue(roomsTab(app, RoomsStringsUnderTest.tabNotes), "no notes tab")
        XCTAssertTrue(
            roomsWaitFor(app, "the recycling goes out on Thursday"),
            "the notes tab did not draw the room's note"
        )
        evidence(app, "rooms-29-notes")
    }

    /// The screen grows with the type size — **measured**, not read off the
    /// audit's headline.
    ///
    /// A named Dynamic Type finding has twice been a false positive on this
    /// track, so this compares the same two elements' heights at the ordinary
    /// size and at AX5: the room card on the roster, and a timer row inside a
    /// room.
    @MainActor
    func testRooms_TheScreenGrowsWithTheTypeSize() throws {
        var card: [String: CGFloat] = [:]
        var timer: [String: CGFloat] = [:]
        for (name, mode) in [
            ("ordinary", Self.ordinaryTypeSize),
            ("AX5", "UICTContentSizeCategoryAccessibilityXXXL"),
        ] {
            let app = roomsOpen(mode: mode)
            let kitchen = app.buttons
                .matching(NSPredicate(format: "label BEGINSWITH %@", "kitchen"))
                .firstMatch
            XCTAssertTrue(kitchen.waitForExistence(timeout: 25), "no kitchen card at \(name)")
            card[name] = kitchen.frame.height
            XCTAssertTrue(roomsOpenRoom(app, "kitchen"), "the kitchen card did not open at \(name)")
            XCTAssertTrue(roomsTab(app, RoomsStringsUnderTest.tabTimers), "no timers tab at \(name)")
            let row = containing(app, "pasta water")
            XCTAssertTrue(row.waitForExistence(timeout: 25), "no timer row at \(name)")
            timer[name] = row.frame.height
            evidence(app, "rooms-30-type-\(name)")
            print("ROOMS SCALE [\(name)] card=\(card[name] ?? -1) timer=\(timer[name] ?? -1)")
            app.terminate()
        }
        XCTAssertGreaterThan(
            card["AX5"] ?? 0,
            (card["ordinary"] ?? 0) * 1.3,
            "the room card is \(card["AX5"] ?? 0) points tall at AX5 against "
                + "\(card["ordinary"] ?? 0) at the ordinary size — it is not growing"
        )
        XCTAssertGreaterThan(
            timer["AX5"] ?? 0,
            (timer["ordinary"] ?? 0) * 1.3,
            "the timer row is \(timer["AX5"] ?? 0) points tall at AX5 against "
                + "\(timer["ordinary"] ?? 0) at the ordinary size"
        )
    }

    /// Apple's audit, on the roster and on an opened room.
    ///
    /// Classified by **signature** (``floorAndRest(_:)``), never by a pinned
    /// total: this is a pushed screen, so it sits on the measured
    /// ``knownPushedBarFloor`` of anonymous Dynamic Type findings, and
    /// anything the audit can attribute to an element is a defect to fix.
    @MainActor
    func testRooms_Audit() throws {
        let app = roomsOpen(pushDelay: Self.pushDelay)
        XCTAssertTrue(
            roomsSummaryText(app).waitForExistence(timeout: 30),
            "the roster did not draw, so there is nothing to audit"
        )
        evidence(app, "rooms-31-audit-roster")
        let roster = try auditIssues(app, "rooms-roster")
        let rosterSplit = Self.floorAndRest(roster)
        let rosterRest = roomsOursOnly(rosterSplit.rest)
        print("ROOMS AUDIT roster floor=\(rosterSplit.floor) rest=\(rosterSplit.rest.count) ours=\(rosterRest.count)")

        XCTAssertTrue(roomsOpenRoom(app, "kitchen"), "the kitchen card did not open")
        XCTAssertTrue(roomsWaitFor(app, RoomsStringsUnderTest.roomIDLabel), "the overview did not draw")
        evidence(app, "rooms-31-audit-detail")
        let detail = try auditIssues(app, "rooms-detail")
        let detailSplit = Self.floorAndRest(detail)
        let detailRest = roomsOursOnly(detailSplit.rest)
        print("ROOMS AUDIT detail floor=\(detailSplit.floor) rest=\(detailSplit.rest.count) ours=\(detailRest.count)")

        XCTAssertTrue(
            rosterRest.isEmpty,
            "Apple's audit has findings against the Rooms roster beyond the "
                + "measured floor: " + rosterRest.joined(separator: " / ")
        )
        XCTAssertTrue(
            detailRest.isEmpty,
            "Apple's audit has findings against an opened room beyond the "
                + "measured floor: " + detailRest.joined(separator: " / ")
        )
        // **Two floors, because they were measured apart.** On train S004 the
        // roster reported no anonymous Dynamic Type finding at all and the
        // opened room reported the eight of ``knownPushedBarFloor``. One
        // number for both would have had to be the larger, and then a roster
        // that started reporting eight — a real regression in the bars — would
        // have read as normal.
        XCTAssertEqual(
            rosterSplit.floor,
            0,
            "the Rooms roster's anonymous floor is \(rosterSplit.floor), not the "
                + "measured 0: " + roster.joined(separator: " | ")
        )
        XCTAssertEqual(
            detailSplit.floor,
            Self.knownPushedBarFloor,
            "an opened room's anonymous floor is \(detailSplit.floor), not the "
                + "known \(Self.knownPushedBarFloor): " + detail.joined(separator: " | ")
        )
    }

    /// Drop the one audit finding that belongs to a view this item does not
    /// own, and say how many were dropped.
    ///
    /// Apple fails the contrast of **`back to Rooms & devices`** — the button
    /// `RouteScreen.swift:296` draws at the foot of *every* Settings section,
    /// in the accent colour, 20.3 points tall. It is the shell's control, not
    /// this screen's: NIGHT-RULES §2 says this item may replace only its own
    /// `// slot:` line in that file, and a tint change there would land on
    /// eleven other sections nobody measured tonight. It is written up in
    /// `overnight/rooms/DECISIONS.md` under "Needs a shared change".
    ///
    /// Matched by **label and headline together**, so this hides exactly that
    /// finding: a second contrast failure, or a hit-area failure against the
    /// same button, still fails the audit. Nothing of this screen's own is
    /// excused here.
    @MainActor
    func roomsOursOnly(_ found: [String]) -> [String] {
        found.filter { line in
            !(line.hasPrefix("Contrast failed")
                && line.contains("label='\(ShellStringsUnderTest.backToRooms)'"))
        }
    }
}

/// The words these cases match on, spelled out rather than imported.
///
/// Deliberately **not** `@testable import DomovoiKit`: a UI test drives the app
/// through the same surface a person does, and a query that silently followed a
/// renamed string would stop testing what it claims to. Each one is
/// ``RoomsStrings``' value, or the stub house's own data.
enum RoomsStringsUnderTest {

    /// ``RoomsStrings/summary(online:offline:provisioned:)`` for the house's
    /// two rooms. **U+00B7 with one space each side.**
    static let summary = "1 online · 1 offline · 2 provisioned"

    static let activeNow = "active now"
    static let noMusic = "no music"
    static let closeRoom = "close this room"

    static let roomIDLabel = "room id"
    static let registryDefault = "registry default"
    static let lastConnected = "last connected"

    static let send = "send"
    /// The section's heading (``RoomsStrings/announceTo(room:)``), which is a
    /// `Text` of its own.
    static let announceToKitchen = "announce to kitchen"
    /// The **field**'s label, which is its placeholder
    /// (``RoomsStrings/announcePlaceholder(room:)``) and is deliberately not
    /// the heading above it. **The ellipsis is U+2026.**
    static let announceFieldLabel = "speak in the kitchen room…"
    static let announcedToKitchen = "announced to kitchen"
    /// ``RoomsStrings/acceptedButDidNotPlay(_:)`` — the sentence the whole
    /// `announced_to` reading exists for. Matched on its tail, because the
    /// **U+2014 em dash** in the middle is the part most likely to be
    /// mistyped in a test and the part that must not change in the app.
    static let acceptedButDidNotPlay = "accepted the request but didn't play — connection may be dead"

    static let broadcastHeading = "Broadcast"
    /// ``RoomsStrings/broadcastFieldLabel`` — iOS only, and drawn nowhere
    /// else on the card.
    static let broadcastFieldLabel = "broadcast to every room"
    static let broadcastButton = "broadcast"
    /// ``HomeToastStrings/broadcastedToPrefix`` plus ``plural(_:_:_:)``.
    static let broadcastedToOneRoom = "broadcasted to 1 room"
    /// ``HomeToastStrings/noRoomsAccepted``.
    static let noRoomsAccepted = "broadcast queued but no rooms accepted it"

    /// ``RoomsStrings/volumeSet(room:level:)``'s start, without the level.
    static let volumeSetPrefix = "kitchen volume set to"

    /// ``RoomsStrings/volumeHelp(room:levelKnown:online:)``'s start. The whole sentence is
    /// 103 characters with the room and the parenthetical, and a query string
    /// over 128 throws (train S001) — this is matched as a `CONTAINS` prefix.
    static let volumeHelpPrefix = "master output"

    /// The `+5` nudge's accessibility label (``RoomsStrings/louder``), which
    /// is the only words that icon-only button has.
    static let louder = "louder"

    static let timerScopeLabel = "Only reminders for this device"
    /// ``RoomsStrings/timerScopeToast(room:on:)`` with `on: true`.
    static let ownTimersOnly = "kitchen now announces only its own timers and reminders"

    /// ``RoomsStrings/quietModes``' second entry.
    static let quietCustom = "Custom hours"
    static let quietFrom = "from"
    static let quietSave = "Save"
    static let timesLookLike = "times look like 22:00"
    /// ``RoomsStrings/quietToast(room:mode:start:end:)``'s custom branch, up
    /// to the normalised time.
    static let keepsQuiet = "kitchen keeps quiet 07:05"

    /// `SAT_TIMER_WORDS_HIDDEN` — rule M1.
    static let timerWordsHidden = "reminder (words hidden)"
    static let cancel = "cancel"
    /// ``RoomsStrings/cancelled(kind:id:)`` for the fixture's first timer.
    static let cancelledTimer = "cancelled timer #41"

    static let searchThisRoom = "search this room…"
    /// ``RoomsStrings/countOf(_:_:)`` once `marmalade` has filtered four turns
    /// to one.
    static let oneOfFour = "1 of 4"
    static let more = "more"
    static let less = "less"

    static let add = "add"
    /// The stub's own refusal, surfaced as the server wrote it.
    static let noFulfiller = "no fulfiller plugin is installed"

    static let dropInLabel = "drop in"
    static let hangUp = "hang up"
    static let noAecMic = "this room's mic can't do drop-in"

    static let rebootNeeded = "reboot needed"
    static let bootNetworkStep = "boot network step"
    static let upgradeIsOnTheDashboard =
        "restarting and upgrading a room are done from the dashboard on a computer"
    static let configIsReadOnly = "Changing any of it is done from the dashboard on a computer."

    /// The sessions tab's empty state. No UI case asserts it — the house
    /// really serves two sessions (see this extension's header) — but it is
    /// the string a case for an empty room would need, and
    /// `RoomsRulesTests` pins the wording.
    static let noSessions = "no sessions in this room"

    static let tabSessions = "sessions"
    static let tabConversations = "conversations"
    static let tabRecentlyPlayed = "recently played"
    static let tabNotes = "notes"
    static let tabTimers = "timers"
    static let tabSettings = "settings"

    /// Every control Android draws that this phone cannot work, by the label
    /// it would carry. ``testRooms_NoAdminControlsAreDrawn`` asserts none of
    /// them exists; each would 401, 403 or 501 every time.
    static let adminControls = ["restart", "upgrade", "save & restart", "start", "reboot"]
}
