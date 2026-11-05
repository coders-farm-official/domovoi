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
/// The one exception is D54's cases. A phone can only drop into a room whose
/// microphone can do two-way audio, and `full_duplex` is on the roster row
/// and nowhere else — so against the house there is no room this phone could
/// call. The control is still **drawn** for such a room, and `disabled`, with
/// ``RoomsStrings/noAecMic`` the line above it saying why (Android composes
/// its button after the `!s.full_duplex` arm too, `SatOverviewTab.kt:564`,
/// and the gate is `roomsPhoneDropInEnabled(_:callIsBusy:)`) — so what the
/// house's roster cannot give a case is a drop-in it can **press**.
/// `fixtures/rooms.py` therefore claims `/api/satellites` **conditionally**,
/// as `fixtures/manual.py` already does: unless a case arms `rooms-roster`
/// the fixture answers False for that path and the request falls through to
/// the house, so nothing else on this screen — or on Home — changes.
/// ``roomsOpen(mode:pushDelay:roster:micSeam:)`` is where it is armed.
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
    /// `roster` arms the fixture's own roster **before the launch**, which is
    /// the only way a room with an echo-cancelling array reaches this screen:
    /// the house's roster sends no `full_duplex` (see this extension's note),
    /// and a room without it draws the drop-in control `disabled` under
    /// ``RoomsStrings/noAecMic`` — honest, and not something a case can
    /// press. The fixture answers that path only when it is armed and
    /// otherwise leaves it to the house, so every case that passes nothing
    /// here sees what it saw before (`fixtures/rooms.py`, `_roster`).
    @MainActor
    func roomsOpen(
        mode: String = StubHouseUITests.ordinaryTypeSize,
        pushDelay: Double = 3,
        roster: String? = nil,
        micSeam: String? = nil
    ) -> XCUIApplication {
        control("reset-fixtures", "{}", port: 6601, what: "the Rooms fixtures")
        if let roster {
            control(
                "rooms-roster",
                "{\"how\": \"\(roster)\"}",
                port: 6601,
                what: "a roster with an echo-cancelling room on it"
            )
        }
        let app = micSeam == nil
            ? launch(mode: mode, push: "Satellites", pushDelay: pushDelay)
            : roomsLaunch(mode: mode, pushDelay: pushDelay, micSeam: micSeam!)
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

    /// ``launch(mode:paired:server:port:strip:push:pushDelay:args:)`` with one
    /// more launch argument on it: ``DropInTestSeam``'s.
    ///
    /// It was a **copy** of `launch`'s body on this unit's branch, because
    /// `launch` is in `StubHouseUITests.swift`, which this unit did not own,
    /// and `launchArguments` must be set before `launch()`. The `args:`
    /// parameter this unit asked for under "needs a shared change" landed in
    /// the same batch, so the body is one call now and cannot drift from the
    /// real launcher.
    @MainActor
    func roomsLaunch(mode: String, pushDelay: Double, micSeam: String) -> XCUIApplication {
        // **`launch(args:)` and not a copy of its body.** This used to write
        // the whole launcher out again, because `launchArguments` must be set
        // before `launch()` and there was no way to add one argument. The
        // `args:` parameter landed on the transfers unit's branch in this same
        // batch, so the duplicate — which drifted from `launch()` silently,
        // and had already lost `wait(for: .runningForeground)`'s siblings once
        // — is gone on the integration.
        //
        // `-domovoi-dropin-mic` replaces the microphone, the audio session and
        // the engine with stand-ins. Everything else about the call is the
        // app's own — ``DropInTestSeam`` has the argument for why this exists
        // and what it is not allowed to do.
        launch(
            mode: mode,
            push: "Satellites",
            pushDelay: pushDelay,
            args: ["-domovoi-dropin-mic", micSeam]
        )
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
    ///
    /// ### What says it opened, since D56
    ///
    /// A room is its own **pushed screen** now (Kamron, answer 56, by
    /// recommendation), so there is no `close this room` button left to wait
    /// for. The marker is ``roomsPushedTitle(_:_:)`` — the pushed screen's
    /// own inline navigation title, which is the room's id
    /// (``RoomsRoomScreen``). That is a true statement that the push landed
    /// and it is not one of the things any case then asserts: the roster
    /// draws the room's name on a **card**, never in a navigation bar.
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
        guard roomsPushedTitle(app, room).waitForExistence(timeout: 20) else {
            print("=== \(room) DID NOT OPEN — tree follows ===")
            print(app.debugDescription)
            return false
        }
        return true
    }

    /// The pushed room screen's own navigation title.
    @MainActor
    func roomsPushedTitle(_ app: XCUIApplication, _ room: String) -> XCUIElement {
        app.navigationBars.staticTexts[room]
    }

    /// Leave a pushed room the way a person does, and say whether the roster
    /// came back.
    ///
    /// `swipe: true` is the **edge drag** — the interactive pop D56 is for.
    /// A press of a twelfth of a second and then a drag across the window:
    /// shorter and it is a tap on whatever sits at the left edge, and a
    /// `swipeRight()` flick starts at the window's centre, which is inside
    /// ``RoomsTabStrip`` and gets eaten by it (this file's
    /// ``roomsBring(_:into:vertical:flicks:)`` note).
    ///
    /// `swipe: false` taps the back button instead, which is what the cases
    /// that merely need to be back on the roster use: the gesture is the
    /// thing under test in exactly one case, and everywhere else it would be
    /// a second way to fail for a reason that is not the case's own.
    @MainActor
    func roomsLeaveRoom(_ app: XCUIApplication, _ room: String, swipe: Bool) -> Bool {
        let title = roomsPushedTitle(app, room)
        if swipe {
            let window = app.windows.firstMatch
            let edge = window.coordinate(withNormalizedOffset: CGVector(dx: 0.004, dy: 0.5))
            edge.press(
                forDuration: 0.08,
                thenDragTo: window.coordinate(withNormalizedOffset: CGVector(dx: 0.95, dy: 0.5))
            )
        } else {
            let back = app.navigationBars.buttons.firstMatch
            guard back.waitForExistence(timeout: 20) else {
                print("=== NO BACK BUTTON OVER \(room) — tree follows ===")
                print(app.debugDescription)
                return false
            }
            back.tap()
        }
        // Both halves, and not just the title going: a pop that landed
        // somewhere else entirely would also take the title away.
        let deadline = Date().addingTimeInterval(15)
        while Date() < deadline {
            if !title.exists && roomsSummaryText(app).exists { return true }
            usleep(300_000)
        }
        print("=== \(room) DID NOT POP (swipe: \(swipe)) — tree follows ===")
        print(app.debugDescription)
        return false
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
    /// ### Why containment is measured on the scroll axis alone
    ///
    /// Train S008's one failure, and the third time this helper's *predicate*
    /// rather than its gesture was the bug. `CGRect.contains(_:)` asks about
    /// both axes, and a tab inside ``RoomsTabStrip`` is **exactly as tall as
    /// the strip** — the strip's height *is* one tab's height. The two are
    /// then equal to within whatever sub-point difference SwiftUI's layout
    /// rounding leaves between a clip and its content, and at AX5 that
    /// difference fell the wrong way: `timers` sat at x 29.7–183.0 in a strip
    /// spanning 16.0–377.0, horizontally inside by a wide margin, and
    /// `contains` still said no because its bottom edge was a fraction below
    /// the strip's. ``roomsShortfall`` (which looks at x only) therefore
    /// returned 0, no drag was made, the loop spun ten times moving nothing,
    /// and the flick fallback oscillated past the tab ten more times. The tree
    /// printed at the failure showed `timers` plainly in the strip — the
    /// reason two readings of it found nothing wrong.
    ///
    /// Containment across the scroll axis is also the only part that bears on
    /// tappability: a control clipped off the side cannot be touched, while
    /// one whose own height matches its clip's is as visible as it will ever
    /// be. So this compares the axis being scrolled, with a half-point
    /// tolerance for the same rounding.
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
            if roomsInside(it, seat, vertical: vertical) { return true }
            if vertical {
                if it.midY > seat.midY { view.swipeUp(velocity: .slow) } else { view.swipeDown(velocity: .slow) }
            } else if !roomsDrag(view, by: roomsShortfall(it, in: seat)) {
                break
            }
        }
        if roomsInside(element.frame, view.frame, vertical: vertical) { return true }
        // Fallback: the old flicks, in case the drag gesture moved nothing.
        for _ in 0..<flicks {
            let seat = view.frame
            let it = element.frame
            if roomsInside(it, seat, vertical: vertical) { return true }
            if vertical {
                if it.midY > seat.midY { view.swipeUp(velocity: .slow) } else { view.swipeDown(velocity: .slow) }
            } else {
                if it.midX > seat.midX { view.swipeLeft() } else { view.swipeRight() }
            }
        }
        return roomsInside(element.frame, view.frame, vertical: vertical)
    }

    /// Whether `it` is wholly inside `seat` **along the axis being scrolled**.
    ///
    /// The tolerance absorbs SwiftUI's layout rounding between a clip and its
    /// content; see ``roomsBring(_:into:vertical:flicks:)`` for the AX5
    /// failure that paid for it. It is half a point — far smaller than any
    /// real clipping, which is tens of points — so a control genuinely cut off
    /// is still reported as outside.
    func roomsInside(_ it: CGRect, _ seat: CGRect, vertical: Bool) -> Bool {
        let slack: CGFloat = 0.5
        return vertical
            ? (it.minY >= seat.minY - slack && it.maxY <= seat.maxY + slack)
            : (it.minX >= seat.minX - slack && it.maxX <= seat.maxX + slack)
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
    ///
    /// A shortfall of zero means **no drag was attempted**, so it answers
    /// false: ``roomsBring`` has already asked whether the element is inside,
    /// and spinning its loop over a gesture that moves nothing is how train
    /// S008 burned ten iterations before reaching the flick fallback.
    @MainActor
    func roomsDrag(_ view: XCUIElement, by dx: CGFloat) -> Bool {
        guard dx != 0 else { return false }
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

    /// Move the page's content **up** by `points` (negative moves it down),
    /// with no momentum — the vertical sibling of ``roomsDrag(_:by:)``, for a
    /// few points rather than a tab's width.
    ///
    /// Written for the drop-in case's audit position (train I006), where the
    /// whole move is 3.7 points plus one, and the numbers are the ones this
    /// suite has already measured rather than new ones:
    ///
    /// * **`withVelocity: .slow` and a hold before the lift.** The
    ///   two-argument `press(forDuration:thenDragTo:)` ends in a flick —
    ///   train R002 measured a drag asked for 143 points moving a page by
    ///   294 (``musicMoreDrag(_:by:vertical:)``'s doc comment) — and lifting a
    ///   stationary finger is what stops the content where the finger did.
    /// * **No gesture under 24 points.** Less than a scroll view's pan slop is
    ///   not a pan but a long press on whatever is under the finger. A move
    ///   smaller than that is made as **two** pans, 24 points the other way
    ///   and then 24 plus the move this way, so each gesture is a real pan
    ///   and the net is the move asked for. The caller re-measures, so what
    ///   the two pans actually moved is a reading, not an assumption.
    /// * **Down the left margin**, `dx: 0.03` (x ≈ 12) as in
    ///   ``printingDrag(_:by:)``: every row on a room's page starts at x = 16,
    ///   so the finger hit-tests to the scroll view and holds no control, and
    ///   the tab strip (x 16–377) never takes the pan.
    /// * At most 80% of the page's height per gesture, starting near the edge
    ///   the finger travels away from, so the whole gesture stays on the page.
    ///
    /// Answers false when nothing was asked for or the page is too short to
    /// pan, so the caller can say that no gesture was made.
    @MainActor
    @discardableResult
    func roomsDragVertically(_ page: XCUIElement, contentUpBy points: CGFloat) -> Bool {
        let height = page.frame.height
        guard points != 0, height > 80 else { return false }
        let leastTravel: CGFloat = 24
        /// One pan; `finger` is negative for a finger moving up.
        func pan(_ finger: CGFloat) {
            let travel = max(-height * 0.8, min(height * 0.8, finger))
            let near: CGFloat = travel < 0 ? 0.9 : 0.1
            let from = page.coordinate(withNormalizedOffset: CGVector(dx: 0.03, dy: near))
            from.press(
                forDuration: 0.4,
                thenDragTo: from.withOffset(CGVector(dx: 0, dy: travel)),
                withVelocity: .slow,
                thenHoldForDuration: 0.4
            )
        }
        // The finger moves the opposite way to the reading: content up is a
        // finger moving up, which is a negative dy.
        let finger = -points
        if abs(finger) >= leastTravel {
            pan(finger)
        } else {
            let away: CGFloat = finger < 0 ? 1 : -1
            pan(away * leastTravel)
            pan(finger - away * leastTravel)
        }
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

    /// The other direction: wait until nothing on screen carries `text`.
    ///
    /// **Why a wait and not one `XCTAssertFalse`.** Review round 2 found both
    /// drop-in cases ending on `app.buttons[dropInFromThisPhone].firstMatch`,
    /// which is the row **behind** the sheet (NIGHT-RULES §5) — so they
    /// passed whether the sheet had gone or not. The sheet's own line is what
    /// discriminates, and a sheet dismisses with an animation, so its absence
    /// has to be given the same patience its arrival gets.
    @MainActor
    func roomsWaitGone(_ app: XCUIApplication, _ text: String, timeout: Double = 20) -> Bool {
        let deadline = Date().addingTimeInterval(timeout)
        while Date() < deadline {
            if !containing(app, text).exists { return true }
            usleep(150_000)
        }
        print("=== “\(text)” NEVER WENT AWAY — tree follows ===")
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

    /// `dropin_seen.frames` as a number, from the same readback
    /// ``roomsAsk(_:port:)`` returns as text.
    ///
    /// Read rather than asserted-on-as-text because the one thing a case
    /// needs from it is **growth**: "more frames arrived after X than before
    /// it" is the only statement that separates a drawn sheet from a running
    /// call, and it cannot be made by looking for a substring. Parsed by
    /// hand, not through `JSONDecoder`, to keep this file free of a type for
    /// one integer; −1 when the key is missing, so a broken readback fails a
    /// comparison instead of passing one.
    @MainActor
    func roomsFrameCount(port: Int = 6601) -> Int {
        let said = roomsAsk("rooms-dropin", port: port)
        guard let at = said.range(of: "\"frames\":") else { return -1 }
        let digits = said[at.upperBound...]
            .drop(while: { $0 == " " })
            .prefix(while: { $0.isNumber })
        return Int(digits) ?? -1
    }

    /// Wait until the house has counted **more than** `n` microphone frames,
    /// and answer the count it reached.
    ///
    /// A frame is 30 ms, so "some have arrived" is true within a moment of a
    /// call going live — but not necessarily in the same instant the sheet
    /// says `live`, and a read that happened to land first would make a case
    /// about a working call fail for a reason that is not the case's. The
    /// poll is the honest shape: it is the growth that is being asserted, and
    /// a port where nothing travels still fails, on the deadline.
    @MainActor
    func roomsFramesPast(_ n: Int, timeout: Double = 20) -> Int {
        var last = -1
        let deadline = Date().addingTimeInterval(timeout)
        while Date() < deadline {
            last = roomsFrameCount()
            if last > n { return last }
            usleep(400_000)
        }
        return last
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

    /// Opening a room shows **that room's** overview, as a pushed screen a
    /// swipe takes back.
    ///
    /// The read rows name the room the card was for, and the two unknown
    /// branches are asserted as such: the house's roster sends no `voice` and
    /// no `mpd_ports`, so `registry default` and the em dash are the honest
    /// readings — and a port that invented a default would draw a voice name
    /// here.
    ///
    /// ### D56, and why the gesture is asserted here and nowhere else
    ///
    /// Kamron, answer 56, by recommendation: a room is its own pushed screen
    /// with swipe-back, not a panel with a `close this room` button. Three
    /// things make that true and all three are asserted here — a navigation
    /// bar over the room, **no** `close this room` anywhere, and the edge
    /// drag landing back on the roster. The other cases use the back button
    /// (``roomsLeaveRoom(_:_:swipe:)``), because a flaky gesture in twelve
    /// cases is twelve ways to fail for a reason that is not theirs.
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
        // The push itself: a navigation bar carrying this room's id, which
        // only a pushed destination draws.
        XCTAssertTrue(
            roomsPushedTitle(app, "kitchen").exists,
            "the room is on screen without a navigation title — it was drawn "
                + "in place of the roster rather than pushed over it"
        )
        // And the control D56 removed. A panel that still had it would pass
        // every other assertion in this case.
        XCTAssertFalse(
            app.buttons["close this room"].exists,
            "`close this room` is still drawn — a pushed screen is left by "
                + "the back button or the edge swipe, and a second way out "
                + "that says something else is the control Kamron's rule is "
                + "about"
        )
        XCTAssertTrue(
            roomsLeaveRoom(app, "kitchen", swipe: true),
            "the edge swipe did not take the room back to the roster"
        )
        // Back on the roster, and the room can be opened again: a pop that
        // left the model stopped would draw a roster that never re-read.
        XCTAssertTrue(
            roomsWaitFor(app, RoomsStringsUnderTest.activeNow),
            "the roster is not live again after the swipe back"
        )
        XCTAssertTrue(
            roomsOpenRoom(app, "kitchen"),
            "the kitchen would not open a second time after a swipe back"
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

    /// The recently-played rows name their source, and `+ add` is drawn
    /// **only where the server says it can be**.
    ///
    /// ### Three readings of one screen
    ///
    ///  1. **A refused add says what the house said**, and the optimistic
    ///     mark goes back. `can_add` on this branch's server is computed
    ///     *without* consulting a fulfiller
    ///     (`web/backend/api/satellites.py:1040-1043`) while the core's gate
    ///     is a fulfiller's URL matcher (`domovoi/main.py:5782-5788`), so a
    ///     403 is reachable from a row the server offered, and its own
    ///     sentence is the only one that tells a person what to do about it.
    ///  2. **One** `+ add` where the server says so, and pressing it queues
    ///     the track. Two buttons would mean the library row had one, which
    ///     is the "+ add on every row" port.
    ///  3. **No button at all** for a house whose rows all carry
    ///     `can_add=false`, and nothing said about it.
    ///
    /// ### Where D55 got to, and why half of it is here
    ///
    /// Kamron, answer 55: "no fulfiller means no button and no error: the
    /// server computes `can_add` against the fulfiller allowlist, so both
    /// phones draw it honestly." Reading 3 is that behaviour, and the app
    /// already does its half — the condition is `can_add` and only `can_add`.
    /// Reading 1 is what is still true until the **server's** half lands
    /// (it is built on `wf/ios-b-android`, which never merges to main): this
    /// unit had deleted the refusal sentence on the strength of the answer,
    /// review round 1 was right that it was premature, and it is back. On the
    /// day `can_add` consults the allowlist, reading 1 is the case to delete.
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
        // **One** may be added, and the server says which.
        XCTAssertEqual(
            adds.count,
            1,
            "there are \(adds.count) `add` buttons for one addable row"
        )

        // **Reading 1, first**, because it has to be: a successful add leaves
        // the row reading `queued` and the button disabled
        // (`RoomsHistoryViews.swift`, `add`), and a refusal puts the mark
        // back — so refusing first is what leaves `+ add` pressable for
        // reading 2.
        control("rooms-add", "{\"how\":\"refused\"}", port: 6601, what: "the add refusal")
        adds.element(boundBy: 0).tap()
        XCTAssertTrue(
            roomsWaitFor(app, RoomsStringsUnderTest.addRefusedDetail),
            "a refused add did not say what the house said about it — the "
                + "server's own sentence is the only one that says what to do"
        )
        evidence(app, "rooms-23-played-refused")
        // The mark went back, so the row can be asked for again. `+ add` and
        // not `queued`: a row left marked after a refusal is a row nothing
        // was queued for, saying it was.
        XCTAssertTrue(
            adds.element(boundBy: 0).waitForExistence(timeout: 20),
            "the row still reads `queued` after an add that was refused"
        )

        // Reading 2: the same press against a house that accepts it.
        control("rooms-add", "{\"how\":null}", port: 6601, what: "an add that is accepted")
        adds.element(boundBy: 0).tap()
        XCTAssertTrue(
            roomsWaitFor(app, RoomsStringsUnderTest.queuedForDownload),
            "pressing `add` said nothing about what happened to the track"
        )

        // Reading 3: the same house with no fulfiller installed — `can_add`
        // is false on every row, so there is nothing to draw.
        control("rooms-can-add", "{\"can_add\": false}", port: 6601, what: "a house with no fulfiller")
        XCTAssertTrue(
            roomsLeaveRoom(app, "kitchen", swipe: false),
            "the kitchen would not pop, so the second reading never happened"
        )
        XCTAssertTrue(roomsOpenRoom(app, "kitchen"), "the kitchen card did not open a second time")
        XCTAssertTrue(roomsTab(app, RoomsStringsUnderTest.tabRecentlyPlayed), "no recently-played tab")
        XCTAssertTrue(roomsWaitFor(app, "Lost Mariner"), "the external row is missing the second time")
        evidence(app, "rooms-23-played-no-fulfiller")
        let none = app.buttons
            .matching(NSPredicate(format: "label == %@", RoomsStringsUnderTest.add))
        XCTAssertEqual(
            none.count,
            0,
            "\(none.count) `add` buttons are drawn for a house whose server "
                + "offered none — a button that would fail every time"
        )
        // And **no explanation**, because there is nothing to explain: a row
        // the server did not offer is a row the screen says nothing about.
        // The refusal sentence of reading 1 belongs to a press, and there is
        // nothing here to press.
        XCTAssertFalse(
            containing(app, "no fulfiller").exists,
            "the screen explains a missing fulfiller although nothing was "
                + "pressed — answer 55 is that it says nothing and draws "
                + "nothing"
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

    /// **D54, end to end**: this phone drops into a room, the house hears it,
    /// and `hang up` ends the call.
    ///
    /// ### What is real here and what is not
    ///
    /// Real: the control, the `dropin/phone-info` read, the socket address
    /// built from that read, the household token on the upgrade, the frames
    /// travelling, the sheet's states, `mute`, `hang up`, and the house's own
    /// record of what arrived. Stood in for: the microphone prompt, the audio
    /// session and `AVAudioEngine` — the three things that need hardware
    /// (``DropInTestSeam``).
    ///
    /// The readback is what makes "the room hears this phone" a measurement:
    /// a sheet that says `live` while nothing leaves the device looks exactly
    /// like a working call from the outside, and that is the failure this
    /// case is for. `frames` is asserted as **more than none** and never as a
    /// count — 30 ms a frame against a Simulator under load is a different
    /// number every run.
    @MainActor
    func testRooms_DropInFromThisPhoneIsHeardAndHungUp() throws {
        let app = roomsOpen(roster: "full-duplex", micSeam: "pump")
        XCTAssertTrue(roomsOpenRoom(app, "kitchen"), "the kitchen card did not open")
        XCTAssertTrue(
            roomsWaitFor(app, RoomsStringsUnderTest.dropInLabel),
            "the overview has no drop-in section"
        )
        let drop = app.buttons[RoomsStringsUnderTest.dropInFromThisPhone].firstMatch
        XCTAssertTrue(
            drop.waitForExistence(timeout: 20),
            "a room with an echo-cancelling array offers no way to drop into it "
                + "from this phone"
        )
        evidence(app, "rooms-27-dropin-control")
        drop.tap()
        // The sheet, by the line that is only on it.
        XCTAssertTrue(
            roomsWaitFor(app, RoomsStringsUnderTest.twoWayAudioKitchen, timeout: 30),
            "tapping `drop in from this phone` opened no call"
        )
        XCTAssertTrue(
            roomsWaitFor(app, RoomsStringsUnderTest.livePrefix, timeout: 30),
            "the call never went live — the house sends `dropin_start` as soon "
                + "as the socket is up"
        )
        evidence(app, "rooms-27-dropin-live")

        // **Apple's audit, on the sheet** — review round 1 asked for this,
        // and the file's own header is the reason: the last dialog-shaped
        // view measured on this screen produced five `Contrast failed`
        // findings against views *behind* its scrim (train S009), which is
        // why ``DropInCallSheet`` is a real presentation and not a card in a
        // `ZStack`. That claim is only worth what a measurement says, so it
        // is measured, here, with the call live.
        //
        // Classified exactly as ``testRooms_Audit`` classifies the roster and
        // the room: `rest` is the defects, and the floor is the platform's
        // anonymous Dynamic Type findings. Printed rather than pinned,
        // because a sheet over a pushed screen is a shape nothing on this
        // track has measured before and the first train is what measures it;
        // an attributed finding fails the case from the first run.
        //
        // **What the first train measured, and the shape it is.** I004
        // returned five, every one `element: nil`: four
        // `Potentially inaccessible text` and one `Contrast nearly passed`.
        // None of them is the sheet's. Apple's element detection reads words
        // off the screenshot and looks for an accessibility element covering
        // them; under a presentation the two come apart, because the covered
        // page stays in the tree at the frames it had before the sheet went
        // up and the backdrop the system draws over it is what hit-testing
        // now finds there — `I004/ALL/att/…/rooms-27-dropin-live-tree…` has
        // `announce to kitchen` at y=399.7 and the switch at y=507.7
        // underneath a sheet that starts at y=398.7, and the page's own
        // words above that line (`version`, `mpd ports`, `stream`,
        // `volume`/`unknown`, and the 33.7-point `master output …` caption
        // at y=350) are on screen through the backdrop. That is the same
        // class `testAudiobooks_ABookPagePassesApplesAudit` measured behind
        // its question (`StubHouseUITests+Audiobooks.swift:1209-1233`), and
        // it is classified here the same way: by wording **and**
        // `element: nil`, so a finding Apple can attribute to a view of the
        // sheet's still lands in `rest` and fails this case from the first
        // run.
        //
        // **The banner is not it**, which the picture in `att/` invites a
        // reader to think: `rooms-27-dropin-live_…png` was taken at t=10.98s
        // with a `Signs of tampering` banner over the app, and a banner is
        // the known producer of exactly this headline
        // (``waitForNoNotificationBanner(_:appearIn:clearIn:)``). The audit
        // was not taken through it —
        // `testRooms_DropInFromThisPhoneIsHeardAndHungUp.log:339`,
        // "clean on attempt 1 after waiting out a banner at
        // (8.0, 56.0, 377.0, 78.67) — 5 finding(s)" — and
        // ``auditIssues(_:_:)`` only prints that line when the banner is gone
        // at both ends of the audit.
        //
        // **`Contrast nearly passed` is excused with the other four, and
        // that needs its own elimination**, because the discriminator below
        // runs with the sheet DOWN and so can never see a finding against
        // text only the sheet draws. The named suspect is this unit's own:
        // `DropInCallSheet.swift:59-60` draws the two-way-audio line
        // `.font(.caption)` + `Color.fgMuted`, which is small grey text and
        // exactly what "contrast is not high enough … unless font size is
        // larger" names. Four measurements, three of them from this same
        // train, say it is not:
        //
        //  * **Apple names what it can resolve.** The sheet's caption is an
        //    element in the tree with its own label and frame
        //    (`{{28.5, 567.0}, {336.0, 14.3}}`), and it is asserted as one
        //    below. The other `Contrast nearly passed` in all 432 cases of
        //    I004 — `testShell_ADialogLeavesNothingBehindIt.log:123-133` —
        //    **names** `cancel`, and `testCalendar_AuditHasNothingNew`'s
        //    `Contrast failed` names `swim lesson`. An anonymous one is not
        //    an element the audit had.
        //  * **The pairing is ambient and never flagged.**
        //    `.font(.caption)` + `Color.fgMuted` is drawn on dozens of this
        //    app's screens (Files, Chat, Calendar, Audiobooks, Assistant …)
        //    and `Contrast nearly passed` occurs **once** in 432 cases.
        //  * **Apple measured that exact pairing in this train and said
        //    nothing about its contrast.** `4.0 MB · 4h ago`
        //    (`FileRowView.swift:122-123`, `.caption` + `fgMuted`) is named
        //    by a *Dynamic Type* finding in `testTheFilesListAudits.log`, so
        //    the audit had the element and measured it.
        //  * **The canvas is the difference.** The sheet is the white
        //    `systemBackground` the ink tokens were measured against; the
        //    covered page under a presentation is that canvas dimmed, which
        //    is the condition NIGHT-RULES §3 records `fgMuted` failing on.
        //
        // **All four eliminate the caption, and train I005 says they left a
        // suspect out.** The caption measures 7.43:1 in that run's own
        // screenshot of this sheet, so the eliminations hold — but `mute`,
        // two rows above it, measured **4.38:1** there, which is what
        // "Contrast nearly passed" names and the only pairing on the sheet
        // under 4.5:1. It is fixed in `DropInCallSheet.swift` and the count
        // below is re-pinned on that; the alternative outcome and what to do
        // with it are written at the assertion.
        //
        // The discriminator is below, after `hang up`: the same audit with
        // nothing over the room must report none of them. One that survives
        // the sheet coming down is the room screen drawing words it never
        // exposes, which is a real defect and what that half is for.
        var underTheSheet: [String] = []
        if #available(iOS 17.0, *) {
            let sheet = try auditIssues(app, "rooms-dropin-sheet")
            let split = Self.floorAndRest(sheet)
            underTheSheet = split.rest.filter(Self.roomsAnonymousUnderAPresentation)
            let ours = split.rest.filter { !Self.roomsAnonymousUnderAPresentation($0) }
            let unexposed = underTheSheet.filter { $0.hasPrefix(Self.roomsUnexposedText) }
            let nearlyPassed = underTheSheet.filter { $0.hasPrefix(Self.roomsNearlyPassed) }
            print(
                "ROOMS AUDIT dropin-sheet floor=\(split.floor) rest=\(split.rest.count) "
                    + "covered=\(underTheSheet.count) (unexposed=\(unexposed.count) "
                    + "nearly-passed=\(nearlyPassed.count)) ours=\(ours.count)"
            )
            for line in underTheSheet { print("ROOMS AUDIT dropin-sheet covered :: \(line)") }
            XCTAssertTrue(
                ours.isEmpty,
                "Apple's audit has findings against the drop-in call sheet "
                    + "beyond the measured floor and the covered page: "
                    + ours.joined(separator: " / ")
            )
            // **The excused set is an equality, not an open filter.** Review
            // round 1 of this fix caught the filter as written: unbounded,
            // where I004 measured exactly four and one — so a sixth and a
            // seventh would have been waved through unseen, and a real
            // low-contrast line added to this sheet tomorrow would be
            // excused for good. Pinned the way ``testRooms_Audit`` pins its
            // two floors, for the same reason: a number that moves is the
            // thing worth seeing. If it moves, re-measure from
            // `att/…/rooms-27-dropin-live-tree…` and the log's `covered ::`
            // lines and re-pin here — never widen it.
            //
            // **Re-pinned 1 -> 0 on a pixel measurement, and what to do if it
            // comes back 1.** The one `Contrast nearly passed` was read as
            // the covered page's, because the sheet's own `.caption` had been
            // eliminated. Train I005's screenshot of this very sheet settles
            // both halves with numbers: the caption measures **7.43:1**, and
            // `mute` — `.buttonStyle(.bordered)` with `.tint(Color.brandInk)`,
            // which draws the label in the tint over the tint at low opacity
            // — was `#975800` on `#ECE1D1`, **4.38:1**. That is the one
            // pairing on this sheet under 4.5:1, it is exactly what "contrast
            // is not high enough ... unless font size is larger" names, and
            // the elimination never considered it because the suspect it was
            // written against was the caption. `DropInCallSheet.swift`'s
            // `mute` draws no fill any more, so the finding it produced
            // should be gone and this is pinned at none.
            //
            // If it comes back **1**, that is the older reading vindicated —
            // the covered page's `fgMuted` sampled through the backdrop
            // (7.43:1 on white, dimmed under a scrim) — and `[4, 1]` with
            // that sentence is the re-pin, which is a measurement and not a
            // widening.
            XCTAssertEqual(
                [unexposed.count, nearlyPassed.count], [4, 0],
                "the backdrop's anonymous findings are no longer the four "
                    + "`\(Self.roomsUnexposedText)` train I004 measured and "
                    + "the none `\(Self.roomsNearlyPassed)` that `mute` losing "
                    + "its 4.38:1 fill should leave. The page behind this "
                    + "sheet is the opened kitchen room; re-measure before "
                    + "re-pinning:\n"
                    + underTheSheet.joined(separator: "\n")
            )
        }
        // **The sheet's own words resolve to elements Apple could have
        // named** — which is what makes an *anonymous* finding above not the
        // sheet's. Worth being exact about what each line is worth: `mute`
        // is the one word of the sheet this case did not already assert, and
        // the caption is the suspect the elimination above turns on, so it is
        // re-read here *after* the audit and its frame is asserted too — an
        // element Apple can both name and place. `live · …` was waited for at
        // the top of this case and `hang up` is asserted below, so repeating
        // them here would have been three restatements dressed as four
        // measurements, which is how review round 1 read them.
        //
        // (The sheet's title is not asserted — `kitchen` is drawn by the
        // navigation bar and by the room's header as well, so an element with
        // that label says nothing about which view produced it.)
        XCTAssertTrue(
            app.buttons[RoomsStringsUnderTest.mute].firstMatch.exists,
            "`mute` is drawn without an accessibility element"
        )
        let sheetCaption = containing(app, RoomsStringsUnderTest.twoWayAudioKitchen)
        XCTAssertTrue(
            sheetCaption.exists,
            "the call sheet's explanation is drawn without an accessibility element"
        )
        XCTAssertFalse(
            sheetCaption.frame.isEmpty,
            "the call sheet's explanation has an element with no frame, so an "
                + "audit finding against it could not have named it — which is "
                + "the step the `Contrast nearly passed` elimination above rests "
                + "on: \(sheetCaption.frame)"
        )

        // The house's own record: the room it was asked for, this phone's id
        // on the query, the household token on the upgrade, and frames.
        let seen = roomsAsk("rooms-dropin")
        XCTAssertTrue(
            seen.contains("\"room\": \"kitchen\""),
            "the socket did not open on the room that was tapped: \(seen)"
        )
        XCTAssertTrue(
            seen.contains("\"token\": true"),
            "the upgrade carried no household token, so a real core would "
                + "answer `unauthorized`: \(seen)"
        )
        XCTAssertFalse(
            seen.contains("\"phone_id\": null"),
            "the socket carried no `phone_id`, which is what makes "
                + "`initiator_busy` work: \(seen)"
        )
        XCTAssertFalse(
            seen.contains("\"frames\": 0"),
            "the call is live and nothing has left this phone — the room "
                + "would hear silence: \(seen)"
        )

        // Mute is offered while the call is on, and only then.
        let mute = app.buttons[RoomsStringsUnderTest.mute].firstMatch
        XCTAssertTrue(mute.waitForExistence(timeout: 10), "a live call offers no mute")
        mute.tap()
        XCTAssertTrue(
            app.buttons[RoomsStringsUnderTest.unmute].firstMatch.waitForExistence(timeout: 10),
            "muting did not offer the way back"
        )

        let hangUp = app.buttons[RoomsStringsUnderTest.hangUp].firstMatch
        XCTAssertTrue(hangUp.exists, "a live call cannot be ended in one tap")
        hangUp.tap()
        evidence(app, "rooms-27-dropin-ended")
        // **The sheet went away**, which is a different statement from "the
        // row behind it is there": review round 2 found this case closing on
        // the row, which exists throughout. The sheet's own line is the only
        // thing that says the presentation is down.
        XCTAssertTrue(
            roomsWaitGone(app, RoomsStringsUnderTest.twoWayAudioKitchen),
            "`hang up` left the call sheet on screen"
        )
        // **The discriminator for the five findings above, and what it does
        // NOT cover.** The room screen is uncovered now and in the tree
        // whole, so Apple's element detection has an element for every word
        // it can read. An anonymous finding of either headline that survives
        // the sheet coming down was never the presentation's: it is this
        // screen drawing something it does not expose, and it fails here.
        //
        // Its reach is the **covered page**, which is where the backdrop
        // theory puts all five. It says nothing about text only the sheet
        // draws, because the sheet is gone — so the sheet's own `.caption`
        // line is eliminated by the four measurements written at the audit
        // above and by the equality pinned there, not by this. (And if
        // `underTheSheet` is empty the claim is vacuous and this asserts
        // nothing new — which is the honest reading of a train that saw
        // none.)
        if #available(iOS 17.0, *) {
            // **Where this page is, and who scrolled it.** XCUITest scrolls
            // to an element before it taps it, so reaching
            // `drop in from this phone` moved this page 380.6 points
            // (train I006's `rooms-27-dropin-ended-tree…`: the content sits
            // at y=-243.3 in a scroll view at 93.3; it was 394.6 and -257.3
            // in I005, before the control lost 14 points of height). Nothing
            // puts it back, so the audit below is taken on a page whose head
            // is under the navigation bar and whose foot is past the clip —
            // which is a fact about the position, not about the colours, and
            // ``roomsContrastWithNoPixels(_:on:after:)`` is where the
            // measurement that separates the two is written.
            //
            // **And no row may straddle the bar's lower edge** (train I006).
            // That classifier excuses an element wholly under the bar and
            // keeps one with any pixels showing, so a row *across* the edge
            // is kept — and the 14 points the drop-in control lost moved the
            // automatic scroll by 14, which put `registry default` at
            // 123.0–141.0 against a bar ending at 137.3: 3.7 points of
            // descenders under the hairline, the label's own ink 21:1, and a
            // `Contrast failed` the classifier was right to keep. So the page
            // is moved up by the largest overlap across that edge plus a
            // point, measured, and then **asserted** to have nothing across
            // it, so the next row-height change fails here loudly instead of
            // flipping the excuse in silence. The drop-in control (726–770)
            // moves by the same few points and must still be wholly on the
            // window, because it is what this audit sits at the foot to read.
            if let page = roomsScrollView(app, strip: false) {
                let bar = app.navigationBars.firstMatch
                let edge = bar.exists ? bar.frame.maxY : 0
                /// The page's labelled elements an edge at `edge` runs
                /// through, with half a point of slack, as `(label, frame)`.
                /// UIKit's scroll indicators are not the page's content.
                func acrossTheBar() -> [(label: String, frame: CGRect)] {
                    Self.roomsLabelled(page.descendants(matching: .any)).filter { one in
                        !one.label.contains("scroll bar")
                            && Self.crosses(edge, one.frame)
                    }
                }
                XCTAssertGreaterThan(edge, 0, "no navigation bar over the room's page, so no edge to clear")
                for _ in 0..<3 {
                    let across = acrossTheBar()
                    guard let overlap = across.map({ $0.frame.maxY - edge }).max() else { break }
                    print(
                        "ROOMS dropin-after: \(across.count) element(s) across the bar's "
                            + "edge at \(edge), largest overlap \(overlap) — moving the page up "
                            + "\(overlap + 1): \(across.map { "'\($0.label)' \($0.frame)" })"
                    )
                    guard roomsDragVertically(page, contentUpBy: overlap + 1) else { break }
                }
                let still = acrossTheBar()
                if !still.isEmpty { evidence(app, "rooms-27-dropin-across-the-bar") }
                XCTAssertTrue(
                    still.isEmpty,
                    "the page still has element(s) across the navigation bar's lower edge at "
                        + "\(edge) after measured drags, so the audit would sample a row "
                        + "that is part bar and part page (I006's `registry default`): "
                        + "\(still.map { "'\($0.label)' \($0.frame)" }) — tree printed above"
                )
                let control = app.buttons[RoomsStringsUnderTest.dropInFromThisPhone].firstMatch
                let dropFrame = control.exists ? control.frame : .zero
                if !(roomsInside(dropFrame, page.frame, vertical: true)
                    && app.frame.contains(dropFrame) && dropFrame.height > 0) {
                    evidence(app, "rooms-27-dropin-control-off-the-window")
                }
                XCTAssertTrue(
                    dropFrame.height > 0
                        && roomsInside(dropFrame, page.frame, vertical: true)
                        && app.frame.contains(dropFrame),
                    "`drop in from this phone` is at \(dropFrame), not wholly inside the "
                        + "page's viewport \(page.frame) on the window, after moving the page "
                        + "off the bar's edge — the audit below would not read the control it "
                        + "sits at the foot of the page to read (tree printed above)"
                )
            } else {
                print(app.debugDescription)
                XCTFail("no page scroll view on the room's screen to clear the bar's edge on")
            }
            let shot = roomsWindowShot(app)
            let bare = try auditIssues(app, "rooms-dropin-after")
            let bareSplit = Self.floorAndRest(bare)
            let stillThere = bareSplit.rest.filter(Self.roomsAnonymousUnderAPresentation)
            let named = bareSplit.rest.filter { !stillThere.contains($0) }
            let seen = roomsContrastWithNoPixels(named, on: shot, after: app)
            print(
                "ROOMS AUDIT dropin-after floor=\(bareSplit.floor) "
                    + "rest=\(bareSplit.rest.count) anonymous=\(stillThere.count) "
                    + "no-pixels=\(seen.noPixels.count) ours=\(seen.measured.count) "
                    + "(under the sheet it was \(underTheSheet.count))"
            )
            XCTAssertEqual(
                [], stillThere,
                "the room screen draws text it does not expose with no sheet "
                    + "over it, so the \(underTheSheet.count) anonymous "
                    + "finding(s) taken through the call sheet were not the "
                    + "presentation's after all:\n"
                    + stillThere.joined(separator: "\n")
            )
            // **And the attributed half, measured where the window shows it.**
            // Train I005 returned four here and three of them were against
            // elements with no pixels on the window — `voice` and
            // `registry default` wholly inside the 44-point navigation bar,
            // the health sentence 70 points past the scroll view's foot. The
            // fourth, `drop in from this phone` at `(16.0, 712.0, 212.0,
            // 58.0)`, was drawn whole: `#975800` on `#ECE1D1` is **4.38:1**
            // off that run's own screenshot, under the 4.5:1 its subheadline
            // needs, and `RoomsDropInRow.swift` no longer draws a fill behind
            // it. That one had to fail, and it is why the classifier excuses
            // nothing an element shows.
            XCTAssertEqual(
                [], seen.measured,
                "an opened room with nothing over it has an audit finding of "
                    + "its own, against an element the window was showing:\n"
                    + seen.measured.joined(separator: "\n")
            )
        }
        // The house was **told**, rather than the socket being dropped: the
        // room's end of a call that is merely abandoned stays open until the
        // core's silence timeout.
        let after = roomsAsk("rooms-dropin")
        XCTAssertTrue(
            after.contains("\"ended\": true"),
            "hanging up did not send `dropin_end` to the house: \(after)"
        )
        // And the screen is the room again, with the control offered afresh.
        XCTAssertTrue(
            drop.waitForExistence(timeout: 20),
            "after a call there is no way to start another"
        )
    }

    /// A refused microphone is a **sentence that says where to turn it on**,
    /// and nothing is opened.
    ///
    /// The other half of "a refusal is a sentence, never a silent failure".
    /// The arbiter's half — the player already holding the microphone — is
    /// pinned in `DropInCallModelTests`
    /// (`testTheMicBeingTakenIsASentenceAndNothingIsOpened`): nothing a UI
    /// case can do from this screen takes the microphone first.
    @MainActor
    func testRooms_DropInSaysWhereToTurnTheMicrophoneOn() throws {
        let app = roomsOpen(roster: "full-duplex", micSeam: "deny")
        XCTAssertTrue(roomsOpenRoom(app, "kitchen"), "the kitchen card did not open")
        let drop = app.buttons[RoomsStringsUnderTest.dropInFromThisPhone].firstMatch
        XCTAssertTrue(drop.waitForExistence(timeout: 20), "no drop-in control to press")
        drop.tap()
        XCTAssertTrue(
            roomsWaitFor(app, RoomsStringsUnderTest.micRefused, timeout: 30),
            "a refused microphone said nothing about where to turn it on"
        )
        evidence(app, "rooms-28-dropin-refused")
        // **Nothing was opened.** The house never saw a socket, so a port
        // that asked for the microphone and connected anyway would fail here
        // rather than in a log nobody reads.
        let seen = roomsAsk("rooms-dropin")
        XCTAssertTrue(
            seen.contains("\"sockets\": 0"),
            "a socket was opened although the microphone was refused: \(seen)"
        )
        // The way out of a call that never started is the same one button.
        let close = app.buttons[RoomsStringsUnderTest.close].firstMatch
        XCTAssertTrue(close.waitForExistence(timeout: 10), "the failed call cannot be dismissed")
        close.tap()
        // Gone, not merely "the row is there" — see ``roomsWaitGone``.
        XCTAssertTrue(
            roomsWaitGone(app, RoomsStringsUnderTest.micRefused),
            "`close` left the refusal on screen"
        )
        XCTAssertTrue(
            drop.waitForExistence(timeout: 20),
            "closing the failed call did not come back to the room"
        )
    }

    /// **A room already in a drop-in with somebody else offers no way in.**
    ///
    /// The must finding of review round 1. The core answers `target_busy` for
    /// a room whose `in_call_with` is set (`domovoi/phone_dropin.py:78-79`),
    /// and Android's own term is `s.online && s.full_duplex &&
    /// s.in_call_with == null` (`PhoneDropinCall.kt:81`) — so the button has
    /// to be unavailable there, and it was drawn live, because the only gate
    /// it had read this phone's own call state.
    ///
    /// ### Unavailable and not absent, which is the whole of the assertion
    ///
    /// The row has to **exist** in the `.inCall` branch: gating its existence
    /// on `in_call_with` would unmount the call sheet the instant the roster
    /// reported *this* phone's own call, which is the hazard Android's
    /// "always composed" comment is about (``RoomsOverviewView``'s drop-in
    /// note). So the two halves are asserted together: the control is there,
    /// and it cannot be pressed. A case that only looked for absence would
    /// pass against the port that reintroduces that hazard.
    ///
    /// The `in-call` roster is the fixture arm that was shipped for this and
    /// never used by a case (`fixtures/rooms.py`, `_roster`): the kitchen
    /// carries `full_duplex` **and** `in_call_with: study`.
    @MainActor
    func testRooms_DropInIsNotOfferedWhileTheRoomIsInACall() throws {
        let app = roomsOpen(roster: "in-call")
        XCTAssertTrue(roomsOpenRoom(app, "kitchen"), "the kitchen card did not open")
        // The branch we are in, by the line only it draws.
        XCTAssertTrue(
            roomsWaitFor(app, RoomsStringsUnderTest.inCallWithStudy),
            "the roster says the kitchen is in a call with the study and the "
                + "overview does not"
        )
        let drop = app.buttons[RoomsStringsUnderTest.dropInFromThisPhone].firstMatch
        XCTAssertTrue(
            drop.waitForExistence(timeout: 20),
            "the drop-in row is not drawn for a room that is in a call — "
                + "gating its existence on `in_call_with` is what unmounts "
                + "the sheet on this phone's own call"
        )
        evidence(app, "rooms-29-dropin-busy")
        XCTAssertFalse(
            drop.isEnabled,
            "`drop in from this phone` is pressable on a room already in a "
                + "drop-in with another peer, where the core can only answer "
                + "`target_busy`"
        )
        // The room's own control is the one that is live here, and it is a
        // different thing: it ends the call the two rooms are in.
        XCTAssertTrue(
            app.buttons[RoomsStringsUnderTest.hangUp].firstMatch.exists,
            "a room in a call offers no `hang up`"
        )
    }

    /// **The call survives the room reporting it** — review round 2's must
    /// finding, which no case could see.
    ///
    /// A phone's drop-in shows up in the roster about a second after it
    /// starts: `domovoi/phone_dropin.py:144-147` puts the call into
    /// `active_dropins` and `web/backend/api/satellites.py:2011-2018` derives
    /// `in_call_with` from exactly that list. So the drop-in section crosses
    /// from ``RoomsDropInState/idle`` to ``RoomsDropInState/inCall`` **while
    /// this phone's own sheet is up**, and that is the one state change the
    /// call has to live through. Android says so in the comment this port is
    /// built on ("gating on in_call_with would unmount the active call dialog
    /// the instant the room reports it's in a call — which is this very
    /// call", `SatOverviewTab.kt:559-563`); round 2 found this app drawing
    /// the row inside two branches of a `switch`, which is two structural
    /// identities and so the same unmount by another name.
    ///
    /// ### How the roster is made to move, since the fixture's is static
    ///
    /// `POST /api/_test/rooms-roster {"how": "in-call"}` is read on **every**
    /// roster request (`fixtures/rooms.py`, `_roster`), so arming it mid-call
    /// changes what the next read answers; the house pushes
    /// `satellites.wifi.changed` every three seconds
    /// (`stubhouse.py:2010-2017`) and that is one of
    /// ``RoomsModel/rosterEvents``, so the next read is a few seconds away
    /// with nothing to tap. The flip is then undone, so the branch is crossed
    /// **both ways** under one sheet — and so that the `hang up` at the end is
    /// unambiguous: while the roster says `in call with study` there are two
    /// buttons with that label on screen, the room's and the sheet's.
    ///
    /// What discriminates: the sheet's own line, which is drawn nowhere else,
    /// and the house's frame count, which only grows while the socket is
    /// open. A port that unmounts the sheet fails on the line; one that keeps
    /// a sheet whose call was hung up on connect fails on the count.
    @MainActor
    func testRooms_TheCallSurvivesTheRoomReportingIt() throws {
        let app = roomsOpen(roster: "full-duplex", micSeam: "pump")
        XCTAssertTrue(roomsOpenRoom(app, "kitchen"), "the kitchen card did not open")
        let drop = app.buttons[RoomsStringsUnderTest.dropInFromThisPhone].firstMatch
        XCTAssertTrue(drop.waitForExistence(timeout: 20), "no drop-in control to press")
        drop.tap()
        XCTAssertTrue(
            roomsWaitFor(app, RoomsStringsUnderTest.twoWayAudioKitchen, timeout: 30),
            "tapping `drop in from this phone` opened no call"
        )
        XCTAssertTrue(
            roomsWaitFor(app, RoomsStringsUnderTest.livePrefix, timeout: 30),
            "the call never went live"
        )
        let before = roomsFramesPast(0)
        XCTAssertGreaterThan(before, 0, "the call is live and nothing has left this phone")

        // The room now reports the call — this very call.
        control(
            "rooms-roster",
            "{\"how\": \"in-call\"}",
            port: 6601,
            what: "a roster that reports this phone's own call"
        )
        XCTAssertTrue(
            roomsWaitFor(app, RoomsStringsUnderTest.inCallWithStudy, timeout: 40),
            "the roster never reported the call, so this case proved nothing "
                + "— the state socket's push is what re-reads it"
        )
        // `29b`, inside the drop-in family and after `29-dropin-busy`:
        // `rooms-30-` is the Dynamic Type pair's (`rooms-30-type-…`), and two
        // cases sharing a sequence number stop the evidence being a reading
        // order even though the filenames differ.
        evidence(app, "rooms-29b-dropin-roster-flipped")
        // The sheet is still the thing in front, and the call is still on.
        XCTAssertTrue(
            containing(app, RoomsStringsUnderTest.twoWayAudioKitchen).exists,
            "the roster reporting this phone's own call took the call sheet "
                + "down — the row is drawn in more than one branch again"
        )
        XCTAssertTrue(
            containing(app, RoomsStringsUnderTest.livePrefix).exists,
            "the call stopped being live when the room reported it"
        )
        let seen = roomsAsk("rooms-dropin")
        XCTAssertFalse(
            seen.contains("\"ended\": true"),
            "`dropin_end` went to the house because the roster reported the "
                + "call — Android's \"killing the call on connect\": \(seen)"
        )

        // And back: the room stops reporting it, which crosses the branch the
        // other way under the same sheet.
        control(
            "rooms-roster",
            "{\"how\": \"full-duplex\"}",
            port: 6601,
            what: "a roster that no longer reports the call"
        )
        XCTAssertTrue(
            roomsWaitGone(app, RoomsStringsUnderTest.inCallWithStudy, timeout: 40),
            "the roster never stopped reporting the call"
        )
        XCTAssertTrue(
            containing(app, RoomsStringsUnderTest.twoWayAudioKitchen).exists,
            "the room stopping reporting the call took the sheet down"
        )
        // The frames kept travelling across both crossings, which is the
        // measurement that separates "the sheet is still drawn" from "the
        // call is still up".
        XCTAssertGreaterThan(
            roomsFramesPast(before),
            before,
            "no microphone frame reached the house after the roster moved, so "
                + "the sheet is drawn over a call that is no longer running"
        )

        // One `hang up` on screen now, and it is the sheet's.
        let hangUp = app.buttons[RoomsStringsUnderTest.hangUp].firstMatch
        XCTAssertTrue(hangUp.waitForExistence(timeout: 20), "the live call cannot be ended")
        hangUp.tap()
        XCTAssertTrue(
            roomsWaitGone(app, RoomsStringsUnderTest.twoWayAudioKitchen),
            "`hang up` left the call sheet on screen"
        )
        let after = roomsAsk("rooms-dropin")
        XCTAssertTrue(
            after.contains("\"ended\": true"),
            "hanging up did not send `dropin_end` to the house: \(after)"
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
        XCTAssertTrue(
            roomsLeaveRoom(app, "kitchen", swipe: false),
            "the kitchen would not pop, so the study was never reached"
        )
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

        XCTAssertTrue(
            roomsLeaveRoom(app, "study", swipe: false),
            "the study would not pop, so the kitchen was never reached"
        )
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
        // **D57.** `back to Rooms & devices` is the foot of every Settings
        // section (`RouteScreen.swift:296`), and the audit used to fail its
        // contrast — a finding this case excused as the shell's.
        //
        // It was not the shell's and it was not a colour. The button sits at
        // the bottom of a page that is taller than the window, so it is drawn
        // **straddling the viewport's edge**: Apple's contrast check samples
        // what it can see of an element, and half a button against whatever
        // is past the clip is not a measurement of the button. Brought wholly
        // onto the window it passes, at the same tint, on the same screen.
        // (The same shape as the finding in `audit-reports-one-issue`: when a
        // finding will not attribute, look at the picture.)
        //
        // So the fix is here rather than in `DomovoiApp.swift`'s ink, and
        // ``roomsOursOnly(_:)`` excuses nothing any more. Kamron's rule reads
        // the other way round too: the tint is honest, and a case that
        // quietly dropped a finding against a real control is the thing that
        // hides the next one.
        if let page = roomsScrollView(app, strip: false) {
            let foot = app.buttons[ShellStringsUnderTest.backToRooms]
            XCTAssertTrue(foot.waitForExistence(timeout: 20), "the Settings section has no foot")
            XCTAssertTrue(
                roomsBring(foot, into: page, vertical: true),
                "`back to Rooms & devices` could not be brought wholly onto "
                    + "the window, so the audit would measure a clipped "
                    + "button again and report a contrast failure that is the "
                    + "measurement's and not the button's"
            )
        } else {
            XCTFail("no settings page to scroll the section foot into")
        }
        evidence(app, "rooms-31-audit-roster")
        let rosterShot = roomsWindowShot(app)
        // **Contrast first, on its own, then everything else** (train I006).
        // This page sits at its maximum scroll offset (D57, above), and the
        // audit's own Dynamic Type pass shrinks the content, so the offset is
        // clamped; when the size comes back the page rests **33 points
        // lower**. I006 was the only train whose roster ran Apple's contrast
        // pass *after* that pass (its log prints the 12 Dynamic Type findings
        // first; I001-I005, S004-S010 and X005 all put contrast first), and
        // it returned eight `Contrast failed` against elements whose pixels
        // pass — `Harvest Moon` black on grey at 18.60:1, `Broadcast` the
        // same — every finding frame exactly +33.0 from this shot's tree, and
        // the boxes landing on the gaps between cards
        // (`i006-diag/rooms.md`, red 1). Frames from one layout, pixels from
        // another. With `.contrast` run alone nothing re-lays the page out,
        // so its frames and its pixels are the ones this shot holds; the
        // Dynamic Type floor of 12 is counted by the second call, unchanged.
        // ``roomsContrastWithNoPixels(_:on:after:)`` is **not** widened for
        // it: it rightly excused none of the eight, whose pixels were visible.
        let rosterContrast = try auditIssues(app, "rooms-roster", types: .contrast)
        let rosterOthers = try auditIssues(
            app,
            "rooms-roster",
            types: XCUIAccessibilityAuditType.all.subtracting(.contrast)
        )
        let roster = rosterContrast + rosterOthers
        let rosterSplit = Self.floorAndRest(roster)
        // **The other end of D57's scroll.** This page is 50.3 points taller
        // than its viewport (content 683 in a 632.7-point band, train I005's
        // roster tree), so bringing the foot wholly onto the window puts the
        // head under the navigation bar — and train I005 reported
        // `Contrast failed … label='Settings'`, the section's page title,
        // which `rooms-31-audit-roster….png` shows is not on the window at
        // all. ``roomsContrastWithNoPixels(_:on:after:)`` holds the whole
        // measurement and what it refuses to excuse; the lines it takes out
        // are printed with their frames, never dropped in silence.
        let rosterSeen = roomsContrastWithNoPixels(rosterSplit.rest, on: rosterShot, after: app)
        let rosterRest = roomsOursOnly(rosterSeen.measured)
        print(
            "ROOMS AUDIT roster floor=\(rosterSplit.floor) rest=\(rosterSplit.rest.count) "
                + "no-pixels=\(rosterSeen.noPixels.count) ours=\(rosterRest.count)"
        )

        XCTAssertTrue(roomsOpenRoom(app, "kitchen"), "the kitchen card did not open")
        XCTAssertTrue(roomsWaitFor(app, RoomsStringsUnderTest.roomIDLabel), "the overview did not draw")
        evidence(app, "rooms-31-audit-detail")
        let detailShot = roomsWindowShot(app)
        let detail = try auditIssues(app, "rooms-detail")
        let detailSplit = Self.floorAndRest(detail)
        // The same classification, for the same reason, though this screen
        // opens at the top of its page and train I005 had nothing for it to
        // take out (`rest=0`).
        let detailSeen = roomsContrastWithNoPixels(detailSplit.rest, on: detailShot, after: app)
        let detailRest = roomsOursOnly(detailSeen.measured)
        print(
            "ROOMS AUDIT detail floor=\(detailSplit.floor) rest=\(detailSplit.rest.count) "
                + "no-pixels=\(detailSeen.noPixels.count) ours=\(detailRest.count)"
        )

        // **The one finding I004 returned here, and whose it is.** The audit
        // came back with `Hit area is too small … | element: nil` — one
        // finding, no element. It is `back to Rooms & devices`, the foot of
        // every Settings section (`RouteScreen.swift:413`), and it is a
        // 192.3 x 20.3 `Button` with no `frame(minHeight: 44)` on it.
        //
        // Apple named nothing, so the attribution is a swap and two
        // eliminations, every number of them out of the two trains' own
        // roster trees (`att/testRooms_Audit/rooms-31-audit-roster-tree…`):
        //
        //  * **The swap.** `diff` of the I002 and I004 roster trees is the
        //    same element set at the same sizes, 26.6 points of scroll apart.
        //    The only element whose relation to the **window** changed is
        //    this button: I002 drew it at y=760.3 with the scroll view's foot
        //    at 770 and the phone bar at 774, so its last 10 points were
        //    occluded and the audit reported `Contrast failed` against it
        //    (D57's finding); I004's scroll puts it at y=733.7, wholly on the
        //    window, and the contrast finding is gone and this one is in its
        //    place. One finding, traded for one finding, on the one element
        //    that moved.
        //  * **The chrome is eliminated by the same swap.** `switch server`
        //    is 121 x 22.3 at `{{172.0, 65.0}}` in **both** trees — it did
        //    not move at all — and only I004 reports a hit area.
        //  * **The text fields are eliminated by the same swap.** The
        //    broadcast field is a 212 x 20.0 `TextField` wholly inside the
        //    scroll view in both trains — y=702.3 in I002, y=675.7 in I004,
        //    the foot at 770 in both — so it was fully on the window in the
        //    run that reported no hit area.
        //    (Review round 1 corrected this one: the elimination used to
        //    cite the **announce** field on the opened room, 279.3 x 20.0,
        //    "on the screen that reports none". That screen's `rest=0` says
        //    nothing about it — I004's detail tree has it at y=830.3, sixty
        //    points below that scroll view's foot at 770, so it was not on
        //    the audited area at all.)
        //
        // So it is real, it is a control, and it is not this item's file.
        // Kamron's answer 57 is "fix it once in the shell", and the shell is
        // where it was fixed: `Button(group.label)` (`RouteScreen.swift:404`),
        // `Button(section.label)` (`:414`),
        // `Button(ShellStrings.allSettings)` (`:419`) and
        // `Button(ShellStrings.backTo(…))` (`:436`) all carry
        // `frame(minHeight: 44)` and `contentShape(.rect)` since the I004
        // merge. Train I005's roster tree has the foot at 192.3 x **44.0**
        // and its audit returns no hit-area finding at all, which is the
        // measurement that closes this. The elimination stays written down:
        // the next unattributed `Hit area is too small` on this screen is
        // read the same way.
        XCTAssertTrue(
            rosterRest.isEmpty,
            "Apple's audit has findings against the Rooms roster beyond the "
                + "measured floor: " + rosterRest.joined(separator: " / ")
                + " — an unattributed `Hit area is too small` here is "
                + "`back to Rooms & devices` (RouteScreen.swift:413, "
                + "192.3 x 20.3), the shell's section foot, and its three "
                + "siblings at :387, :395 and :398 are the same shape; see "
                + "this case's comment for the elimination"
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
        //
        // **Re-pinned 0 → 12 on train I004's measurement.** The 0 was S004's,
        // taken before two things landed in this batch: D57's scroll, which
        // moves the page, and the shell's stale-title fix
        // (`ShellChromeViews.swift:546-558`), which applies
        // `navigationTitle`/`navigationBarTitleDisplayMode` unconditionally
        // and so changes what the navigation bars hand the audit on **every**
        // screen — the roster's `Back` button went 68 points wide to 44
        // between the two trees. I004 returned exactly twelve
        // `Dynamic Type font sizes are partially unsupported`, each
        // `element: nil` (`I004/ALL/testRooms_Audit.log:167-202`), and
        // `testShell_TheFoldedTitleComesBackOffAfterAnAudit` is what holds
        // the claim that those are the platform's and not a real defect.
        // Still an equality, for the reason below: a floor that grows again
        // is the thing worth seeing.
        XCTAssertEqual(
            rosterSplit.floor,
            12,
            "the Rooms roster's anonymous floor is \(rosterSplit.floor), not the "
                + "12 measured on train I004: " + roster.joined(separator: " | ")
        )
        //
        // **This number was measured before D56**, when the detail was drawn
        // inside the pushed Settings screen; the room is now a second push,
        // so Settings and Home are both beneath it and the count of bars the
        // audit cannot attribute may be higher. Review round 1 flagged it and
        // it is right: the first train to run this is the measurement, and
        // the `print` above is where to read it. An equality rather than a
        // bound, for the reason the shared helper gives
        // (`StubHouseUITests.swift:957-962`): a floor that *grew* is the
        // thing worth seeing, and a `<=` would hide exactly the direction
        // this change can move it in.
        XCTAssertEqual(
            detailSplit.floor,
            Self.knownPushedBarFloor,
            "an opened room's anonymous floor is \(detailSplit.floor), not the "
                + "known \(Self.knownPushedBarFloor) — which was measured "
                + "before D56 made the room its own push, so if the \(detailSplit.floor) "
                + "is all `element: nil` bar findings this is the number to "
                + "re-pin: " + detail.joined(separator: " | ")
        )
    }

    /// **Nothing is dropped any more** (D57).
    ///
    /// This used to hide one finding: `Contrast failed` against
    /// `back to Rooms & devices`, excused as the shell's control in a file
    /// this item may not retint. It was neither the shell's fault nor a
    /// colour — the button was drawn half past the bottom of the window, and
    /// a contrast check samples what it can see. ``testRooms_Audit`` now
    /// brings it wholly onto the window before it asks Apple anything, and
    /// the finding is gone at the same tint.
    ///
    /// Kept as a function rather than deleted at its three call sites: the
    /// shape of this screen's audit is "the floor, and everything else is a
    /// defect", and a named step that answers *everything* says so better
    /// than a silent absence would. A later finding that really does belong
    /// to another item gets its filter here, with the measurement that says
    /// why.
    @MainActor
    func roomsOursOnly(_ found: [String]) -> [String] {
        found
    }

    /// A finding the audit could not attribute, of the two kinds a
    /// **presentation** produces — read off the screenshot with no element
    /// under the words, or sampled through the backdrop a sheet draws.
    ///
    /// `performAccessibilityAudit` walks views; the accessibility tree is
    /// what is on the screen. Under a sheet they come apart: the covered page
    /// keeps its frames, the sheet draws other words over them, and Apple
    /// reports pictures it cannot place. ``StubHouseUITests/floorAndRest(_:)``
    /// deliberately refuses to swallow an anonymous finding of an
    /// unrecognised kind, so the classification is a **caller's** job, made
    /// once and paid for with a measurement — here, the second audit
    /// `testRooms_DropInFromThisPhoneIsHeardAndHungUp` takes with the sheet
    /// down.
    ///
    /// Matched by wording **and** `element: nil`, the same pair
    /// ``audiobooksAnonymousInaccessibleText(_:)`` uses
    /// (`StubHouseUITests+Audiobooks.swift:1250`): a finding Apple can
    /// attribute to a view is a defect and stays in `rest`.
    ///
    /// Written out here rather than calling the Audiobooks predicate: that
    /// one is another item's, it covers one of the two headlines, and a
    /// cross-file borrow is a rename away from silently matching nothing.
    ///
    /// ### The second headline is excused on its own evidence
    ///
    /// ``audiobooksAnonymousInaccessibleText(_:)`` covers
    /// ``roomsUnexposedText`` only. ``roomsNearlyPassed`` is this file's
    /// addition, and it is not carried by that precedent: the sheet's own
    /// `.caption` + `Color.fgMuted` line (`DropInCallSheet.swift:59-60`) is
    /// a live suspect for it, and the discriminator in
    /// ``testRooms_DropInFromThisPhoneIsHeardAndHungUp()`` runs with the
    /// sheet down and so cannot see a finding against text only the sheet
    /// draws. The four measurements that eliminate the caption are written
    /// out at that audit, and the excused set is pinned there as an
    /// **equality** (4 + 1, train I004) rather than left as an open filter —
    /// so this predicate decides which findings are *candidates*, and the
    /// case decides how many there may be.
    static func roomsAnonymousUnderAPresentation(_ line: String) -> Bool {
        guard line.hasSuffix("| element: nil") else { return false }
        return line.hasPrefix(Self.roomsUnexposedText)
            || line.hasPrefix(Self.roomsNearlyPassed)
    }

    /// Split contrast findings into the ones whose element **has no pixels on
    /// the window**, and the ones the screen really did show Apple.
    ///
    /// ### The measurement this rests on, twice over
    ///
    /// A contrast finding is a statement about *pixels*. Train I005 produced
    /// three against elements the window was not showing at all:
    ///
    ///  * `testRooms_Audit`, `label='Settings'` — the Settings section's own
    ///    page title. D57's scroll brings `back to Rooms & devices` wholly
    ///    onto the window, and this page is 50.3 points taller than its
    ///    viewport, so the far end of the same scroll puts the title
    ///    **under the navigation bar**: `att/…/rooms-31-audit-roster-tree…`
    ///    has it at `{{16.0, 103.0}, {78.7, 26.3}}` inside a bar that spans
    ///    93.3–137.3, and `rooms-31-audit-roster….png` shows the band where
    ///    it should be with the subtitle at the top of the page instead.
    ///  * `testRooms_DropInFromThisPhoneIsHeardAndHungUp`, `label='voice'`
    ///    and `label='registry default'` — the same shape. XCUITest scrolls
    ///    to an element before it taps it, so reaching `drop in from this
    ///    phone` scrolled that page 394.6 points; the two config words end
    ///    up at y 92.7 and 109, wholly inside the same 44-point bar
    ///    (`att/…/rooms-27-dropin-ended-tree…:47-49`). (Train I006: the
    ///    control is 14 points shorter, the scroll 380.6, and
    ///    `registry default` came to rest *across* the bar's edge, which this
    ///    classifier rightly keeps — so the case now moves the page off that
    ///    edge and asserts nothing straddles it before its window shot.)
    ///  * The same case's health sentence at `y=840.3`, 70 points **past the
    ///    scroll view's foot at 770** and behind the phone bar.
    ///
    /// The same four elements on the same screens in the same train, at a
    /// scroll position that shows them, are clean: `testRooms_Audit`'s opened
    /// room has `voice` at y=487.3, `registry default` at 503.7, the health
    /// sentence at 1235.0 and `drop in from this phone` at 1106.7, and it
    /// reports `floor=8 rest=0` (`I005/ALL/testRooms_Audit.log:383`).
    ///
    /// All three excused elements are the page's own: `Settings` is a
    /// descendant of the roster's scroll view
    /// (`rooms-31-audit-roster-tree…:22, 24`) and `voice`, `registry default`
    /// and the health sentence of the room's
    /// (`rooms-27-dropin-ended-tree…:23, 47-49, 82`), which is why asking
    /// `inPage` on both arms takes none of them back.
    ///
    /// So this excuses a sample of the navigation bar's material, or of
    /// whatever the window draws where the page's own content is clipped
    /// away — never a sample of the element. Both arms therefore ask
    /// `inPage`: only the page's own content can be the thing one of those
    /// two rectangles took the pixels away from. Everything else the window
    /// draws in either rectangle — the bar's own `Back` and title, the phone
    /// bar's four words, a sheet over the top — is drawn *there*, and a
    /// finding against it is kept. It is the same class as D57's `back to Rooms &
    /// devices`, which Kamron answered with "the fix is the case scrolling it
    /// into view, not a colour" — except that a page taller than its viewport
    /// cannot have both ends on the window at once, which is why the finding
    /// moved from the foot to the head when the foot was brought on.
    ///
    /// ### What it deliberately does not excuse
    ///
    ///  * **Anything but contrast.** `Hit area is too small` and the Dynamic
    ///    Type headlines are facts about a view's geometry or its font, not
    ///    about pixels, and are as true off the window as on it. Only the two
    ///    contrast headlines are classified here.
    ///  * **An element with any pixels showing.** `drop in from this phone`
    ///    was reported at `(16.0, 712.0, 212.0, 58.0)` in a page clipped at
    ///    770 — its glyphs are drawn whole, the picture shows them crisp, and
    ///    it stays in `measured` and fails its case. That finding was a real
    ///    4.38:1 and is fixed in `RoomsDropInRow.swift`.
    ///  * **The chrome above the page.** `unverified`, `  live` and
    ///    `127.0.0.1:6601` sit at y 65–87, above the scroll view and above
    ///    the bar — outside the bar's rectangle, so a finding against them
    ///    is kept. They are a known producer of real findings on other
    ///    screens.
    ///  * **The navigation bar's own children**, which is the reason the
    ///    *first* arm asks `inPage` and `!inBar` and not only
    ///    `covered.contains`. The bar's rectangle is where it covers the
    ///    page; what it covers the page *with* is its own content, and that
    ///    is drawn. `Back` at `{{0, 93.3}, {44, 44}}` and a pushed room's bar
    ///    title `kitchen` at `{{167, 105}, {59, 20.3}}` are wholly inside
    ///    `covered` (`{{-1, 92.3}, {395, 46}}`) in all three of this unit's
    ///    classified audits, and they are not hidden at all. Measured off
    ///    this train's own screenshots (Pillow, the exact frames at 3x):
    ///    the `Back` chevron is 466 pixels of `#975800` in every one of the
    ///    three — the same glyph, fully drawn — against the bar material
    ///    behind it, **5.13:1** on the roster (`#F3F4F3`), **5.22:1** on the
    ///    ended drop-in (`#F5F6F5`) and **5.66:1** on the opened room
    ///    (`#FFFFFF`); the bar title `kitchen` is `#000000` on `#F7F8F7`,
    ///    **19.72:1**. All three clear 4.5:1, so nothing real was being
    ///    hidden today — but a contrast finding against either was excused
    ///    in silence, which is what this arm's `inPage` now ends. (A sheet's
    ///    content inside that rectangle is the same case: a sheet is drawn
    ///    *over* the bar, and this unit's drop-in case audits with one up.)
    ///  * **The phone bar below the page**, which is the reason the second
    ///    arm asks `inPage` and not only `minY >= page.maxY`. The page is
    ///    the tallest scroll view, `{{0, 93.3}, {393, 676.7}}` on all three
    ///    Rooms screens, so its foot is at 770 — and the window's own tab
    ///    bar is **below** that and fully drawn: four `Button`s at y=774 and
    ///    four `StaticText`s at y≈799–800, in every I005 Rooms tree
    ///    (`rooms-31-audit-roster-tree…:93-106`, the same in the detail and
    ///    `dropin-live` trees), and the roster screenshot shows the bar
    ///    whole. A bare `minY >= page.maxY` was true for all eight, so any
    ///    contrast finding against `home`, `music`, `videos` or `chat` was
    ///    excused in silence in all three audits — and that pairing is not
    ///    hypothetical: measured off `rooms-31-audit-roster….png`, the
    ///    selected word `home` is `#975800` on `#F2F2F7`, **5.07:1**, which
    ///    clears 4.5:1 by 0.57, and `music` is `#58554F` on the same,
    ///    6.66:1. (Brand ink on a tinted surface is what measured 4.38:1 on
    ///    `drop in from this phone` in the same train.) Those eight are not
    ///    the page's descendants in any of the trees, so `inPage` is false
    ///    for them and a finding against one now fails the case.
    ///  * **Anything at all, when the audit folded the bar.** The audit's own
    ///    re-layout leaves a 96-point navigation bar on some screens
    ///    (`StubHouseUITests.swift:295-302`, and `auditIssues` prints it at
    ///    `:708-717`), and then the frames read here are not the frames the
    ///    audit sampled. So the bar is read **twice** — in the shot before
    ///    the audit and again from `app` after it — and every finding is
    ///    kept when either read is 60 points or more, or when the second is
    ///    taller than the first. A guard that only read the shot could never
    ///    trip on the fold the audit itself causes.
    ///
    /// ### What that guard does *not* prove
    ///
    /// An unchanged bar is not a promise that nothing moved. Train I005's
    /// roster is the counterexample: the bar is 44.0 before the audit
    /// (`rooms-31-audit-roster-tree…:16`) and no fold is printed after it,
    /// yet Apple's finding carries `frame=(16.0, 136.0, 78.67, 26.33)`
    /// against the tree's `{{16.0, 103.0}, {78.7, 26.3}}` — a 33-point
    /// re-layout the guard cannot see. The guard catches the fold that is
    /// known to persist, and nothing more.
    ///
    /// That is why the `Settings` verdict does not rest on this classifier
    /// alone. The title is `Text(ShellStrings.settingsTitle).font(.title2)`
    /// with no `foregroundStyle` (`RouteScreen.swift:367`) — `Color.primary`
    /// on the white page, which is about 16:1 and cannot fail a contrast
    /// check at any size. Whatever Apple sampled there, it was not that
    /// text's own pixels.
    ///
    /// Frames come from ``roomsWindowShot(_:)``, taken **before** the audit,
    /// rather than from the finding's own `frame=`: that one is written
    /// inside the audit's re-layout, which reported the Settings title at
    /// `y=136.0` where the tree attached to the same run has it at 103.0.
    /// Twins are read by label and **all** of them must be off the window —
    /// the Button and the StaticText of one control share a label, which is
    /// how `drop in from this phone` stays in `measured`.
    ///
    /// - Parameter app: read **after** the audit, for the bar's second
    ///   measurement. This function is only ever called once `auditIssues`
    ///   has returned, so this read is the post-audit one.
    @MainActor
    func roomsContrastWithNoPixels(
        _ lines: [String],
        on shot: RoomsWindowShot,
        after app: XCUIApplication
    ) -> (noPixels: [String], measured: [String]) {
        let barFrame = shot.bar
        guard !barFrame.isEmpty, barFrame.height < 60 else {
            print(
                "ROOMS AUDIT no-pixels: the navigation bar is \(barFrame) and not the "
                    + "ordinary 44 points — nothing classified"
            )
            return ([], lines)
        }
        // **The bar again, now that the audit has run.** The fold the shot
        // is afraid of is one the audit *causes*, so a pre-audit read is
        // 44.0 by construction and could never see it. In train I003 the bar
        // stayed folded after the audit on about twenty screens, which is
        // what makes this read worth taking — and when it is folded, every
        // frame in the shot is ~52 points from where the audit sampled it.
        let barNow = app.navigationBars.firstMatch
        let barAfter = barNow.exists ? barNow.frame : .zero
        guard !barAfter.isEmpty, barAfter.height < 60,
              barAfter.height <= barFrame.height + 0.5 else {
            print(
                "ROOMS AUDIT no-pixels: the navigation bar is \(barAfter) after the audit "
                    + "against \(barFrame) before it — the audit re-laid this screen out "
                    + "and the shot's frames are not what Apple sampled; nothing classified"
            )
            return ([], lines)
        }
        // **An empty page excuses everything**, because `page.maxY` would be
        // 0 and every element on the screen would be "past the foot". A
        // classifier with no geometry classifies nothing.
        let page = shot.page
        guard !page.isEmpty else {
            print("ROOMS AUDIT no-pixels: no page frame was taken — nothing classified")
            return ([], lines)
        }
        let covered = barFrame.insetBy(dx: -1, dy: -1)
        let seen = shot.elements
        // **Is this element the page's own content?** Matched by label *and*
        // frame, because the two lists are read in the same pass of the same
        // tree, so the one element appears in both with the one frame — and
        // a label alone would let the tab bar's `home` borrow a row called
        // `home` inside the page.
        func inPage(_ twin: (label: String, frame: CGRect)) -> Bool {
            shot.pageElements.contains { $0.label == twin.label && $0.frame == twin.frame }
        }
        // **Is this element the navigation bar's own content?** Matched the
        // same way, and for the same reason one rectangle over: the bar's
        // children are inside `covered` because the bar draws them there,
        // not because it hides them. In every I005 Rooms tree the bar is a
        // sibling of the scroll view's ancestors, so `inPage` alone already
        // keeps them; this is asked as well because that is a fact about
        // today's view hierarchy and `!inBar` is a fact about the bar, and a
        // toolbar placed inside the page later would still be drawn.
        func inBar(_ twin: (label: String, frame: CGRect)) -> Bool {
            shot.barElements.contains { $0.label == twin.label && $0.frame == twin.frame }
        }
        func noPixels(_ line: String) -> Bool {
            guard line.hasPrefix(Self.roomsContrastFailed)
                || line.hasPrefix(Self.roomsNearlyPassed) else { return false }
            guard let label = Self.roomsLabel(in: line) else { return false }
            let twins = seen.filter { $0.label == label }
            guard !twins.isEmpty else { return false }
            let off = twins.allSatisfy { twin in
                // Wholly behind the navigation bar, or clipped away by the
                // page's own foot. **Both arms are the page's content
                // only**: the bar's children are drawn on the bar and the
                // window's phone bar is drawn below the page's foot, so an
                // element in neither list is inside one of those two
                // rectangles because something put it there to be seen.
                (inPage(twin) && !inBar(twin) && covered.contains(twin.frame))
                    || (inPage(twin) && twin.frame.minY >= page.maxY - 0.5)
            }
            if off {
                print(
                    "ROOMS AUDIT no-pixels :: \(line) — on screen at "
                        + twins.map {
                            let whose = inBar($0)
                                ? " on the navigation bar"
                                : (inPage($0) ? " in the page" : " outside the page")
                            return "\($0.frame)\(whose)"
                        }
                            .joined(separator: ", ")
                        + ", with the bar at \(barFrame) and the page's foot at \(page.maxY)"
                )
            }
            return off
        }
        // One pass, because the predicate prints: a `filter` for each half
        // would say every excused finding twice in the log.
        var off: [String] = []
        var measured: [String] = []
        for line in lines {
            if noPixels(line) { off.append(line) } else { measured.append(line) }
        }
        return (off, measured)
    }

    /// Where this screen's page, navigation bar and every labelled element
    /// are, read in one pass **before** an audit.
    ///
    /// ### Why before
    ///
    /// `performAccessibilityAudit` re-lays the screen out while it works —
    /// the shell folds its header into the navigation bar under the audit's
    /// type-size override and the page moves down by a large-title row
    /// (`StubHouseUITests.swift:295-302`) — so the `frame=` Apple writes into
    /// a finding is a frame from **inside** that re-layout. Train I005 is the
    /// example: the Settings title is at y=103.0 in the tree attached to that
    /// run and the finding against it says y=136.0.
    ///
    /// The position a reader can check is the one in `att/…-tree….txt` and
    /// in the screenshot beside it, which is this one. So the geometry is
    /// taken where the case put the screen, in the same breath as the
    /// evidence, and the classification is against that.
    ///
    /// The page's own subtree is read as well as the window's, because "past
    /// the page's foot" is only a statement about pixels for something the
    /// page draws. The phone bar's eight leaves sit below that foot and are
    /// drawn in full; in every I005 Rooms tree they are siblings of the
    /// scroll view's ancestors, not its descendants, which is the difference
    /// these two lists carry.
    ///
    /// The **navigation bar's own subtree** is read for the same reason, one
    /// rectangle over: the bar covers the top of the page, but what it covers
    /// the page with is the bar's own content, drawn in full. `Back` sits at
    /// `{{0, 93.3}, {44, 44}}` and a pushed room's bar title `kitchen` at
    /// `{{167, 105}, {59, 20.3}}` (`rooms-27-dropin-ended-tree…:16-18`),
    /// both wholly inside the bar's rectangle and both perfectly legible in
    /// the screenshot beside it.
    @MainActor
    func roomsWindowShot(_ app: XCUIApplication) -> RoomsWindowShot {
        let bar = app.navigationBars.firstMatch
        let page = roomsScrollView(app, strip: false)
        var inside: [(label: String, frame: CGRect)] = []
        if let page {
            inside = Self.roomsLabelled(page.descendants(matching: .any))
        }
        var chrome: [(label: String, frame: CGRect)] = []
        if bar.exists {
            chrome = Self.roomsLabelled(bar.descendants(matching: .any))
        }
        return RoomsWindowShot(
            page: page?.frame ?? .zero,
            bar: bar.exists ? bar.frame : .zero,
            elements: Self.roomsLabelled(app.descendants(matching: .any)),
            pageElements: inside,
            barElements: chrome
        )
    }

    /// Every element of `query` that exists and carries a label, as the pair
    /// ``RoomsWindowShot`` keeps. One reader, so the window's list and the
    /// page's cannot be built two different ways and then compared.
    @MainActor
    static func roomsLabelled(_ query: XCUIElementQuery) -> [(label: String, frame: CGRect)] {
        query.allElementsBoundByIndex
            .filter { $0.exists && !$0.label.isEmpty }
            .map { (label: $0.label, frame: $0.frame) }
    }

    /// The `label='…'` of a finding Apple attributed, or nil for one it did
    /// not. Written out rather than borrowed from
    /// ``StubHouseUITests/elementFrame(in:)``'s neighbourhood because the
    /// shared file has no label reader, and a `label` can contain a comma
    /// (`'kitchen, online, …'`) so the frame parser's split is no help.
    static func roomsLabel(in line: String) -> String? {
        guard let open = line.range(of: "label='"),
              let close = line.range(
                  of: "' frame=", options: .backwards, range: open.upperBound..<line.endIndex
              )
        else { return nil }
        return String(line[open.upperBound..<close.lowerBound])
    }

    /// Apple's wording for "this element's contrast is below the threshold
    /// for its font size".
    static let roomsContrastFailed = "Contrast failed"

    /// Apple's wording for "there is text here belonging to no element".
    /// Spelled once, so the predicate, the counts and the failure message
    /// cannot drift apart.
    static let roomsUnexposedText = "Potentially inaccessible text"

    /// Apple's wording for "contrast is not high enough … unless font size is
    /// larger" — small text, which is why a `.caption` needed eliminating.
    static let roomsNearlyPassed = "Contrast nearly passed"
}

/// Where a Rooms screen's chrome and content were at one moment.
///
/// Taken by ``StubHouseUITests/roomsWindowShot(_:)`` beside the evidence and
/// read by ``StubHouseUITests/roomsContrastWithNoPixels(_:on:after:)``; that pair's
/// doc comments carry the measurements. A struct rather than three arguments
/// so the three readings cannot be taken at three different moments, which is
/// the whole point of taking them before the audit.
struct RoomsWindowShot {

    /// The page's scroll view — the tallest one on screen, so its foot is
    /// where the page's content is clipped.
    let page: CGRect

    /// The navigation bar, which covers the top of that scroll view.
    let bar: CGRect

    /// Every element with a label, so a finding's `label='…'` can be placed —
    /// and so twins that share one can both be checked.
    let elements: [(label: String, frame: CGRect)]

    /// The same, for the **page's own subtree** only. `page.maxY` is where
    /// the page clips its content; it says nothing about the window's phone
    /// bar, which is drawn below it. An element is "clipped away" only if it
    /// is in here.
    let pageElements: [(label: String, frame: CGRect)]

    /// The same, for the **navigation bar's own subtree**. `bar` is where the
    /// bar covers the page; the bar's own children are what it covers it
    /// *with*, and they are drawn. An element is "behind the bar" only if it
    /// is not in here.
    let barElements: [(label: String, frame: CGRect)]
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
    /// ``RoomsStrings/queuedForDownload(_:)`` for the external row — the
    /// stub's `add-by-url` answer when nothing is armed.
    static let queuedForDownload = "queued for download: Lost Mariner"

    /// The stub's 403, surfaced **as the server wrote it**
    /// (`fixtures/rooms.py`, `rooms-add {"how":"refused"}`).
    ///
    /// Reachable because `can_add` on this branch's server does not consult
    /// the fulfiller allowlist, which is the server half of D55 that has not
    /// landed. This is the sentence the app shows in its place; review round
    /// 1 found it deleted ahead of that change.
    static let addRefusedDetail = "no fulfiller recognises that address"

    static let dropInLabel = "drop in"
    static let hangUp = "hang up"
    static let noAecMic = "this room's mic can't do drop-in"
    /// ``RoomsStrings/inCallWith(_:)`` for the `in-call` roster arm, whose
    /// kitchen is in a call with `study` (`fixtures/rooms.py`, `_roster`).
    static let inCallWithStudy = "in call with study"

    // MARK: - a call from this phone (``DropInStrings``)

    /// ``DropInStrings/dropInFromThisPhone`` — the control D54 added.
    static let dropInFromThisPhone = "drop in from this phone"
    /// ``DropInStrings/twoWayAudio(room:)`` for the kitchen. **The dash is
    /// U+2014** and the sentence is on the call sheet and nowhere else, which
    /// is what makes it the marker for "the call is up".
    static let twoWayAudioKitchen = "two-way audio with the kitchen room — speakerphone is on"
    /// ``DropInStrings/live(_:)``'s start, without the clock. Matched as a
    /// prefix because the seconds move while the case reads them.
    static let livePrefix = "live ·"
    static let mute = "mute"
    static let unmute = "unmute"
    /// ``DropInStrings/close`` — what the one leave button says once the call
    /// is over, or never started.
    static let close = "close"
    /// ``DropInStrings/micRefused``. **The chevron is U+203A**, and the
    /// sentence has to name where the switch is: a refusal that only says
    /// "no" is the silent failure Kamron's rule is about.
    static let micRefused =
        "Domovoi needs the microphone to drop in. Turn it on in Settings › Domovoi."

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
