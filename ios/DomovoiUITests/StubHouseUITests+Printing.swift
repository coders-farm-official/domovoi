import XCTest

/// Printers and scanners, driven against the stub house: the panel in
/// Settings → Printers & scanners, and the Print and Scan sheets Files opens.
///
/// The fixture is `design-notes/ios/stubhouse/fixtures/printing.py`, whose
/// seed is written so every assertion below has a word only one thing can
/// produce:
///
/// * **two printers, opposite in every way that gates a control.** `office
///   printer` is duplex, mono-only, reports ink and offers Identify; `the
///   colour one` is not duplex, offers colour, reports no ink and offers no
///   Identify. So "two-sided by default" belongs to exactly one card and
///   "colour by default" to the other, and a panel that drew every control on
///   every printer cannot pass.
/// * **one nonsense word per preview**, inside the server's own `summary`:
///   `studyside` can only have come from printer 1's preview and `colourway`
///   only from printer 2's.
/// * **`scan.pdf` and `logo.svg`** are both in the house's Documents root, and
///   `.pdf` is in the real `printable.exts` list while `.svg` is not — so
///   "Print is offered for the right kinds" discriminates by construction
///   rather than by a seeded flag.
/// * **the already-added found row is `Brother HL-L2375DW`**, the model card 1
///   shows in its subtitle. A bare `Brother HL-L2375DW` on screen is therefore
///   a found row that should have been filtered out.
///
/// Every case resets the fixtures first: the cases share one house for the
/// whole run, and five of them write to it.
extension StubHouseUITests {

    // MARK: - getting there

    /// Reset the house, launch into Settings and walk to Printers & scanners.
    ///
    /// The two labels are the ones the bisect case already uses
    /// (``ShellStringsUnderTest/householdGroup`` and `.printersSection`), so a
    /// rename cannot leave this file matching nothing while the bisect keeps
    /// passing.
    ///
    /// The reset is **before** the launch, not after: the panel reads the
    /// saved list when it appears, and a reset that landed afterwards would
    /// leave the app showing a list the fixture had already replaced.
    @MainActor
    func printingOpenPanel(_ mode: String = StubHouseUITests.ordinaryTypeSize) -> XCUIApplication? {
        control("reset-fixtures", "{}", port: 6601, what: "the printing fixture's seed")
        // **Two seconds, not ``pushDelay``'s twelve.** The twelve are for a
        // screen whose own reads have to land before it is photographed;
        // Settings is static chrome and reads nothing until the section is
        // opened, and a dozen seconds on each of this file's launches is
        // minutes of a train. Not nil either: pushing on the first frame is
        // what the audit runs found meant nothing, because the root had not
        // drawn under it.
        let app = launch(mode: mode, push: "Settings", pushDelay: 2)
        let group = app.buttons[ShellStringsUnderTest.householdGroup]
        guard group.waitForExistence(timeout: 30) else {
            print("=== SETTINGS NEVER DREW ITS GROUPS — tree follows ===")
            print(app.debugDescription)
            XCTFail("Settings never drew its group list")
            return nil
        }
        // Scrolled to before each tap: at an accessibility type size the five
        // group buttons are taller than the window, and a tap on an element
        // that exists and is not hittable lands on nothing and reads exactly
        // like a screen that never drew.
        guard printingScrollToHittable(app, group) else {
            XCTFail("the Household group is unreachable")
            return nil
        }
        group.tap()
        let section = app.buttons[ShellStringsUnderTest.printersSection]
        guard section.waitForExistence(timeout: 15) else {
            print("=== NO PRINTERS SECTION — tree follows ===")
            print(app.debugDescription)
            XCTFail("the Household group has no Printers & scanners")
            return nil
        }
        guard printingScrollToHittable(app, section) else {
            XCTFail("the Printers & scanners section is unreachable")
            return nil
        }
        section.tap()
        // The first printer's own name is what only a **completed read** can
        // produce: the slot's placeholder says `not built yet` and the network
        // line is drawn before any read lands, so neither would do.
        let marker = app.staticTexts["office printer"]
        guard marker.waitForExistence(timeout: 30) else {
            print("=== THE PANEL NEVER LISTED A PRINTER — tree follows ===")
            print(app.debugDescription)
            XCTFail("the printers panel never listed the saved printers")
            return nil
        }
        return app
    }

    /// Scroll the Settings screen until `element` can be tapped.
    ///
    /// Not ``scrollUntilBuilt(_:_:flicks:)``: that scrolls
    /// `app.collectionViews.firstMatch` and this screen is a `ScrollView` over
    /// a plain `VStack`, so it has no collection view at all. And not
    /// `exists`: the panel's cards are **not** lazy, so every card exists from
    /// the first frame and the thing a tap needs is hittability.
    ///
    /// ### It was fourteen `app.swipeUp()`s, and that is what train I004 lost
    ///
    /// `testPrinting_WhichOneIsItOnlyWhereTheServerSaysSo` failed on "the
    /// identify button is unreachable" with the button **in the tree the
    /// failure printed**, at `{{30.0, -561.3}, {136.0, 44.0}}` — 561 points
    /// *above* the top of a scroll view whose own indicator read `value: 100%`.
    /// The first tree in the same log has it at `{{30.0, 772.7}, {136.0, 44.0}}`
    /// with the indicator at `0%`: 44 points below the scroll view's bottom
    /// edge at y = 770, and behind the phone bar. It needed about 50 points of
    /// scroll and it got the whole travel, **1334** — the content is
    /// `{{0, 137.3}, {393.3, 1966.7}}` at rest and `{{0, -1196.7}, …}` in the
    /// failure tree, and 772.7 - 1334 is exactly the -561.3 the button ended
    /// at. (1966.7 - 676.7 = 1290 is the arithmetic that forgets the scroll
    /// view's 44-point top content inset; the travel is the inset plus that.)
    ///
    /// A bare `swipeUp()` is a **flick**: it carries momentum, and on this
    /// panel one of them runs the content to its end stop. The loop then spent
    /// its remaining thirteen iterations swiping a scroll view that was already
    /// at the bottom, and the `where` clause — which does stop the loop the
    /// moment the element is hittable — never got a frame in which it was.
    /// Nothing about the app changed; the panel grew by a card (the look's
    /// found rows arrive by themselves, `testPrinting_LookForPrintersAddsOnlyWhatIsNotAdded`
    /// says why) until one flick was more than the whole remaining scroll.
    ///
    /// So this no longer flicks. It **measures** and drags: each round reads
    /// where the element is and where the readable band is, and moves the
    /// content by that difference with ``printingDrag(_:by:)``, a held drag
    /// that ends stationary and so takes no velocity. Because the move is
    /// signed it works in both directions — an element above the fold comes
    /// back down, which fourteen `swipeUp`s could never do — and because it
    /// converges on the band's middle it stops rather than bouncing off an end
    /// stop. The per-round geometry is printed, so a failure next train says
    /// which way it was going and how far.
    ///
    /// `flicks` keeps its name and its meaning: the number of rounds.
    @MainActor
    func printingScrollToHittable(_ app: XCUIApplication, _ element: XCUIElement, flicks: Int = 14) -> Bool {
        guard element.exists else {
            print("=== NOT IN THE TREE AT ALL, SO NOT A SCROLL — tree follows ===")
            print(app.debugDescription)
            return false
        }
        var trace: [String] = []
        for round in 1...flicks {
            if element.isHittable { return true }
            let box = element.frame
            guard let page = printingPageHolding(app, box) else {
                trace.append("round \(round): no scroll view to drag")
                break
            }
            // The navigation bar is translucent and **hit-tests**: an element
            // under it exists, is in the picture as a blur, and is not
            // hittable. So the readable band starts at the bar's bottom edge,
            // not at the scroll view's top.
            let bar = app.navigationBars.firstMatch
            let top = max(page.frame.minY, bar.exists ? bar.frame.maxY : page.frame.minY)
            let bottom = page.frame.maxY
            guard bottom - top > box.height + 8 else {
                trace.append(
                    "round \(round): the band \(top)…\(bottom) cannot hold a "
                        + "\(box.height)-point element"
                )
                break
            }
            let move = box.midY - (top + bottom) / 2
            trace.append(
                "round \(round): element \(box.minY)…\(box.maxY), band \(top)…\(bottom), "
                    + "moving \(move)"
            )
            // Already where it should be and still not hittable: something is
            // over it, which is not a scroll's to fix and is a different
            // finding. Stop and print, rather than drag on the spot.
            //
            // **Twelve points, which is half of ``printingDrag(_:by:)``'s
            // 24-point floor** (below a scroll view's pan slop a drag is a
            // long press, not a pan). That floor is what sets this number: ask
            // for anything under 24 and the page moves 24, so a `move` of
            // magnitude m in (12, 24) comes back as 24 - m, which is again in
            // (0, 12) — under the guard, and the loop stops. A guard of 8
            // would not: m = 10 drags 24, leaves -14, drags 24 back, and
            // oscillates for all fourteen rounds. Above 24 the drag is what
            // was asked for, up to the 80%-of-height clamp, so it converges on
            // its own and several rounds are a longer distance, not a bounce.
            guard abs(move) > 12 else { break }
            guard printingDrag(page, by: move) else {
                // A drag that could not be made must not read as one. The
                // guards inside are a scroller too short to pan at all, which
                // is a finding of its own and never fourteen silent rounds.
                trace.append(
                    "round \(round): no drag — the scroller is \(page.frame.height) "
                        + "points tall and the move asked was \(move)"
                )
                break
            }
        }
        if element.isHittable { return true }
        print("=== \(flicks) MEASURED DRAGS AND STILL NOT HITTABLE ===")
        for line in trace { print("    \(line)") }
        print(app.debugDescription)
        return false
    }

    /// The scroll view the element's box falls inside, or — when it falls
    /// inside none — the frontmost full-width one.
    ///
    /// ### Containment cannot be the whole rule, and the fallback is the half
    /// that matters
    ///
    /// This helper is only ever called for an element that is **not** hittable,
    /// and the commonest reason for that on this panel is an element the
    /// scroller has clipped — whose frame is therefore *outside* its own
    /// scroller (`{{30, 772.7}, …}` against an edge at 770, and `{{30,
    /// -561.3}, …}` after the overshoot). So containment misses in exactly the
    /// case the helper exists for, and what the fallback picks is the answer.
    ///
    /// It is not "the tallest". That was written for the sheets this file
    /// drives, on the belief that the tallest scroller behind a sheet is the
    /// Files list; I004's own tree says otherwise, and the belief was
    /// backwards. With the Print sheet up (`printing-21-preview-switched-tree`):
    ///
    /// * the presenting screen is pushed back and scaled — `{{16, 59}, {361,
    ///   782.6}}` — and its Files scroller is `{{16, 144.7}, {361, 621.6}}`,
    ///   with a 331.6 x 40.4 chip row nested inside it;
    /// * the sheet is the **last** subtree of the window, `{{0, 69}, {393,
    ///   783}}`, and its own scroller is that same rectangle, with a 361 x
    ///   105.3 page strip nested inside it.
    ///
    /// So the tallest is the sheet's here (783 against 621.6) and the old rule
    /// was right by luck. The rule now says what it means: **frontmost**, which
    /// is last in index order, because a presented sheet comes after the view
    /// that presented it; and **full-width and tall**, because the two nested
    /// strips are scrollers too and a vertical drag must never take one. On
    /// the plain panel there is exactly one scroller in the tree
    /// (`{{0, 93.3}, {393.3, 676.7}}`, `printing-06-identify-tree`), so the
    /// filter changes nothing there.
    ///
    /// Frontmost applies to the containment branch as well, and that is not a
    /// nicety: the pushed-back Files scroller `{{16, 144.7}, {361, 621.6}}`
    /// *contains* the Print sheet's own `printer` row `{{16, 280.7}, {361,
    /// 44}}`, so a `first(where:)` over the same tree would hand back the page
    /// behind the sheet for an element on it.
    @MainActor
    func printingPageHolding(_ app: XCUIApplication, _ box: CGRect) -> XCUIElement? {
        let pages = app.scrollViews.allElementsBoundByIndex.filter { $0.frame.height > 0 }
        let wide = max(app.frame.width, 1) * 0.75
        let whole = pages.filter { $0.frame.width >= wide && $0.frame.height > 200 }
        let candidates = whole.isEmpty ? pages : whole
        return candidates.last(where: { $0.frame.insetBy(dx: -1, dy: -1).contains(box) })
            ?? candidates.last
            ?? pages.max(by: { $0.frame.height < $1.frame.height })
    }

    /// Move a scroll view's content by `points` (positive scrolls further down
    /// the page), with no momentum.
    ///
    /// **The four-argument `press` is the whole method.** The two-argument
    /// `press(forDuration:thenDragTo:)` is a press followed by a *flick* —
    /// XCTest's own log says "with velocity of 500.00 pixels per second, and
    /// then hold for 0.0 seconds" — and train R002 measured a drag asked for
    /// 143 points moving a page by 294. `withVelocity: .slow` plus
    /// `thenHoldForDuration` ends the gesture stationary, so the content stops
    /// where the finger did. The measurement and the two cases it cost are in
    /// ``musicMoreDrag(_:by:vertical:)``'s doc comment; this is the same
    /// correction, made here before this helper could repeat it.
    ///
    /// The other three numbers are that method's too, for the same reasons: at
    /// least 24 points of travel (less than a scroll view's pan slop is not a
    /// pan at all, it is a long press on whatever is under the finger), at most
    /// 80% of the scroller's height (the whole gesture has to stay inside it),
    /// and the finger starts near the edge it is travelling away from.
    ///
    /// The drag runs down the **left margin**, `dx: 0.03`. Every control on
    /// this panel starts at x = 16 or x = 30, so a finger at x ≈ 12 hit-tests
    /// to the scroll view itself rather than holding a button for 0.4 seconds.
    /// - Returns: `false` when no gesture was made at all — a scroller too
    ///   short to pan, or nothing asked for. The caller traces that rather
    ///   than reporting geometry that implies a motion which never happened.
    @MainActor
    @discardableResult
    func printingDrag(_ page: XCUIElement, by points: CGFloat) -> Bool {
        let height = page.frame.height
        guard height > 40, points != 0 else { return false }
        // The finger travels the opposite way to the content.
        let wanted = -points
        let leastTravel: CGFloat = 24
        let asked = abs(wanted) < leastTravel
            ? (wanted < 0 ? -leastTravel : leastTravel)
            : wanted
        let travel = max(-height * 0.8, min(height * 0.8, asked))
        let near: CGFloat = travel < 0 ? 0.9 : 0.1
        let from = page.coordinate(withNormalizedOffset: CGVector(dx: 0.03, dy: near))
        from.press(
            forDuration: 0.4,
            thenDragTo: from.withOffset(CGVector(dx: 0, dy: travel)),
            withVelocity: .slow,
            thenHoldForDuration: 0.4
        )
        return true
    }

    /// Scroll the panel back to its very top, so **nothing is under the
    /// navigation bar** when the audit samples it.
    ///
    /// This exists because of train S012. `testPrinting_PanelAudit` had to
    /// scroll down to reach `recent jobs` and tap it, which left the first
    /// card's `printer's own page` link at `y = 98…142` — and the navigation
    /// bar is `y = 93.3…137.3`. The link was *entirely behind the translucent
    /// bar*: it is not in the screenshot at all. Apple's audit still walks it,
    /// and what it sampled behind the glyphs was the bar's blur over whatever
    /// had scrolled under it, not the card — so `brandInk`, which is 5.65:1 on
    /// this app's canvas and passes on every other screen that draws a
    /// ``HomeLink``, came back as "Contrast failed" plus an unattributed
    /// "Contrast is not high enough … unless font size is larger".
    ///
    /// The control is not the defect: S009 already moved it from a `Link` to a
    /// ``HomeLink`` for the 44 points, and the 44 points are what pushed its
    /// box *further* under the bar. The **measurement** was the defect, and
    /// the fix is to measure with the panel at rest at the top, where a
    /// `ScrollView` under a `NavigationStack` puts its first content below the
    /// bar and no element is composited over the blur. This is not asserting
    /// less: every element is still in the audit, and a real contrast failure
    /// on any of them still fails the case — it is asserting against what a
    /// person can actually see.
    ///
    /// "At the top" is read as *the content stopped moving*, not as a flick
    /// count and not as one element clearing the bar: a header above the first
    /// card would still be under the bar when the card itself had cleared it.
    /// The panel has no `.refreshable`, so a swipe at the top is a no-op
    /// rather than a pull that reloads the list out from under the audit.
    @MainActor
    func printingScrollToTop(_ app: XCUIApplication, flicks: Int = 14) -> Bool {
        let anchor = app.staticTexts["office printer"]
        guard anchor.exists else { return false }
        for _ in 0..<flicks {
            let before = anchor.frame.minY
            app.swipeDown()
            if anchor.exists, abs(anchor.frame.minY - before) < 0.5 { return true }
        }
        print("=== \(flicks) FLICKS AND THE PANEL IS STILL NOT AT ITS TOP — tree follows ===")
        print(app.debugDescription)
        return false
    }

    /// Open Files, pick Documents and wait for the folder.
    @MainActor
    func printingOpenDocuments(
        _ mode: String = StubHouseUITests.ordinaryTypeSize,
        blocked: Bool = false
    ) -> XCUIApplication? {
        control("reset-fixtures", "{}", port: 6601, what: "the printing fixture's seed")
        let (app, landed) = openFiles(mode, blocked: blocked)
        guard landed else {
            XCTFail("the folder never answered")
            return nil
        }
        guard pickLibrary(app, "Documents") else {
            XCTFail("the library picker never opened")
            return nil
        }
        let marker = app.buttons["notes.md"]
        _ = marker.waitForExistence(timeout: 20)
        guard documentsScrollTo(app, marker) else {
            XCTFail("Documents never listed")
            return nil
        }
        return app
    }

    /// The value a ``PrintingMenuRow`` is showing, by the row's title.
    ///
    /// The title is unique per card by construction — "two-sided by default"
    /// is drawn only on the duplex printer and "colour by default" only on
    /// the colour one — where the **value** ("follow the printer") is the same
    /// sentence on both. So the title is the query and the value is the
    /// assertion, and a bare `staticTexts["follow the printer"]` would be
    /// ambiguous between two cards.
    @MainActor
    func printingMenuValue(_ app: XCUIApplication, _ title: String) -> String? {
        let row = app.buttons[title].firstMatch
        guard row.waitForExistence(timeout: 15) else {
            print("=== NO MENU ROW \(title) — tree follows ===")
            print(app.debugDescription)
            return nil
        }
        return row.value as? String
    }

    /// Pick one option out of a ``PrintingMenuRow``.
    @MainActor
    func printingPickFromMenu(_ app: XCUIApplication, _ title: String, _ option: String) -> Bool {
        let row = app.buttons[title].firstMatch
        guard printingScrollToHittable(app, row) else { return false }
        row.tap()
        // **`firstMatch` would take the element behind the menu.** The chosen
        // value is drawn on the row itself, so "follow the printer" exists
        // twice the moment the menu is up — once on the row and once as the
        // option. The option is the hittable one.
        let items = app.buttons.matching(NSPredicate(format: "label == %@", option))
        guard items.firstMatch.waitForExistence(timeout: 10) else {
            print("=== MENU \(title) HAS NO \(option) — tree follows ===")
            print(app.debugDescription)
            return false
        }
        for index in 0..<items.count {
            let candidate = items.element(boundBy: index)
            if candidate.exists, candidate.isHittable {
                candidate.tap()
                return true
            }
        }
        print("=== NO HITTABLE \(option) IN \(title) — tree follows ===")
        print(app.debugDescription)
        return false
    }

    // MARK: - the panel

    /// The panel draws the saved printers and says where renaming is done.
    ///
    /// It would fail if: the slot still drew `not built yet`; a ``Printer``
    /// failed to decode (one non-optional field would empty the whole list,
    /// which is map §2.5's trap); the card took the wrong field for its title;
    /// or the admin sentence — the entire answer to Rename and Remove — were
    /// dropped as decoration.
    @MainActor
    func testPrinting_PanelListsTheSavedPrinters() throws {
        guard let app = printingOpenPanel() else { return }
        evidence(app, "printing-01-panel")

        XCTAssertFalse(
            app.staticTexts[ShellStringsUnderTest.notBuilt].exists,
            "the printers slot is still drawing its placeholder"
        )
        XCTAssertTrue(app.staticTexts["office printer"].exists, "card 1 has no title")
        XCTAssertTrue(app.staticTexts["the colour one"].exists, "card 2 has no title")
        XCTAssertTrue(
            containing(app, "Brother HL-L2375DW \u{00b7} the study").exists,
            "card 1's subtitle is not the model and the location"
        )
        XCTAssertTrue(app.staticTexts["ready"].exists, "card 1 does not say what state it is in")
        XCTAssertTrue(
            startingWith(app, "An admin can rename or remove printers").exists,
            "the panel does not say where renaming is done, so Rename looks missing rather than elsewhere"
        )
        // The server's own words about printer 2, which no string in this app
        // could have produced.
        XCTAssertTrue(
            app.staticTexts["The paper tray is open."].exists,
            "the printer's own status words are not on screen"
        )
        XCTAssertTrue(
            app.staticTexts["The colour one is out of magenta."].exists,
            "needs_attention is not drawn"
        )
    }

    /// Ink is shown for the printer that reports it, and the other says so.
    ///
    /// The second half is the one that matters: a 0% bar for a printer that
    /// reports nothing is a **false reading** about a household's consumables,
    /// not a cosmetic default. `supplies_reported` is false on printer 2 and
    /// its `supplies` list is empty, so an app that ignored the flag would
    /// draw an empty bar and claim the cartridge was spent.
    @MainActor
    func testPrinting_InkIsShownOnlyWhereItIsReported() throws {
        guard let app = printingOpenPanel() else { return }
        evidence(app, "printing-02-ink")

        XCTAssertTrue(
            containing(app, "black toner 8%").exists,
            "the reported toner level is not on screen"
        )
        // The WHOLE line, not `containing("low")`: that matched card 1's
        // "follow the printer" (fol-LOW) and so passed against an app that
        // had dropped ``PrintingStrings/lowSuffix`` entirely. The supply row
        // is one combined accessibility element whose label is exactly
        // `PrinterSupplyBar.line`, so this is unambiguous.
        XCTAssertTrue(
            startingWith(app, "black toner 8% \u{00b7} low").exists,
            "the printer called the toner low and nothing says so"
        )
        XCTAssertTrue(
            app.staticTexts["this printer doesn't report ink levels"].exists,
            "the printer that reports no ink levels does not say so"
        )
        // Exactly one card reports ink, so exactly one `black toner` line.
        XCTAssertEqual(
            1,
            app.descendants(matching: .any)
                .matching(NSPredicate(format: "label BEGINSWITH %@", "black toner")).count,
            "an ink line was drawn for a printer that reports no levels"
        )
    }

    /// "follow the printer" is sent as a **literal JSON null**, and it sticks.
    ///
    /// The whole of map §1.4 in one case. The core's `PatchBody` is
    /// `extra: forbid` plus `model_dump(exclude_unset=True)`, so an omitted
    /// key means "don't touch it" and a `null` key means "follow the
    /// printer" — and `JSONEncoder` drops a `nil` from an `Optional`
    /// property. A body built by an encoder would therefore send `{}`, the
    /// stub would change nothing, and the dropdown would spring back to
    /// `two-sided`.
    ///
    /// It goes `follow the printer` → `two-sided` → `follow the printer` on
    /// purpose: the seed already *is* nil, so picking nil first would be a
    /// no-op that passed against a body that sent nothing at all.
    @MainActor
    func testPrinting_TheDefaultsThatFollowThePrinterSendALiteralNull() throws {
        guard let app = printingOpenPanel() else { return }
        let title = "two-sided by default"
        XCTAssertEqual(
            "follow the printer",
            printingMenuValue(app, title),
            "the seeded null is not shown as 'follow the printer'"
        )

        XCTAssertTrue(printingPickFromMenu(app, title, "two-sided"), "could not pick two-sided")
        XCTAssertTrue(
            printingWaitForMenuValue(app, title, "two-sided"),
            "the card did not take the server's answer for two-sided"
        )
        evidence(app, "printing-03-two-sided-set")

        XCTAssertTrue(
            printingPickFromMenu(app, title, "follow the printer"),
            "could not pick follow the printer"
        )
        XCTAssertTrue(
            printingWaitForMenuValue(app, title, "follow the printer"),
            "the card sprang back to two-sided — the PATCH carried no key, so the "
                + "literal null was dropped by an encoder"
        )
        evidence(app, "printing-04-two-sided-null")
    }

    /// Wait for a ``PrintingMenuRow`` to settle on a value.
    @MainActor
    func printingWaitForMenuValue(
        _ app: XCUIApplication,
        _ title: String,
        _ wanted: String,
        timeout: Double = 20
    ) -> Bool {
        let deadline = Date().addingTimeInterval(timeout)
        while Date() < deadline {
            if printingMenuValue(app, title) == wanted { return true }
            usleep(300_000)
        }
        print("=== \(title) NEVER REACHED \(wanted) — tree follows ===")
        print(app.debugDescription)
        return false
    }

    /// The two household-default pickers follow the **hardware**.
    ///
    /// `duplex` and `color` are the printer's own capabilities
    /// (`devices.py:239`), and a "two-sided by default" switch on a printer
    /// with one paper path is a control that cannot do what it says. Printer 1
    /// is duplex and mono; printer 2 is colour and single-sided; so each
    /// picker must appear exactly once on a panel showing both.
    @MainActor
    func testPrinting_DuplexAndColourControlsFollowTheHardware() throws {
        guard let app = printingOpenPanel() else { return }
        evidence(app, "printing-05-capability-gates")

        XCTAssertEqual(
            1,
            app.buttons.matching(NSPredicate(format: "label == %@", "two-sided by default")).count,
            "two-sided by default is drawn for a printer that is not duplex (or for neither)"
        )
        XCTAssertEqual(
            1,
            app.buttons.matching(NSPredicate(format: "label == %@", "colour by default")).count,
            "colour by default is drawn for a mono printer (or for neither)"
        )

        // The scan-only device (`can_print: false`, which is what the server
        // answers for a device with no IPP address, `devices.py:241`) gets
        // none of the printing half of a card: Android hides the Default
        // switch, the room chips, the dropdowns and the recent-jobs link
        // behind `if (p.canPrint)` (`PrintersPanel.kt:340,352,415`). On a
        // flatbed the switch cannot make it the house's print default, the
        // chips cannot either and the log is forever empty — three controls
        // that cannot do what they say.
        // `exists`, not a scroll: the panel's cards are not lazy, so every
        // card is built from the first frame (printingScrollToHittable's
        // own note says why).
        XCTAssertTrue(
            app.staticTexts["the flatbed"].exists,
            "the scan-only device has no card"
        )
        XCTAssertEqual(
            2,
            app.switches.matching(NSPredicate(format: "label CONTAINS %@", "Default")).count,
            "the Default switch is drawn on the scan-only device, which cannot be the print default"
        )
        XCTAssertEqual(
            2,
            app.buttons.matching(NSPredicate(format: "label == %@", "recent jobs")).count,
            "a recent-jobs link is drawn on a device that can never have printed anything"
        )
    }

    /// `Which one is it?` only where the server says the printer can identify
    /// itself.
    ///
    /// `identify` is the server's verdict on whether the printer has the IPP
    /// Identify operation at all. Without the gate the button would 409 on
    /// every printer that has not — which the fixture answers, so a panel that
    /// drew it twice would also show the refusal.
    @MainActor
    func testPrinting_WhichOneIsItOnlyWhereTheServerSaysSo() throws {
        guard let app = printingOpenPanel() else { return }
        evidence(app, "printing-06-identify")

        XCTAssertEqual(
            1,
            app.buttons.matching(NSPredicate(format: "label == %@", "Which one is it?")).count,
            "the identify button is drawn for a printer with no Identify operation"
        )
        // The printer that has it does what it says.
        let button = app.buttons["Which one is it?"].firstMatch
        XCTAssertTrue(printingScrollToHittable(app, button), "the identify button is unreachable")
        button.tap()
        XCTAssertTrue(
            startingWith(app, "office printer should beep or flash").waitForExistence(timeout: 20),
            "identify was pressed and nothing was said about it"
        )
    }

    /// The look offers `Add` only for what the house has **not** got, and the
    /// diagnostics sentence is the server's four numbers in the right places.
    ///
    /// **The panel looks by itself on open** (`PrintersPanel.kt:125`, one look
    /// with no sweep), which MAP §6.6 did not account for: the button
    /// therefore already reads `Look again` by the time the panel settles, and
    /// the found rows are on screen without a tap. So this asserts the
    /// auto-look's results, then taps `Look again` for the second half — the
    /// server's own sweep cap, which only a second look reports.
    @MainActor
    func testPrinting_LookForPrintersAddsOnlyWhatIsNotAdded() throws {
        guard let app = printingOpenPanel() else { return }
        let found = app.staticTexts["HP ENVY 6000"]
        XCTAssertTrue(found.waitForExistence(timeout: 25), "the look found nothing")
        evidence(app, "printing-07-found")

        // `Brother HL-L2375DW` arrives from the look with `already_added: 1`.
        // Card 1's subtitle is `Brother HL-L2375DW · the study`, which is a
        // different label, so a bare match here is a found row that the
        // already-added filter should have dropped — and tapping its `Add`
        // would add the house's own printer twice.
        XCTAssertFalse(
            app.staticTexts["Brother HL-L2375DW"].exists,
            "a printer the house already has is being offered for adding again"
        )
        XCTAssertTrue(
            app.staticTexts[
                "last look: found 2 printers \u{2014} 2 by multicast, 0 by unicast, 0 by sweep; 1 added by address"
            ].exists,
            "the diagnostics sentence is not the server's four numbers in the server's order"
        )

        let again = app.buttons["Look again"].firstMatch
        XCTAssertTrue(printingScrollToHittable(app, again), "no Look again button after the first look")
        again.tap()
        XCTAssertTrue(
            startingWith(app, "the network sweep runs at most once a minute").waitForExistence(timeout: 25),
            "a rate-limited sweep is not reported, so a person would just see nothing happen"
        )
        evidence(app, "printing-08-looked-again")
    }

    /// Adding a printer by address offers a test page, and the new printer is
    /// in the list.
    ///
    /// A typed name — `the hallway one`, a phrase nothing else in this house
    /// says — is what tells the added printer from the found row it shares a
    /// model with. The alert is the add → reload → offer chain: no alert means
    /// the 201's `can_print` was never read.
    @MainActor
    func testPrinting_AddingAPrinterOffersATestPage() throws {
        guard let app = printingOpenPanel() else { return }
        let byAddress = app.buttons["Add by address"].firstMatch
        XCTAssertTrue(printingScrollToHittable(app, byAddress), "no add-by-address button")
        byAddress.tap()

        let address = app.textFields["address"]
        XCTAssertTrue(address.waitForExistence(timeout: 15), "the add sheet has no address field")
        address.tap()
        app.typeText("192.168.0.60")
        let name = app.textFields["name (optional)"]
        XCTAssertTrue(name.exists, "the add sheet has no name field")
        name.tap()
        app.typeText("the hallway one")
        evidence(app, "printing-09-add-sheet")

        // The sheet's own `Add`, not the found row's behind it: the row stays
        // in the query tree while a sheet is up and stops being hittable.
        let add = app.buttons.matching(NSPredicate(format: "label == %@", "Add"))
        var tapped = false
        for index in 0..<add.count where !tapped {
            let candidate = add.element(boundBy: index)
            if candidate.exists, candidate.isHittable {
                candidate.tap()
                tapped = true
            }
        }
        XCTAssertTrue(tapped, "nothing hittable called Add inside the sheet")

        XCTAssertTrue(
            app.staticTexts["Print a test page?"].waitForExistence(timeout: 25),
            "the add landed and no test page was offered — the 201's can_print was not read"
        )
        evidence(app, "printing-10-test-page-offer")
        XCTAssertTrue(
            startingWith(app, "One page on the hallway one").exists,
            "the offer does not name the printer it would print on"
        )
        app.buttons["cancel"].firstMatch.tap()

        XCTAssertTrue(
            app.staticTexts["the hallway one"].waitForExistence(timeout: 25),
            "the added printer is not in the saved list, so the reload never happened"
        )
    }

    /// The recent list shows the failure in the printer's own words, and
    /// offers `cancel` only beside the job the server says can be cancelled.
    ///
    /// Two things at once, both of them decode-shaped: `GET /api/print/jobs`
    /// answers a **wrapper** `{"jobs": …, "scans": …}`, so a decoder that read
    /// the bare array shows an empty log that looks like a quiet house; and
    /// `can_cancel` is computed server-side, so a cancel beside a finished job
    /// is a button that 409s.
    @MainActor
    func testPrinting_RecentJobsShowTheFailureAndOfferCancel() throws {
        guard let app = printingOpenPanel() else { return }
        let open = app.buttons["recent jobs"].firstMatch
        XCTAssertTrue(printingScrollToHittable(app, open), "no recent-jobs link on a printer card")
        open.tap()

        // Matched by label across element types, not as `staticTexts`: since
        // the S009 audit a job's three lines are **one** accessibility
        // element (`PrinterJobRow.spoken`), so the row reports as the
        // combined element and its title is that element's prefix. The
        // assertions below are the same three facts, read off the one label.
        let failed = startingWith(app, "the council letter")
        XCTAssertTrue(failed.waitForExistence(timeout: 25), "the job log is empty — the wrapper was not decoded")
        evidence(app, "printing-11-recent-jobs")
        XCTAssertTrue(
            containing(app, "The printer said: cover open.").exists,
            "the failed job's reason is not shown, so the person is told only that it failed"
        )
        XCTAssertTrue(startingWith(app, "a spreadsheet.xlsx").exists, "the printing job is not listed")
        // The in-flight job's **progress**, not its bare state: Android is
        // `(if (j.finished) j.state else j.progress.ifBlank { j.state })`
        // (`PrintersPanel.kt:432-437`) and the server sends `progress` as a
        // finished sentence (`jobs.py:107-109`). "printing" alone would drop
        // the one number a person wants from a job that is still going.
        XCTAssertTrue(
            containing(app, "printing \u{00b7} 2 of 6").exists,
            "the unfinished job shows its bare state, not how far along it is"
        )
        // One of the three jobs has `can_cancel: true`.
        XCTAssertEqual(
            1,
            app.buttons.matching(NSPredicate(format: "label == %@", "cancel")).count,
            "a cancel is offered beside a job that has already finished"
        )
    }

    // MARK: - the Files hooks

    /// `Print` is offered for a file the server can print, and not for one it
    /// cannot.
    ///
    /// Discriminating by construction: `.pdf` is in the real `printable.exts`
    /// list copied out of `jobs.py:41-51` and `.svg` is not, so an app that
    /// ignored the table would offer a Print that answers 415.
    @MainActor
    func testPrinting_PrintIsOfferedOnlyForPrintableFiles() throws {
        guard let app = printingOpenDocuments() else { return }
        XCTAssertTrue(documentsScrollTo(app, app.buttons["scan.pdf"]), "scan.pdf's row never built")
        XCTAssertTrue(openRowMenu(app, "scan.pdf"), "scan.pdf has no row menu")
        XCTAssertTrue(
            app.buttons["Print"].firstMatch.waitForExistence(timeout: 15),
            "a .pdf is not offered a Print"
        )
        evidence(app, "printing-12-print-offered")
        // **A second launch rather than dismissing the menu.** There is no
        // reliable way to close a SwiftUI `Menu` without choosing something,
        // and every item in this one does something to a file. A relaunch is
        // slower and says exactly what it means.
        app.terminate()

        guard let second = printingOpenDocuments() else { return }
        XCTAssertTrue(documentsScrollTo(second, second.buttons["logo.svg"]), "logo.svg's row never built")
        XCTAssertTrue(openRowMenu(second, "logo.svg"), "logo.svg has no row menu")
        // A known item from the same menu, waited for first: without it this
        // could pass against a menu that had simply not drawn yet.
        XCTAssertTrue(
            second.buttons["move"].firstMatch.waitForExistence(timeout: 15),
            "the svg's menu never opened"
        )
        evidence(second, "printing-13-print-not-offered")
        XCTAssertFalse(
            second.buttons["Print"].exists,
            "a .svg is offered a Print the server answers 415 to"
        )
    }

    /// `scan` is in the Documents header, and nowhere else — and not at all
    /// from a phone an admin has blocked from changing files.
    ///
    /// The block is the house's own `/api/_test/files-blocked`, which moves
    /// **both** halves of the gate at once: `writable` on the folder listing,
    /// which is where the app's `canWrite` comes from, and the 403 the scan
    /// routes answer. One switch, so a case cannot prove the refusal while the
    /// button is still drawn.
    ///
    /// The server's 403 sentence is deliberately **not** asserted: with the
    /// button gone there is no way to reach it from the app, which is the
    /// point of the gate. It is covered where it can be — the fixture answers
    /// it, and the package tests cover
    /// ``printingOffersScan(printers:libraryID:canWrite:)``.
    @MainActor
    func testPrinting_ScanIsOfferedOnlyInDocumentsAndOnlyWhenWritable() throws {
        control("reset-fixtures", "{}", port: 6601, what: "the printing fixture's seed")
        let (app, landed) = openFiles()
        XCTAssertTrue(landed, "the folder never answered")
        // Music first: a scan goes into Documents and nowhere else.
        evidence(app, "printing-14-music-header")
        XCTAssertFalse(app.buttons["scan"].exists, "Music offers a scan button")

        XCTAssertTrue(pickLibrary(app, "Documents"), "the library picker never opened")
        XCTAssertTrue(
            app.buttons["scan"].waitForExistence(timeout: 25),
            "Documents does not offer a scan button"
        )
        evidence(app, "printing-15-scan-offered")
        app.terminate()

        guard let blocked = printingOpenDocuments(blocked: true) else { return }
        evidence(blocked, "printing-16-scan-blocked")
        XCTAssertFalse(
            blocked.buttons["scan"].exists,
            "a phone that may not change files is offered a scan that would 403 every time"
        )
    }

    // MARK: - the Print sheet

    /// Open the Print sheet on `scan.pdf`.
    @MainActor
    func printingOpenPrintSheet(_ app: XCUIApplication) -> Bool {
        guard documentsScrollTo(app, app.buttons["scan.pdf"]) else { return false }
        guard openRowMenu(app, "scan.pdf") else { return false }
        let item = app.buttons["Print"].firstMatch
        guard item.waitForExistence(timeout: 15) else {
            print("=== NO PRINT ITEM — tree follows ===")
            print(app.debugDescription)
            return false
        }
        item.tap()
        // The summary is the one line only a landed preview can produce.
        guard startingWith(app, "3 pages on").waitForExistence(timeout: 30) else {
            print("=== THE PREVIEW NEVER LANDED — tree follows ===")
            print(app.debugDescription)
            return false
        }
        return true
    }

    /// The sheet draws only the controls that can change something.
    ///
    /// Printer 1 offers one colour mode, so a `colour` picker over it is a
    /// control with one option — Kamron's no-misleading-controls rule in its
    /// smallest form. Printer 1 offers two `sides`, so that picker is there,
    /// and the pair of assertions together say the gate is `choices` and not a
    /// blanket "draw everything".
    @MainActor
    func testPrinting_PrintSheetShowsOnlyTheControlsThatCanChangeSomething() throws {
        guard let app = printingOpenDocuments() else { return }
        XCTAssertTrue(printingOpenPrintSheet(app), "the Print sheet never opened")
        evidence(app, "printing-17-print-sheet")

        XCTAssertTrue(app.textFields["pages"].exists, "no pages field on a three-page document")
        XCTAssertTrue(app.staticTexts["copies"].exists, "no copies control where copies_max is 20")
        XCTAssertTrue(
            app.buttons["two-sided"].exists,
            "no two-sided picker on a printer that offers two sides"
        )
        XCTAssertFalse(
            app.buttons["colour"].exists,
            "a colour picker is drawn for a printer that offers one colour mode"
        )
        XCTAssertFalse(
            app.buttons["paper"].exists,
            "a paper picker is drawn for a printer that offers one paper size"
        )
        XCTAssertTrue(
            containing(app, "studyside").exists,
            "the summary on screen is not the chosen printer's"
        )
    }

    /// Print sends the job and follows it to the end.
    ///
    /// The **intermediate** `printing · 1 of 3` is asserted on purpose: the
    /// stub advances one step per read, so a sheet that jumped straight to
    /// `done` never polled at all. The end line is
    /// ``printingJobEndMessage(_:)`` over the server's own job.
    @MainActor
    func testPrinting_PrintSendsTheJobAndFollowsItToTheEnd() throws {
        guard let app = printingOpenDocuments() else { return }
        XCTAssertTrue(printingOpenPrintSheet(app), "the Print sheet never opened")

        // Not named `print`: that shadows Swift's own `print`, and the tree
        // dumps in this file are the only diagnosis a train run gets.
        let printButton = app.buttons["Print"].firstMatch
        XCTAssertTrue(printButton.waitForExistence(timeout: 20), "the sheet has no Print button")
        XCTAssertTrue(printButton.isEnabled, "Print is disabled on a preview with no over_limit")
        printButton.tap()

        XCTAssertTrue(
            app.staticTexts["queued"].waitForExistence(timeout: 25),
            "the job was never shown as queued, so the 202 was not read"
        )
        evidence(app, "printing-18-job-queued")
        XCTAssertTrue(
            app.staticTexts["printing \u{00b7} 1 of 3"].waitForExistence(timeout: 30),
            "the sheet never showed the intermediate progress — the two-second poll did not run"
        )
        XCTAssertTrue(
            // `scan`, not `scan.pdf`: `_forward_print` sends the **stem** as
            // the job's title (`web/backend/api/printers.py:257-258`), which
            // the fixture now matches.
            app.staticTexts["Printed: scan (3 pages)"].waitForExistence(timeout: 30),
            "the job finished and the sheet does not say how it went"
        )
        evidence(app, "printing-19-job-done")
        XCTAssertTrue(app.buttons["close"].exists, "a finished job's sheet still says cancel")
    }

    /// A refusal the household's limits have already decided disables Print
    /// rather than letting it be pressed.
    ///
    /// The stub refuses more than two copies on printer 1, which the `+`
    /// button can reach (`copies_max` is 20) — so this is a path through the
    /// real controls and not a seeded flag.
    @MainActor
    func testPrinting_OverTheLimitRefusesBeforeAnythingIsSent() throws {
        guard let app = printingOpenDocuments() else { return }
        XCTAssertTrue(printingOpenPrintSheet(app), "the Print sheet never opened")

        let more = app.buttons["one more copy"].firstMatch
        XCTAssertTrue(more.waitForExistence(timeout: 20), "no way to ask for more copies")
        more.tap()
        more.tap()

        let refusal = startingWith(app, "That's more copies than this house allows")
        XCTAssertTrue(
            refusal.waitForExistence(timeout: 30),
            "the household's own refusal is not on screen"
        )
        evidence(app, "printing-20-over-limit")
        XCTAssertFalse(
            app.buttons["Print"].firstMatch.isEnabled,
            "Print can be pressed against a preview the house has already refused"
        )
    }

    /// A preview belongs to the printer that is chosen **now**, not to the one
    /// that was chosen when it was asked for.
    ///
    /// [[read-after-await-wrong-key]] in its print form. The stub holds
    /// printer 1's preview for six seconds; the case picks printer 2 while it
    /// is still in the air, then waits past the hold. `colourway` and
    /// `studyside` are each one printer's preview and nothing else's, so a
    /// sheet that filed the stale answer shows printer 1's page count, paper
    /// sizes and limits under printer 2's name — and then prints with them.
    ///
    /// The switch is **inside** the round trip, which is the only arrangement
    /// that catches it: two sequential previews both pass against the bug.
    @MainActor
    func testPrinting_PreviewForTheSheetThatIsOpenOnly() throws {
        guard let app = printingOpenDocuments() else { return }
        control(
            "printing",
            "{\"preview\": \"slow-once\"}",
            port: 6601,
            what: "printer 1's next preview to be held"
        )
        XCTAssertTrue(documentsScrollTo(app, app.buttons["scan.pdf"]), "scan.pdf's row never built")
        XCTAssertTrue(openRowMenu(app, "scan.pdf"), "scan.pdf has no row menu")
        let item = app.buttons["Print"].firstMatch
        XCTAssertTrue(item.waitForExistence(timeout: 15), "the row menu has no Print")
        item.tap()

        // The picker is drawn from the printer LIST and not from the preview,
        // so it is there while printer 1's answer is still being held —
        // waited for rather than swiped towards, because a swipe at a sheet
        // that has not come up yet scrolls the folder behind it.
        XCTAssertTrue(
            app.buttons["printer"].firstMatch.waitForExistence(timeout: 20),
            "the Print sheet has no printer picker, so there is nothing to switch"
        )
        XCTAssertTrue(
            printingPickFromMenu(app, "printer", "the colour one"),
            "could not switch printer while the first preview was in flight"
        )

        XCTAssertTrue(
            containing(app, "colourway").waitForExistence(timeout: 30),
            "the second printer's preview never landed"
        )
        // Past the hold, so the stale answer has arrived and been judged.
        // A deliberate wait and not a poll: what is being asserted is that
        // nothing happened when it landed.
        Thread.sleep(forTimeInterval: 8)
        evidence(app, "printing-21-preview-switched")
        XCTAssertFalse(
            containing(app, "studyside").exists,
            "printer 1's held preview was filed under printer 2 — the key was read after the await"
        )
        XCTAssertTrue(
            containing(app, "colourway").exists,
            "printer 2's preview was replaced by the stale one"
        )
        XCTAssertTrue(
            app.buttons["colour"].exists,
            "the choices on screen are not the chosen printer's — printer 2 offers two colour modes"
        )
    }

    // MARK: - the Scan sheet

    /// A scan goes into Documents, says where it went in the server's own
    /// words, and the folder behind the sheet has the new row.
    @MainActor
    func testPrinting_ScanSavesIntoDocumentsAndSaysWhere() throws {
        guard let app = printingOpenDocuments() else { return }
        let open = app.buttons["scan"].firstMatch
        XCTAssertTrue(open.waitForExistence(timeout: 25), "Documents offers no scan")
        open.tap()

        let start = app.buttons["Scan"].firstMatch
        XCTAssertTrue(start.waitForExistence(timeout: 20), "the scan sheet has no Scan button")
        start.tap()

        // `waiting · 1 page` is the stub's answer to the FIRST poll, so this
        // is the 1.5-second re-read being asserted and not the 201.
        XCTAssertTrue(
            app.staticTexts["waiting \u{00b7} 1 page"].waitForExistence(timeout: 30),
            "the sheet never saw the scanner finish a page — the poll did not run"
        )
        evidence(app, "printing-22-scan-waiting")

        let done = app.buttons["Done"].firstMatch
        XCTAssertTrue(done.waitForExistence(timeout: 20), "no Done on a scan with a page in hand")
        done.tap()

        XCTAssertTrue(
            app.staticTexts["Saved to Documents as scan 2026-11-02.pdf"].waitForExistence(timeout: 30),
            "the scan was saved and the sheet does not say where"
        )
        evidence(app, "printing-23-scan-saved")
        app.buttons["close"].firstMatch.tap()

        XCTAssertTrue(
            documentsScrollTo(app, app.buttons["scan 2026-11-02.pdf"]),
            "the saved scan is not in the folder, so onSaved never reloaded it"
        )
    }

    /// Walking away from a half-finished scan cancels it on the server.
    ///
    /// A scan that has started holds the household's scanner and has pages
    /// nothing has saved. Android cancels on dismiss
    /// (`ScanSheet.kt:101-105`) and so does this; a port that dropped it
    /// would leave a scan running on the house's scanner after the sheet
    /// closed, with the paper still on the glass.
    ///
    /// **The assertion is a question put to the stub.** Nothing on any screen
    /// shows a scan's state once the sheet is gone — the panel's recent list
    /// is prints only, as Android's is — so the fixture answers 409 unless
    /// every scan it holds is cancelled, and ``control(_:_:port:what:)``
    /// asserts the 200.
    @MainActor
    func testPrinting_LeavingMidScanCancelsIt() throws {
        guard let app = printingOpenDocuments() else { return }
        let open = app.buttons["scan"].firstMatch
        XCTAssertTrue(open.waitForExistence(timeout: 25), "Documents offers no scan")
        open.tap()

        let start = app.buttons["Scan"].firstMatch
        XCTAssertTrue(start.waitForExistence(timeout: 20), "the scan sheet has no Scan button")
        start.tap()
        // The **state line**, not the button's `scanning…` label: the button
        // says that while this sheet's own call is in flight, which is true
        // before the 201 has landed and before there is any scan to cancel.
        // A line of the form `<state> · N pages` can only come from a scan
        // the server made — and either state will do, because the stub
        // advances `scanning` to `waiting` on the sheet's first poll and a
        // case that insisted on the first of them would be racing that poll.
        let line = app.descendants(matching: .any)
            .matching(NSPredicate(
                format: "label BEGINSWITH %@ OR label BEGINSWITH %@",
                "scanning \u{00b7}", "waiting \u{00b7}"
            ))
            .firstMatch
        XCTAssertTrue(
            line.waitForExistence(timeout: 25),
            "the scan never started, so there would be nothing to cancel"
        )
        evidence(app, "printing-24-scan-running")

        app.buttons["close"].firstMatch.tap()
        XCTAssertTrue(waitUntilGone(line), "the scan sheet did not close")
        // The cancel leaves in a detached task, because the model goes away
        // with the sheet. A beat for it to land.
        Thread.sleep(forTimeInterval: 3)
        control(
            "printing",
            "{\"expect\": \"scan-canceled\"}",
            port: 6601,
            what: "the scan to have been cancelled when the sheet closed"
        )
    }

    // MARK: - the audit, and the type size

    /// Apple's own audit over the whole panel, classified by signature.
    ///
    /// `rest` must be empty: an anonymous Dynamic Type headline is the
    /// measured floor (``floorAndRest(_:)``), and anything Apple can
    /// attribute to an element is a defect to fix. Never a pinned total — a
    /// count drifts with the house's data, where a signature does not.
    ///
    /// ### Train I001: the case was right and the app was wrong
    ///
    /// Two "Contrast nearly passed … unless font size is larger" — one
    /// against `label='Test page'`, one with no element. The picture settled
    /// both. Sampled from `printing-25-audit` at 3x, the card's secondary
    /// buttons were `.buttonStyle(.bordered).tint(Color.brandInk)`, which
    /// washes the surface with the tint and then draws the label in it:
    /// `#975800` on `#E4D9C9` is **4.06:1**, under the 4.5 a `subheadline`
    /// needs, where the same ink on the bare card is 5.19:1. The second
    /// finding is the next button down — `Which one is it?` at
    /// `y = 763…797` against a scroll view that ends at 770, so the only
    /// strip of it Apple could sample holds no glyphs and it had nothing to
    /// attribute the finding to.
    ///
    /// Fixed in the app (`PrintingOutlineButtonStyle`), not here: the
    /// assertion is unchanged, and a wash coming back fails it again —
    /// including on a button the scroll edge cuts, because an anonymous
    /// *contrast* headline is not in ``knownAnonymousHeadlines`` and lands
    /// in `rest` like any other.
    @MainActor
    func testPrinting_PanelAudit() throws {
        guard let app = printingOpenPanel() else { return }
        // The recent list open too, so the audit covers the job rows: they
        // are the only part of the card with a button beside wrapping text.
        let open = app.buttons["recent jobs"].firstMatch
        if printingScrollToHittable(app, open) {
            open.tap()
            // The job rows are one combined element each since S009, so this
            // waits on the label's prefix rather than on a `staticTexts` hit.
            _ = startingWith(app, "the council letter").waitForExistence(timeout: 20)
        }
        // Back to the top before anything is sampled: the scrolling above is
        // what put a card control behind the translucent navigation bar on
        // S012, and the audit measured the bar's blur as that control's
        // background. ``printingScrollToTop(_:flicks:)`` says the whole of it.
        XCTAssertTrue(printingScrollToTop(app), "the panel would not return to its top")
        evidence(app, "printing-25-audit")
        let split = Self.floorAndRest(try auditIssues(app, "printing-panel"))
        XCTAssertEqual([], split.rest, "the printers panel has attributable audit findings")
    }

    /// The panel's rows grow with the type size.
    ///
    /// **Measured, not read off a headline**: a named Dynamic Type finding has
    /// twice been a false positive on this track. Two rows are measured
    /// because they fail differently — a supply row holds a glyph-width bar
    /// (an `@ScaledMetric`, which a fixed `frame(height:)` would pin), and a
    /// menu row holds a sentence that has to wrap (which a
    /// `minimumScaleFactor` would shrink instead).
    @MainActor
    func testPrinting_PanelAtAccessibilitySize() throws {
        func heights(_ mode: String, _ name: String) -> (supply: CGFloat, menu: CGFloat) {
            guard let app = printingOpenPanel(mode) else { return (0, 0) }
            let supply = app.descendants(matching: .any)
                .matching(NSPredicate(format: "label BEGINSWITH %@", "black toner"))
                .firstMatch
            let menu = app.buttons["two-sided by default"].firstMatch
            guard supply.waitForExistence(timeout: 25), menu.waitForExistence(timeout: 25) else {
                print("=== NO ROWS TO MEASURE AT \(mode) — tree follows ===")
                print(app.debugDescription)
                return (0, 0)
            }
            evidence(app, name)
            let found = (supply.frame.height, menu.frame.height)
            app.terminate()
            return found
        }

        let ordinary = heights(Self.ordinaryTypeSize, "printing-26-rows-large")
        let huge = heights("UICTContentSizeCategoryAccessibilityXXXL", "printing-27-rows-ax5")
        XCTAssertGreaterThan(ordinary.supply, 0, "the panel never drew a supply row at the ordinary size")
        XCTAssertGreaterThan(
            huge.supply, ordinary.supply,
            "a supply row is \(huge.supply)pt at AX5 and \(ordinary.supply)pt at large — it does not grow"
        )
        XCTAssertGreaterThan(
            huge.menu, ordinary.menu,
            "a dropdown row is \(huge.menu)pt at AX5 and \(ordinary.menu)pt at large — it does not grow"
        )
    }
}
