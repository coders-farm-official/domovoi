import XCTest

/// Music's second half: the playlists tab and its two sheets, the cover art,
/// the room-followed lyrics, and the tab that is deliberately not offered.
///
/// Every case is named `testMusicMore_…` so a train can hand its result back
/// to this item, and every piece of evidence is named `musicmore-…`.
///
/// ### Which track each case uses, and why it matters
///
/// `fixtures/musicmore.py` seeds six tracks whose words nothing else in the
/// house uses. **Track 501 is the one the parlour is playing**, so its title
/// is on screen twice — once on its library row and once on the room card —
/// and `containing(app, …).firstMatch` cannot say which. So no case here
/// opens 501's library row: the membership cases use **503**, the cover and
/// plain-lyrics cases **502**, the Favorites and create cases **505**, and
/// the timing-chip case **506**, each of which appears exactly once.
///
/// **Track 506 is synced and no room plays it** — the only combination that
/// reaches the no-room branch, where the timing chips would write nowhere and
/// are therefore not drawn.
///
/// ### One sheet at a time
///
/// Music has three sheets behind **one** `.sheet` modifier
/// (``MusicMoreSheet``), so `add to playlist` replaces the track sheet it was
/// reached from rather than opening over it, and one `close` comes back to
/// Music.
///
/// ### The two fixtures that exist to tell a kept guard from a dropped one
///
/// * **Track 502 has no cover**, so `GET …/502/cover` is a real 404 and the
///   placeholder is a measured answer rather than a screenshot of a loading
///   state.
/// * **The cellar's room-lyrics answer carries a document beside a null
///   `track_id`.** `MusicRoomLyricsModel.apply` keeps a doc only when the
///   track is not nil, and `isHidden` is true for a nil track; an app that
///   dropped either guard would print `musicmoreCellarLine` on the streaming
///   room's card. Its presence anywhere on screen is the failure.
extension StubHouseUITests {

    // MARK: - getting there

    /// Put the house back where every case below expects it.
    ///
    /// Cases share one house for the whole run, in alphabetical order, so a
    /// playlist another case created or a track it favourited is still there
    /// unless this is called first.
    @MainActor
    func musicMoreReset(port: Int = 6601) {
        control("reset-fixtures", "{}", port: port, what: "the musicmore seed")
    }

    /// Open Music and wait for it to have drawn.
    ///
    /// The crumb is the shell's; the tab bar is the screen's, and the second
    /// is what says the reads have answered rather than that the route was
    /// pushed.
    @MainActor
    func openMusicMore(_ mode: String = StubHouseUITests.ordinaryTypeSize)
        -> (app: XCUIApplication, landed: Bool) {
        let app = launch(mode: mode, push: "Music", pushDelay: Self.pushDelay)
        let crumb = app.staticTexts[ShellStringsUnderTest.musicCrumb]
        guard crumb.waitForExistence(timeout: 40) else { return (app, false) }
        let tab = startingWith(app, MusicMoreUnderTest.tabPlaylists)
        guard !tab.waitForExistence(timeout: 30) else { return (app, true) }
        // **The strip is below the room cards, and at an accessibility size
        // that is below the fold.** Train S007 failed here and the message
        // said "the playlists tab never drew a row", which reads as a screen
        // that did not answer; the tree beside it said otherwise. At
        // AX5 the three room cards above the strip are 1520 points tall
        // against a content area under 280 — the shell's crumb, the server
        // button, the navigation bar and the phone-source switch take the
        // rest of the 852, and R003 measured the content area at 178.7
        // before ``PhoneSourceCollapsedSwitch`` gave back the switch's
        // column — so the strip is not off screen, it is *unbuilt*, the lazy foot of a
        // `LazyVStack` nobody has scrolled to. Scrolling to it is what a
        // person does, so the case does it too, rather than the screen being
        // called broken. S010 then failed here a second time, for a
        // different reason: the flicks were landing on the wrong scroll view
        // (``musicMorePage(_:)``) and there were a third as many as the
        // distance needs.
        //
        // Not an app defect: Android stacks `NowPlayingCards` above
        // `ScrollableTabRow` in the same order, and at the ordinary size the
        // strip is 726 points down a 770-point window, which is where it has
        // always been.
        //
        // "Landed" above is existence on purpose, and S013 is why the fix for
        // it is **not** here. At the ordinary size the strip had drawn at
        // `{{16.0, 865.3}, {361.0, 44.0}}`, below the foot of a page whose
        // viewport ends at 770: built, and untouchable. Revealing it from
        // here would scroll the page for the cases that only read the room
        // cards above it too, and move controls they tap off the top; built
        // is all they need. Reaching it belongs to whoever touches it —
        // ``musicMoreTapTab(_:_:)``.
        return (app, musicMoreReveal(app, tab))
    }

    /// Open Music and move to the playlists tab.
    @MainActor
    func openPlaylistsTab(_ mode: String = StubHouseUITests.ordinaryTypeSize)
        -> (app: XCUIApplication, landed: Bool) {
        let (app, landed) = openMusicMore(mode)
        guard landed else { return (app, false) }
        guard musicMoreTapTab(app, MusicMoreUnderTest.tabPlaylists) else { return (app, false) }
        let row = containing(app, MusicMoreUnderTest.longwater)
        if row.waitForExistence(timeout: 30) { return (app, true) }
        // The rows are under the strip, so at an accessibility size they are
        // unbuilt for the same reason the strip was — see `openMusicMore`.
        return (app, musicMoreScroll(app, to: row))
    }

    // MARK: - reaching a control, by its frame and nothing else

    /// Whether `it` sits wholly inside `seat` **along the axis being
    /// scrolled**.
    ///
    /// ### Why this file no longer asks `isHittable` at all
    ///
    /// **`isHittable` is not a total function, and train I001 paid for it
    /// three times.** For an element whose activation point falls outside
    /// the window XCTest cannot compute one and cannot sample one off the
    /// frame either, so the getter does not return `false`: it **fails the
    /// test** with "Failed to determine hittability of "playlists" Button:
    /// Activation point invalid and no suggested hit points based on element
    /// frame". The measurement from that train's own snapshot is
    /// `playlists` at `{{522.0, 485.0}, {193.0, 58.7}}` inside a strip at
    /// `{{16.0, 485.0}, {361.0, 58.7}}` on a 393-point screen — a midpoint
    /// at x 618.5, 225 points off the right-hand edge. Three cases died on
    /// that message at the one line that merely **asked** the question
    /// (``musicMoreScrollOnScreen(_:_:flicks:)``), before any tap.
    ///
    /// Round 1 guarded the getter with the window's frame. That is sound and
    /// not enough: the getter is still asked, about an element whose visible
    /// frame can be empty for a second reason (a clip, not the screen's
    /// edge), and a guard that has to predict every input a getter cannot
    /// answer for is a guard that will be wrong again. So the walks are now
    /// **geometry only** — ``musicMoreBring(_:into:vertical:steps:)`` and
    /// these three — and `isHittable` appears nowhere in this file. The one
    /// question left for XCTest is the `tap()` itself, by which point the
    /// control has been measured wholly inside its own clip.
    ///
    /// ### Why containment is measured on one axis
    ///
    /// Train S008 (Rooms, the same strip problem on another screen) is the
    /// precedent: `CGRect.contains(_:)` asks about both axes, and a tab
    /// inside ``MusicTabBar`` is **exactly as tall as the strip** — 58.7
    /// against 58.7 at AX5, 44 against 44 at the ordinary size. The two are
    /// then equal to within SwiftUI's layout rounding between a clip and its
    /// content, and that rounding fell the wrong way there: a tab plainly
    /// inside its strip horizontally was reported outside because its bottom
    /// edge sat a fraction below the strip's, the shortfall came back zero,
    /// and no drag was ever made. Containment across the scroll axis is also
    /// the only part that bears on tappability.
    ///
    /// **Not an app defect.** ``MusicTabBar`` is a horizontal `ScrollView`
    /// on purpose (Android's `ScrollableTabRow`), and at AX5 its four words
    /// are 828 points wide inside a 361-point frame; a word off the right
    /// edge of a strip that scrolls is a word a person drags to, which is
    /// what ``musicMoreTapTab(_:_:)`` now does.
    func musicMoreInside(_ it: CGRect, _ seat: CGRect, vertical: Bool) -> Bool {
        // Half a point: far smaller than any real clipping, which is tens of
        // points, so a control genuinely cut off is still reported outside.
        let slack: CGFloat = 0.5
        return vertical
            ? (it.minY >= seat.minY - slack && it.maxY <= seat.maxY + slack)
            : (it.minX >= seat.minX - slack && it.maxX <= seat.maxX + slack)
    }

    /// How far, in points, `seat`'s content must move for `it` to sit inside
    /// it — negative towards the top or the left, zero when it already fits.
    ///
    /// The eight-point margin is why a measured drag converges where the
    /// flicks did not: landing a control exactly on the clip's edge is
    /// landing it outside, and a drag measured to the edge lands there about
    /// half the time.
    ///
    /// **An element bigger than its own clip** can never be wholly inside it,
    /// and asking for that is how a loop oscillates until its budget runs
    /// out. At AX5 Music's page viewport is under 280 points tall — it was
    /// 178.7 while the phone-source switch was a column there, see
    /// `integration/sourceswitch-r4.md` — while one room card is 1520, so
    /// this is not hypothetical. The best
    /// that can be asked of such an element is that its **leading edge** is
    /// inside, which is where a tap on it lands.
    func musicMoreShortfall(_ it: CGRect, in seat: CGRect, vertical: Bool) -> CGFloat {
        let margin: CGFloat = 8
        let itNear = vertical ? it.minY : it.minX
        let itFar = vertical ? it.maxY : it.maxX
        let seatNear = vertical ? seat.minY : seat.minX
        let seatFar = vertical ? seat.maxY : seat.maxX
        if itFar - itNear >= (seatFar - seatNear) - 2 * margin {
            return (seatNear + margin) - itNear
        }
        if itFar > seatFar { return -(itFar - seatFar + margin) }
        if itNear < seatNear { return (seatNear - itNear) + margin }
        return 0
    }

    /// Drag a scroller by a measured number of points, and say whether a drag
    /// was attempted.
    ///
    /// A press-and-drag, not a flick: a flick's momentum moves the content by
    /// an amount nobody asked for, which at AX5 is how the build scroll
    /// carried the tab strip 38 points **past** the page's top in one go
    /// (I001's own snapshot: content at `minY -1083.3`, strip at y 485,
    /// viewport from 523.3).
    ///
    /// ### The hold at the end, and why round 2's drag still flicked
    ///
    /// Round 2 wrote here that "a press has no momentum, so it moves the
    /// content by the number of points asked for and converges from either
    /// side". **That was a belief about the API, and train R002 measured it
    /// false.** `press(forDuration:thenDragTo:)` is a press followed by a
    /// *flick*: XCTest's own log line for it is "… then drag to … **with
    /// velocity of 500.00 pixels per second, and then hold for 0.0
    /// seconds**", and lifting a finger that is still moving is what a
    /// scroll view reads as a throw however long the press before it was.
    /// R002's numbers, from ``musicMoreBring(_:into:vertical:steps:)``'s own
    /// re-measurement after each drag: a drag asked for -142.9 points moved
    /// the page's content about 294, and the one asked for +24 moved it
    /// nearly 290. So the loop never converged — thirty drags alternating
    /// -142.9, +47.3, -136.0, +74.3, … with the shortfall no smaller at the
    /// end than at the start — and the two Dynamic Type cases reported the
    /// playlists tab as never drawn.
    ///
    /// The fix is the fourth argument: `thenHoldForDuration`. Holding at the
    /// destination before the lift ends the gesture stationary, so the
    /// scroller takes no velocity from it and the content stops where the
    /// finger did. This is the same correction `assistantCreep` made on the
    /// Assistant screen ("the hold is what kills the momentum: lifting
    /// mid-drag is a flick however slowly the drag was made",
    /// `StubHouseUITests+Assistant.swift:896`) — the two walks were written
    /// the same night and only one of them had been measured.
    ///
    /// The whole gesture has to stay inside the scroller — a touch that
    /// leaves it is a touch on something else, and on this screen the
    /// something else is the pinned pill above or the phone bar below — so
    /// one drag travels at most 80% of the scroller's own extent and
    /// ``musicMoreBring(_:into:vertical:steps:)``'s loop does the rest.
    ///
    /// A shortfall of zero means **no drag was attempted**, so it answers
    /// false: the caller has already asked whether the element is inside.
    @MainActor
    func musicMoreDrag(_ view: XCUIElement, by delta: CGFloat, vertical: Bool) -> Bool {
        guard delta != 0, view.exists else { return false }
        let box = view.frame
        let extent = vertical ? box.height : box.width
        guard extent > 40 else { return false }
        // **At least 24 points, whatever the shortfall asked for.** A gesture
        // that moves less than a scroll view's pan slop is not a pan: the
        // touch stays with whatever is under the finger, and a `Button` that
        // keeps it fires on touch-up however long the press was (SwiftUI's
        // button has no duration limit). The smallest shortfall this file can
        // produce is a hair over the eight-point margin, which is inside that
        // slop — so the floor is the difference between dragging a page and
        // tapping a row of it. Overshooting by up to sixteen points lands the
        // element further *inside* the clip, which is the harmless direction,
        // and an element nearly as tall as its clip is routed to the
        // leading-edge branch of ``musicMoreShortfall(_:in:vertical:)``
        // before it gets here.
        let leastTravel: CGFloat = 24
        let asked = abs(delta) < leastTravel
            ? (delta < 0 ? -leastTravel : leastTravel)
            : delta
        let travel = max(-extent * 0.8, min(extent * 0.8, asked))
        // Start near the edge the content is travelling away from, so the far
        // end of the gesture is still inside the scroller.
        let near: CGFloat = travel < 0 ? 0.9 : 0.1
        let from = view.coordinate(
            withNormalizedOffset: vertical
                ? CGVector(dx: 0.5, dy: near)
                : CGVector(dx: near, dy: 0.5)
        )
        // The press before the move is long enough that the control under
        // the finger cannot read the gesture as a tap; the **hold after** it
        // is what makes the whole thing a drag rather than a flick — see
        // this method's doc comment for the measurement that cost two cases.
        from.press(
            forDuration: 0.4,
            thenDragTo: from.withOffset(
                vertical ? CGVector(dx: 0, dy: travel) : CGVector(dx: travel, dy: 0)
            ),
            withVelocity: .slow,
            thenHoldForDuration: 0.4
        )
        return true
    }

    /// Move `view`'s content until `element` sits inside `view` on one axis.
    ///
    /// Measured drags first, then the flicks they replaced as a fallback —
    /// if the drag gesture is not recognised at all, nothing else would move
    /// the content, and the cases that were green on flicks stay green.
    ///
    /// ### Each step is checked, and the flicks are no longer unconditional
    ///
    /// **Train R002 is why.** Two things went wrong together there and only
    /// the first was the drag's own fault:
    ///
    /// 1. The drag carried momentum (``musicMoreDrag(_:by:vertical:)``), so
    ///    the measured loop oscillated through all thirty of its steps
    ///    without ever landing the tab strip inside the page.
    /// 2. The loop then fell into the flicks **because it had run out of
    ///    steps**, and the first `swipeDown` carried the page far enough
    ///    that its `LazyVStack` unbuilt the strip. The next iteration's
    ///    `guard element.exists` saw nothing and answered false, so
    ///    ``musicMoreReveal(_:_:flicks:)`` reported an element it had
    ///    already built as never drawn — "the playlists tab never drew a row
    ///    at UICTContentSizeCategoryAccessibilityXXXL", about a screen that
    ///    had drawn it twice.
    ///
    /// So the flicks are now reached only by the one condition their own
    /// comment names: **a drag the scroller did not see**, which is a drag
    /// after which the element has not moved by even a point. A loop that is
    /// moving the element but has run out of steps reports what it measured
    /// and prints the frames, rather than handing a nearly-converged page to
    /// a gesture that scrolls by an amount nobody asked for. And an element
    /// a drag has just unbuilt is dragged back the way it came, so what the
    /// caller gets is a measurement and not the absence of one.
    @MainActor
    func musicMoreBring(
        _ element: XCUIElement,
        into view: XCUIElement,
        vertical: Bool,
        steps: Int = 10
    ) -> Bool {
        // Starts true: the flicks are the answer to a gesture that did
        // nothing, and until a drag has been measured and moved nothing,
        // nothing says the scroller is deaf.
        var scrollerTookTheDrag = true
        // **The damper, and the measurement it answers.** R002 and R003 both
        // measured this loop asking for 142.9 points and getting about 294 —
        // the same ~285 of travel whatever it asked for, in either direction,
        // because `press(forDuration:thenDragTo:)` lifts the finger while it
        // is still moving and a scroller reads that as a throw. The loop then
        // alternated (−142.9, +47.3, −136.0, +74.3, …) with the shortfall no
        // smaller at step 30 than at step 1.
        //
        // ``musicMoreDrag(_:by:vertical:)`` now holds before the lift, which
        // is the correction `assistantCreep` measured
        // (`StubHouseUITests+Assistant.swift:949`) and which R003 never ran —
        // its log line still reads "hold for 0.0 seconds", so the three
        // MusicMore reds in that train are round 2's gesture, not round 3's.
        // This halving is the belt on top of that brace: whatever residual
        // momentum a drag carries, a loop that overshot once asks for half as
        // much next time, so it converges geometrically instead of ringing.
        // It cannot mask a real failure — the element's frame is still what
        // decides, and the ask never goes below
        // ``musicMoreDrag(_:by:vertical:)``'s 24-point floor, which is well
        // inside the acceptance window (219 points on Music at AX5: a
        // 278-point viewport less the 58.7-point strip).
        var damp: CGFloat = 1
        var lastSign: CGFloat = 0
        for step in 0..<steps {
            guard element.exists, view.exists else { return false }
            let before = element.frame
            if self.musicMoreInside(before, view.frame, vertical: vertical) { return true }
            let shortfall = self.musicMoreShortfall(
                before, in: view.frame, vertical: vertical
            )
            let sign: CGFloat = shortfall < 0 ? -1 : 1
            if lastSign != 0, sign != lastSign { damp /= 2 }
            lastSign = sign
            if !self.musicMoreDrag(view, by: shortfall * damp, vertical: vertical) { break }
            // A lazy container can unbuild the element the drag was for. Put
            // it back the way it came rather than leaving the caller with no
            // frame to measure at all.
            guard element.exists else {
                _ = self.musicMoreDrag(view, by: -shortfall * damp, vertical: vertical)
                break
            }
            let after = element.frame
            let moved = vertical
                ? abs(after.minY - before.minY)
                : abs(after.minX - before.minX)
            if moved < 1 {
                scrollerTookTheDrag = false
                break
            }
            if step == steps - 1 {
                // Printed, not swallowed: a walk that is moving and still
                // short after its budget is a number the next train needs.
                print(
                    "=== BRING RAN OUT OF STEPS: element \(after) in \(view.frame), "
                        + "last shortfall \(shortfall), damp \(damp), last step moved \(moved) ==="
                )
            }
        }
        guard !scrollerTookTheDrag else {
            guard element.exists, view.exists else { return false }
            return self.musicMoreInside(element.frame, view.frame, vertical: vertical)
        }
        for _ in 0..<steps {
            guard element.exists, view.exists else { return false }
            let seat = view.frame
            let it = element.frame
            if self.musicMoreInside(it, seat, vertical: vertical) { return true }
            if vertical {
                if it.midY > seat.midY {
                    view.swipeUp(velocity: .slow)
                } else {
                    view.swipeDown(velocity: .slow)
                }
            } else {
                if it.midX > seat.midX {
                    view.swipeLeft(velocity: .slow)
                } else {
                    view.swipeRight(velocity: .slow)
                }
            }
        }
        guard element.exists, view.exists else { return false }
        return self.musicMoreInside(element.frame, view.frame, vertical: vertical)
    }

    /// Scroll Music until `element` is built **and on screen**.
    ///
    /// ``musicMoreScroll(_:to:flicks:)`` answers "is it built", which is the
    /// question a lazy foot asks; this one also answers "can it be touched",
    /// which is the question S013 answered wrongly: the tab strip had drawn
    /// 95 points below the page's viewport, and a flick on an element whose
    /// visible frame is empty throws rather than scrolling it into view.
    @MainActor
    func musicMoreReveal(_ app: XCUIApplication, _ element: XCUIElement, flicks: Int = 60) -> Bool {
        guard musicMoreScroll(app, to: element, flicks: flicks) else { return false }
        musicMoreScrollOnScreen(app, element)
        return element.exists
    }

    /// Move Music's page until `element` sits inside the page's viewport.
    ///
    /// The viewport is ``musicMorePage(_:)``'s own frame, not the window's:
    /// an element 95 points past the foot of the page is still inside a
    /// 852-point screen and still untouchable. A word that is inside the
    /// viewport vertically but off the **right** edge — the AX5 case
    /// ``musicMoreTapTab(_:_:)`` exists for — is left alone here, because a
    /// vertical gesture is not what moves it: that is the sideways half,
    /// against the strip and not against the page.
    @MainActor
    func musicMoreScrollOnScreen(_ app: XCUIApplication, _ element: XCUIElement, flicks: Int = 30) {
        guard element.exists, let page = musicMorePage(app) else { return }
        _ = musicMoreBring(element, into: page, vertical: true, steps: flicks)
    }

    /// Open one playlist's sheet from the tab.
    @MainActor
    func openPlaylistSheet(_ app: XCUIApplication, _ name: String) -> Bool {
        let row = containing(app, name)
        guard row.waitForExistence(timeout: 20) else {
            // The tree, not a sentence about what is missing: "the row is not
            // there" is a claim about an absence and the only useful answer is
            // what *is*.
            print("=== NO PLAYLIST ROW for \(name) — tree follows ===")
            print(app.debugDescription)
            return false
        }
        // The last row of the tab can be past the page's foot for the same
        // reason the strip was (S013): built, and untouchable.
        musicMoreScrollOnScreen(app, row)
        row.tap()
        // The sheet's own eyebrow, not the name: the name is also on the row
        // behind the sheet, so waiting for it would not prove the sheet came
        // up at all.
        return app.buttons[MusicMoreUnderTest.close].waitForExistence(timeout: 20)
    }

    /// Tap one of the four tab words, bringing it **sideways** into view
    /// first.
    ///
    /// **Train S012 is why this is not a `tap()`.** The strip is a
    /// horizontal `ScrollView` (`MusicTabBar`, Android's `ScrollableTabRow`),
    /// and at AX5 its four words are 828 points wide inside a 361-point
    /// frame: I001's snapshot has `playlists` at
    /// `{{522.0, 485.0}, {193.0, 58.7}}` in a strip at
    /// `{{16.0, 485.0}, {361.0, 58.7}}`, so its right-hand edge is 322
    /// points past the strip's and its midpoint 225 past a 393-point
    /// screen's. XCUITest's own "scroll element to visible" cannot move a
    /// horizontal scroll view, so the tap failed with `Activation point
    /// invalid and no suggested hit points`, and the case read as the
    /// playlists tab being broken when the screen was fine and the word was
    /// simply off the right-hand edge.
    ///
    /// A person drags the strip. So does this: two measured brings, one per
    /// axis, and no `isHittable` anywhere — see ``musicMoreInside(_:_:vertical:)``
    /// for the three cases train I001 lost to asking that getter about the
    /// very word this walk exists to reach.
    @MainActor
    func musicMoreTapTab(_ app: XCUIApplication, _ word: String) -> Bool {
        let tab = startingWith(app, word)
        guard tab.waitForExistence(timeout: 30) else {
            print("=== NO '\(word)' TAB ON MUSIC — tree follows ===")
            print(app.debugDescription)
            return false
        }
        // Down the page before sideways along the strip: a strip outside the
        // page's clip cannot be dragged at all (S013 — a gesture on an
        // element whose visible frame is empty throws instead of scrolling
        // it), and the frames the sideways bring measures are only meaningful
        // once the strip is inside the page.
        //
        // The **strip** is what is brought into the page, not the word: at
        // AX5 the word is 225 points off the right-hand edge, and a frame
        // that far outside the window is one XCTest will not drag to.
        if let page = musicMorePage(app), let strip = musicMoreTabStrip(app, holding: tab) {
            _ = musicMoreBring(strip, into: page, vertical: true)
        }
        // Re-found after the vertical bring: ``musicMoreTabStrip(_:holding:)``
        // picks the strip by the band the tab's own middle falls in, and that
        // band has just moved.
        guard let strip = musicMoreTabStrip(app, holding: tab) else {
            print("=== NO STRIP AROUND '\(word)' AT \(tab.frame) — tree follows ===")
            print(app.debugDescription)
            return false
        }
        guard musicMoreBring(tab, into: strip, vertical: false) else {
            print(
                "=== '\(word)' TAB STILL OUTSIDE ITS STRIP: tab \(tab.frame) "
                    + "in strip \(strip.frame) — tree follows ==="
            )
            print(app.debugDescription)
            return false
        }
        tab.tap()
        return true
    }

    /// The tab strip, found by the tab it holds.
    ///
    /// Not by order and not by "the one that scrolls sideways": at AX5 the
    /// **phone bar** scrolls sideways too (``musicMorePage(_:)`` is where
    /// that cost a train). The strip is the scroll view that is as tall as
    /// the tab itself — 58.7 points at AX5, 44 at the ordinary size, against
    /// the bar's 146 and the page's 178.7 — and whose band the tab's own
    /// middle falls inside.
    @MainActor
    func musicMoreTabStrip(_ app: XCUIApplication, holding tab: XCUIElement) -> XCUIElement? {
        let middle = tab.frame.midY
        return app.scrollViews.allElementsBoundByIndex.first {
            abs($0.frame.height - tab.frame.height) < 8
                && $0.frame.minY <= middle && middle <= $0.frame.maxY
        }
    }

    /// Music's own page scroll view — the one a flick should move.
    ///
    /// **Not `app.scrollViews.firstMatch`, and train S010 is why.** Music's
    /// screen has three scroll views, and at an accessibility size the one
    /// the query returned first was the **phone bar** — `home · music ·
    /// videos · chat` goes scrollable sideways once its words are 48 points
    /// tall. Twelve flicks landed on `ScrollView at {{0, 706}, {393, 146}}`,
    /// which is that bar, while the page sat at `{{0, 523.3}, {393, 178.7}}`
    /// and never moved; the case then reported the strip as never drawn. The
    /// page is the one that scrolls **vertically**: its content is 3652
    /// points inside a 178-point viewport, where the bar's content is
    /// shorter than its own frame and the tab strip's is exactly as tall as
    /// its own. So pick by overflow, not by order.
    @MainActor
    func musicMorePage(_ app: XCUIApplication) -> XCUIElement? {
        guard app.scrollViews.firstMatch.waitForExistence(timeout: 20) else { return nil }
        return app.scrollViews.allElementsBoundByIndex
            .map { ($0, musicMoreOverflow($0)) }
            .filter { $0.1 > 1 }
            .max(by: { $0.1 < $1.1 })?
            .0
    }

    /// How much taller a scroll view's content is than the scroll view.
    @MainActor
    func musicMoreOverflow(_ page: XCUIElement) -> CGFloat {
        let content = page.children(matching: .other).firstMatch
        guard content.exists else { return 0 }
        return content.frame.height - page.frame.height
    }

    /// Scroll Music until `element` is built.
    ///
    /// **Not `scrollUntilBuilt`**: that one scrolls
    /// `app.collectionViews.firstMatch`, and Music's list is a `LazyVStack`
    /// inside a `ScrollView` (the owner's 2026-10-11 load-as-you-scroll
    /// decision, in SwiftUI's own container), so there is no collection view
    /// on this screen at all and it would report every row as unbuilt.
    ///
    /// `.slow`, for the reason the Chat pass wrote down: a plain `swipeUp()`
    /// steps over rows, and a lazy list's foot is unbuilt rather than merely
    /// off screen.
    ///
    /// **Sixty flicks, not twelve, and a stop when nothing moves.** At AX5
    /// the three room cards above the strip are 1520 points tall and the
    /// whole page is 3652 inside a 178-point viewport, so a slow flick —
    /// momentum proportional to that short viewport — is worth under a
    /// hundred points and the foot is thirty-five flicks away. The budget is
    /// the distance, and the `minY` check is what keeps a case that is
    /// *already* at the foot from paying for it: the moment a flick moves
    /// the content less than a point, the content is as far up as it goes
    /// and another fifty-nine would change nothing.
    @MainActor
    func musicMoreScroll(_ app: XCUIApplication, to element: XCUIElement, flicks: Int = 60) -> Bool {
        if element.exists { return true }
        guard let page = musicMorePage(app) else {
            print("=== NO SCROLLING SCROLL VIEW ON MUSIC — tree follows ===")
            print(app.debugDescription)
            return element.exists
        }
        let content = page.children(matching: .other).firstMatch
        var last = CGFloat.greatestFiniteMagnitude
        for _ in 0..<flicks {
            if element.exists { return true }
            // `nan` when the content element is momentarily not in the
            // snapshot: a missing measurement is not "nothing moved", so it
            // keeps flicking rather than deciding it is at the foot.
            let top = content.exists ? content.frame.minY : CGFloat.nan
            if !top.isNaN, abs(top - last) < 1 { break }
            last = top
            page.swipeUp(velocity: .slow)
        }
        if !element.exists {
            print("=== SCROLLED TO THE FOOT AND STILL UNBUILT — tree follows ===")
            print(app.debugDescription)
        }
        return element.exists
    }

    /// Back to the top, so the tab bar is reachable again after a scroll.
    ///
    /// ``musicMorePage(_:)``, for the reason written there: the first scroll
    /// view on this screen can be the phone bar, and swiping that one moves
    /// nothing.
    @MainActor
    func musicMoreScrollTop(_ app: XCUIApplication, flicks: Int = 12) {
        guard let page = musicMorePage(app) else { return }
        let bar = startingWith(app, MusicMoreUnderTest.tabPlaylists)
        // The strip can be unbuilt as well as off the top — a `LazyVStack`
        // foot the page has scrolled past — and a frame no element has is a
        // frame no bring can measure, so the flicks come first and only until
        // it exists.
        for _ in 0..<flicks {
            if bar.exists { break }
            page.swipeDown(velocity: .slow)
        }
        guard bar.exists else { return }
        // Then by measurement, not by `isHittable`: at AX5 this word is 225
        // points off the right-hand edge and the getter fails the case rather
        // than answering it (I001). What this helper owes its callers is a
        // strip inside the page's clip; reaching the word along the strip is
        // ``musicMoreTapTab(_:_:)``'s half.
        _ = musicMoreBring(bar, into: page, vertical: true, steps: flicks)
    }

    /// Open a library row's track sheet, by a title that appears once.
    @MainActor
    func openTrackSheet(_ app: XCUIApplication, _ title: String) -> Bool {
        let row = containing(app, title)
        guard musicMoreReveal(app, row) else { return false }
        row.tap()
        return app.buttons[MusicMoreUnderTest.close].waitForExistence(timeout: 20)
    }

    /// Close whatever sheet is up.
    @MainActor
    func closeSheet(_ app: XCUIApplication) {
        let close = app.buttons[MusicMoreUnderTest.close]
        if close.exists { close.tap() }
        _ = waitUntilGone(close, timeout: 15)
    }

    /// What the open sheet says about **its own** write.
    ///
    /// Matched by `identifier`, which only `MusicMoreSheetStatus` carries, so
    /// nothing behind the sheet can satisfy it. That matters: the shell's
    /// toast is an overlay on the shell's content and a page sheet covers it,
    /// so these outcomes used to be drawn behind the sheet that was still up
    /// — and four of these cases passed anyway, because
    /// `app.descendants(matching: .any)` reaches an element behind a sheet
    /// (NIGHT-RULES §5). Asserted this way they fail if the message is
    /// invisible.
    @MainActor
    func sheetSaid(
        _ app: XCUIApplication,
        startsWith prefix: String,
        timeout: Double = 20
    ) -> Bool {
        app.staticTexts.matching(
            NSPredicate(
                format: "identifier == %@ AND label BEGINSWITH %@",
                MusicMoreUnderTest.sheetStatus, prefix
            )
        ).firstMatch.waitForExistence(timeout: timeout)
    }

    /// ``sheetSaid(_:startsWith:timeout:)`` for a phrase inside the sentence
    /// rather than at the front of it.
    @MainActor
    func sheetSaid(
        _ app: XCUIApplication,
        containing phrase: String,
        timeout: Double = 20
    ) -> Bool {
        app.staticTexts.matching(
            NSPredicate(
                format: "identifier == %@ AND label CONTAINS %@",
                MusicMoreUnderTest.sheetStatus, phrase
            )
        ).firstMatch.waitForExistence(timeout: timeout)
    }

    /// How many elements on screen carry a label containing `text`.
    ///
    /// For the half of an assertion that is an **absence**. `.exists` on a
    /// `firstMatch` answers the same question, and this one prints a number
    /// a failure message can carry.
    @MainActor
    func countContaining(_ app: XCUIApplication, _ text: String) -> Int {
        app.descendants(matching: .any)
            .matching(NSPredicate(format: "label CONTAINS %@", text))
            .count
    }

    // MARK: - 1. the playlists tab

    /// The tab lists what the house has, Favorites first, and a row with a
    /// description shows **the description** rather than its count.
    ///
    /// The row's second line is a three-way `when` on Android
    /// (`PlaylistsTab.kt:114`) and the order is the whole of it: a
    /// description and a count are both true of the same row, and the one
    /// worth the line is the one a person wrote. `Longwater Drive` has both —
    /// a description and three tracks — so `3 tracks` appearing anywhere is
    /// the branch having collapsed to the count.
    @MainActor
    func testMusicMore_PlaylistsTabListsTheHousePlaylists() throws {
        musicMoreReset()
        let (app, landed) = openPlaylistsTab()
        evidence(app, "musicmore-10-playlists-tab")
        XCTAssertFalse(fellBackToLocalMedia(in: app), "the shell gave up on the server")
        XCTAssertTrue(landed, "the playlists tab never drew a row")

        XCTAssertTrue(
            containing(app, MusicMoreUnderTest.favorites).exists,
            "Favorites is not on the tab — the server pins it on top and this does not re-sort"
        )
        XCTAssertTrue(
            containing(app, MusicMoreUnderTest.longwater).exists,
            "the named playlist is not on the tab"
        )
        XCTAssertTrue(
            containing(app, MusicMoreUnderTest.longwaterNote).exists,
            "the row with a description does not show it"
        )
        XCTAssertEqual(
            0, countContaining(app, MusicMoreUnderTest.threeTracks),
            "the row showed its count instead of the description it has"
        )
        // Favorites has no description, so its line IS its own sentence —
        // the second arm of the same `when`, and the one that proves the
        // first assertion is not passing by the count simply being absent.
        XCTAssertTrue(
            containing(app, MusicMoreUnderTest.derivedFromFavorites).exists,
            "the virtual playlist does not say where it comes from"
        )
    }

    /// The badge's glyph is decorative — **nothing says it**, and it is not
    /// clipped.
    ///
    /// ### What this case used to assert, and why it was wrong about the app
    ///
    /// It asserted that the motorway emoji was not an element of the tree at
    /// all, because that is what would silence the `Text clipped` and
    /// `Contrast failed` the audit reads off it. Three trains say SwiftUI
    /// does not offer that: `accessibilityHidden` and
    /// `accessibilityElement(children: .ignore)` on ``MusicPlaylistBadge``
    /// both lost to the merge at each of its three call sites, which
    /// collects the descendants back with their hidden flags disregarded
    /// (S007 listed the glyph as the badge, S010 the same, S012 one level
    /// deeper under the `.ignore` container). An assertion about the tree
    /// was an assertion about SwiftUI, not about this app.
    ///
    /// So this is the same concern, measured where it is decidable:
    ///
    /// * **Nothing reads it out.** The row's merged label is `Longwater
    ///   Drive, songs for the Vantersk road` — the glyph's own name,
    ///   `motorway`, is not in it, which is the whole of what the hidden
    ///   flag is for and what a VoiceOver user gets. Drop the modifier and
    ///   the merge puts `motorway` in the middle of that sentence.
    /// * **It is not clipped.** `Text clipped` claims the text "may be
    ///   clipped at larger Dynamic Type sizes", and the badge's box is
    ///   `@ScaledMetric` — so the box is measured at both type sizes, the
    ///   same way three other named findings on this track turned out to be
    ///   false positives. A fixed `frame(width: 36, height: 36)` would
    ///   measure 36 points at AX5 too, and that is the defect the headline
    ///   describes.
    ///
    /// The contrast half has no measurement and needs none: the glyph is
    /// drawn by the system emoji font in its own colours, which no app can
    /// change, and `musicMoreUnexplained` carries both exclusions pinned to
    /// this one label.
    @MainActor
    func testMusicMore_ThePlaylistEmojiIsDecorative() throws {
        func badgeHeight(_ mode: String, _ name: String) -> CGFloat {
            musicMoreReset()
            let (app, landed) = openPlaylistsTab(mode)
            XCTAssertTrue(landed, "the playlists tab never drew a row at \(mode)")
            let glyph = app.staticTexts[MusicMoreUnderTest.playlistEmoji]
            XCTAssertTrue(
                musicMoreScroll(app, to: glyph),
                "the badge that carries the emoji was never built at \(mode)"
            )
            evidence(app, name)

            // The spoken sentence, on the same screen the box is measured on:
            // the row that merges the badge must not have the glyph in its
            // label.
            let row = app.buttons
                .matching(NSPredicate(format: "label BEGINSWITH %@", MusicMoreUnderTest.longwater))
                .firstMatch
            XCTAssertTrue(row.exists, "the playlist row that carries the badge is not a button")
            XCTAssertFalse(
                row.label.contains(MusicMoreUnderTest.playlistEmoji),
                "the decorative glyph is inside the row's spoken label (\(row.label)) — "
                    + "the badge's accessibilityHidden is gone"
            )

            let height = glyph.frame.height
            print("SCALE [musicmore-badge] \(mode) height=\(height)")
            app.terminate()
            return height
        }

        let ordinary = badgeHeight(Self.ordinaryTypeSize, "musicmore-10B-badge-ordinary")
        let huge = badgeHeight(
            "UICTContentSizeCategoryAccessibilityXXXL",
            "musicmore-10C-badge-ax5"
        )
        XCTAssertGreaterThan(
            huge, ordinary * 1.5,
            "the badge's box did not grow with the type size (\(ordinary) -> \(huge) "
                + "points), so the audit's `Text clipped` against the glyph is real "
                + "after all and `side` is not scaling"
        )
    }

    // MARK: - 2. the playlist sheet

    /// The sheet lists the playlist's tracks **in the server's order**.
    ///
    /// `GET /api/playlists/{id}/tracks` answers a **bare array** in playback
    /// order (`playlists.py:122`), and the seed's order is deliberately not
    /// alphabetical: 501, 503, 502. A client that decoded a wrapper would
    /// show nothing, and one that re-sorted by title would put `Pellucid`
    /// first — so the assertion is on the positions and not just on
    /// presence.
    @MainActor
    func testMusicMore_PlaylistSheetShowsTracksInOrder() throws {
        musicMoreReset()
        let (app, landed) = openPlaylistsTab()
        XCTAssertTrue(landed, "the playlists tab never drew a row")
        XCTAssertTrue(openPlaylistSheet(app, MusicMoreUnderTest.longwater), "the sheet never opened")
        evidence(app, "musicmore-20-playlist-sheet")

        let first = containing(app, MusicMoreUnderTest.veldrassen)
        let last = containing(app, MusicMoreUnderTest.pellucid)
        XCTAssertTrue(first.waitForExistence(timeout: 20), "the track read decoded to nothing")
        XCTAssertTrue(
            containing(app, MusicMoreUnderTest.ashkeep).exists,
            "the middle track is missing"
        )
        XCTAssertTrue(last.exists, "the last track is missing")
        XCTAssertLessThan(
            first.frame.minY, last.frame.minY,
            "the list is not in the server's order — 501 is first on the wire and "
                + "a title sort would put Pellucid there"
        )
    }

    // MARK: - 3. Favorites is not a playlist

    /// Favorites offers **no** edit, no delete and no reorder; the real
    /// playlist offers all three.
    ///
    /// Every one of those routes is refused for id 0 — `DELETE
    /// /api/playlists/0` (`playlists.py:359`), the order PATCH (`:318`) and
    /// the track POST, whose 400 names the library route to use instead
    /// (`:382`). A port that dropped the guard would ship a row of buttons
    /// that fail on every tap.
    ///
    /// **Both halves are asserted**, and that is what makes it discriminate:
    /// a sheet that drew none of the three for anybody would pass the first
    /// half alone.
    @MainActor
    func testMusicMore_FavoritesOffersNoEdit() throws {
        musicMoreReset()
        let (app, landed) = openPlaylistsTab()
        XCTAssertTrue(landed, "the playlists tab never drew a row")

        XCTAssertTrue(openPlaylistSheet(app, MusicMoreUnderTest.favorites), "Favorites never opened")
        evidence(app, "musicmore-30-favorites-sheet")
        XCTAssertFalse(
            app.buttons[MusicMoreUnderTest.edit].exists,
            "Favorites is offered an edit, and PATCH /api/playlists/0 is refused"
        )
        XCTAssertFalse(
            containing(app, MusicMoreUnderTest.deletePlaylist).exists,
            "Favorites is offered a delete, and DELETE /api/playlists/0 is refused"
        )
        XCTAssertEqual(
            0, countContaining(app, MusicMoreUnderTest.moveUp),
            "Favorites is offered a reorder, and its order PATCH is refused"
        )
        closeSheet(app)

        XCTAssertTrue(
            openPlaylistSheet(app, MusicMoreUnderTest.longwater),
            "the real playlist never opened"
        )
        evidence(app, "musicmore-31-real-playlist-sheet")
        XCTAssertTrue(
            app.buttons[MusicMoreUnderTest.edit].exists,
            "the real playlist is offered no edit either, so the first half proved nothing"
        )
        XCTAssertTrue(
            containing(app, MusicMoreUnderTest.deletePlaylist).exists,
            "the real playlist is offered no delete"
        )
        XCTAssertGreaterThan(
            countContaining(app, MusicMoreUnderTest.moveUp), 0,
            "the real playlist is offered no reorder"
        )
    }

    // MARK: - 4. add to playlist

    /// The sheet reads which playlists hold this track, and a tap moves the
    /// one that was tapped.
    ///
    /// Track 503 is in `Longwater Drive` and in nothing else, so one row must
    /// read `in this playlist` and the other its count — which is the
    /// memberships read having been decoded at all (another **bare array**,
    /// `music.py:341`).
    @MainActor
    func testMusicMore_AddToPlaylistTogglesMembership() throws {
        musicMoreReset()
        let (app, landed) = openMusicMore()
        XCTAssertTrue(landed, "Music never drew")
        XCTAssertTrue(openTrackSheet(app, MusicMoreUnderTest.ashkeep), "the track sheet never opened")
        app.buttons[MusicMoreUnderTest.addToPlaylist].tap()
        XCTAssertTrue(
            containing(app, MusicMoreUnderTest.createNewPlaylist).waitForExistence(timeout: 20),
            "the add-to-playlist sheet never opened"
        )
        evidence(app, "musicmore-40-add-sheet")

        let held = app.descendants(matching: .any).matching(
            NSPredicate(
                format: "label CONTAINS %@ AND label CONTAINS %@",
                MusicMoreUnderTest.longwater, MusicMoreUnderTest.inThisPlaylist
            )
        ).firstMatch
        XCTAssertTrue(held.exists, "the playlist that holds this track does not say so")

        let other = app.descendants(matching: .any).matching(
            NSPredicate(
                format: "label CONTAINS %@ AND label CONTAINS %@",
                MusicMoreUnderTest.quietHours, MusicMoreUnderTest.noTracks
            )
        ).firstMatch
        XCTAssertTrue(other.exists, "the empty playlist does not show its count")

        containing(app, MusicMoreUnderTest.quietHours).tap()
        let moved = app.descendants(matching: .any).matching(
            NSPredicate(
                format: "label CONTAINS %@ AND label CONTAINS %@",
                MusicMoreUnderTest.quietHours, MusicMoreUnderTest.inThisPlaylist
            )
        ).firstMatch
        XCTAssertTrue(
            moved.waitForExistence(timeout: 20),
            "the tapped playlist did not become one this track is in"
        )
        evidence(app, "musicmore-41-add-sheet-after")
        // And the other row is untouched: a toggle that moved both would
        // pass the assertion above on its own.
        XCTAssertTrue(held.exists, "the row that was not tapped changed too")
    }

    /// Adding a track that is **already** in a playlist says so, and does not
    /// say it failed.
    ///
    /// The server answers 409 "track N is already in this playlist"
    /// (`playlists.py:438`), which is the state the person asked for.
    /// Android's own bulk add counts those apart from real failures and says
    /// "added 3 tracks, 1 already in" (`MusicScreen.kt:255`); the single add
    /// from `AddToPlaylistSheet.kt:131` has no such branch and reads
    /// `failed: 409 Conflict: …`.
    ///
    /// The 409 is reached the way it happens in a house: the sheet is opened,
    /// **then** somebody else puts the track in, so this phone's memberships
    /// are a second out of date. `musicmore-prime` sends no push, which is
    /// what keeps them stale.
    @MainActor
    func testMusicMore_AddingATrackAlreadyInItSaysSo() throws {
        musicMoreReset()
        let (app, landed) = openMusicMore()
        XCTAssertTrue(landed, "Music never drew")
        XCTAssertTrue(openTrackSheet(app, MusicMoreUnderTest.sablewind), "the track sheet never opened")
        app.buttons[MusicMoreUnderTest.addToPlaylist].tap()
        XCTAssertTrue(
            containing(app, MusicMoreUnderTest.quietHours).waitForExistence(timeout: 20),
            "the add-to-playlist sheet never opened"
        )

        control(
            "musicmore-prime",
            "{\"playlist_id\":42,\"track_id\":505}",
            port: 6601,
            what: "track 505 into Quiet Hours behind the app's back"
        )
        containing(app, MusicMoreUnderTest.quietHours).tap()

        XCTAssertTrue(
            sheetSaid(app, startsWith: MusicMoreUnderTest.alreadyInPrefix),
            "the sheet did not itself report the 409 as the track already being there"
        )
        evidence(app, "musicmore-50-already-in")
        XCTAssertEqual(
            0, countContaining(app, MusicMoreUnderTest.failedWord),
            "a 409 was reported as a failure"
        )
    }

    /// Favorites goes through the **library** route, not the playlist's.
    ///
    /// `POST /api/playlists/0/tracks` is a 400 whose own detail names
    /// `PATCH /api/music/library/{id} {favorited: true}` instead
    /// (`playlists.py:382`), so a port that forgot Favorites is not a
    /// playlist ships a row that fails every time it is tapped.
    ///
    /// Two things are asserted, because either alone could pass wrongly: the
    /// toast is the **favourite's** wording and not a playlist's, and the
    /// row itself becomes one this track is in — which only a 2xx produces.
    @MainActor
    func testMusicMore_FavoritesAddGoesThroughTheLibrary() throws {
        musicMoreReset()
        let (app, landed) = openMusicMore()
        XCTAssertTrue(landed, "Music never drew")
        XCTAssertTrue(openTrackSheet(app, MusicMoreUnderTest.sablewind), "the track sheet never opened")
        app.buttons[MusicMoreUnderTest.addToPlaylist].tap()
        XCTAssertTrue(
            containing(app, MusicMoreUnderTest.favorites).waitForExistence(timeout: 20),
            "the add-to-playlist sheet never opened"
        )

        containing(app, MusicMoreUnderTest.favorites).tap()
        XCTAssertTrue(
            sheetSaid(app, startsWith: MusicMoreUnderTest.favoritedPrefix),
            "the sheet did not say the track was favourited — "
                + "the playlist route 400s for id 0"
        )
        evidence(app, "musicmore-60-favorited")
        XCTAssertEqual(
            0, countContaining(app, MusicMoreUnderTest.failedWord),
            "tapping Favorites reported a failure"
        )
        let moved = app.descendants(matching: .any).matching(
            NSPredicate(
                format: "label CONTAINS %@ AND label CONTAINS %@",
                MusicMoreUnderTest.favorites, MusicMoreUnderTest.inThisPlaylist
            )
        ).firstMatch
        XCTAssertTrue(
            moved.waitForExistence(timeout: 20),
            "the Favorites row did not become one this track is in, so nothing was written"
        )
    }

    /// Creating a playlist from the sheet names it, adds this track to it,
    /// and the tab then lists it.
    ///
    /// Two calls, one gesture (`AddToPlaylistSheet.kt:116`), and the new id
    /// comes **out of the 201's body** — nothing else names it
    /// (`playlists.py:179`). A name the seed does not contain, so the tab
    /// listing it cannot be a seed row read twice.
    @MainActor
    func testMusicMore_CreatePlaylistFromTheSheet() throws {
        musicMoreReset()
        let (app, landed) = openMusicMore()
        XCTAssertTrue(landed, "Music never drew")
        XCTAssertTrue(openTrackSheet(app, MusicMoreUnderTest.sablewind), "the track sheet never opened")
        app.buttons[MusicMoreUnderTest.addToPlaylist].tap()
        let field = app.textFields[MusicMoreUnderTest.playlistNameHint]
        XCTAssertTrue(field.waitForExistence(timeout: 20), "the create field is not on the sheet")

        field.tap()
        clearField(app)
        app.typeText(MusicMoreUnderTest.newPlaylist)
        let create = app.buttons[MusicMoreUnderTest.create]
        XCTAssertTrue(
            create.waitForExistence(timeout: 15),
            "`create` is not offered for a name that has been typed"
        )
        create.tap()

        XCTAssertTrue(
            sheetSaid(app, startsWith: MusicMoreUnderTest.createdPrefix),
            "the sheet did not report the name the create was given"
        )
        evidence(app, "musicmore-70-created")
        // **One** close, not two: the add sheet replaced the track sheet it
        // was reached from rather than being presented over it, because
        // three `.sheet` modifiers on one view is the hazard
        // `AppShellView.swift:54` wrote down (``MusicMoreSheet``).
        closeSheet(app)
        // The library row was reached by scrolling, so the bar is above the
        // fold now.
        musicMoreScrollTop(app)

        XCTAssertTrue(
            musicMoreTapTab(app, MusicMoreUnderTest.tabPlaylists),
            "the playlists tab could not be reached after the create"
        )
        let row = containing(app, MusicMoreUnderTest.newPlaylist)
        XCTAssertTrue(
            row.waitForExistence(timeout: 25),
            "the created playlist is not on the tab, so the 201 was not read back"
        )
        evidence(app, "musicmore-71-created-on-tab")
    }

    // MARK: - 5. cover art

    /// A track with a cover says it has one; a track whose cover route 404s
    /// says it has none.
    ///
    /// The cover is fetched through `MusicCoverStore`, not `AsyncImage` —
    /// which would make its own request outside `DomovoiClient`, escape the
    /// cleartext and trust policies and draw nothing with no explanation. A
    /// store wired that way would leave **both** sheets looking the same,
    /// which is why both halves are asserted here.
    ///
    /// `GET …/502/cover` is a real 404, and `.absent` is the settled answer:
    /// `.loading` and `.failed` deliberately do **not** say "no cover art",
    /// because neither is an answer.
    @MainActor
    func testMusicMore_CoverArtDrawsForATrackThatHasOneAndNotForOneWithout() throws {
        musicMoreReset()
        let (app, landed) = openMusicMore()
        XCTAssertTrue(landed, "Music never drew")

        XCTAssertTrue(openTrackSheet(app, MusicMoreUnderTest.ashkeep), "the track sheet never opened")
        let has = app.descendants(matching: .any)
            .matching(NSPredicate(format: "label == %@", MusicMoreUnderTest.coverArt))
            .firstMatch
        XCTAssertTrue(
            has.waitForExistence(timeout: 25),
            "the track with a cover does not say it has one"
        )
        evidence(app, "musicmore-80-cover-present")
        closeSheet(app)

        XCTAssertTrue(openTrackSheet(app, MusicMoreUnderTest.pellucid), "the second sheet never opened")
        let none = app.descendants(matching: .any)
            .matching(NSPredicate(format: "label == %@", MusicMoreUnderTest.noCoverArt))
            .firstMatch
        XCTAssertTrue(
            none.waitForExistence(timeout: 25),
            "the track whose cover route 404s does not say it has none"
        )
        evidence(app, "musicmore-81-cover-absent")
    }

    // MARK: - 6. lyrics

    /// The room card carries the line the room is **on**, out of the room's
    /// own document.
    ///
    /// The parlour is on track 501, whose document is `synced`; track 502's
    /// is `plain` and shares no word with it. So the synced line being on
    /// screen while the plain text is not is the document having been decoded
    /// **and** `state()` having taken the right branch for the right track.
    @MainActor
    func testMusicMore_LyricsFollowTheRoom() throws {
        musicMoreReset()
        let (app, landed) = openMusicMore()
        XCTAssertTrue(landed, "Music never drew")

        let line = startingWith(app, MusicMoreUnderTest.activeSyncedLine)
        XCTAssertTrue(
            line.waitForExistence(timeout: 30),
            "the playing room's card carries no lyric line"
        )
        evidence(app, "musicmore-90-room-lyric-line")
        XCTAssertEqual(
            0, countContaining(app, MusicMoreUnderTest.plainFirstWords),
            "another track's plain text is on the room card"
        )
        // The line is the one the room's CLOCK is on, not the document's
        // first: the parlour is seeded past the last timed line, so a card
        // that simply drew `lines.first` would show this instead.
        XCTAssertEqual(
            0, countContaining(app, MusicMoreUnderTest.firstSyncedLine),
            "the card shows the document's first line rather than the room's position"
        )
    }

    /// Each lyrics state says the thing that is actually true of it.
    ///
    /// `Lyrics.kt:77`'s `when`, in its order, over three tracks: 502 is
    /// `plain` and its text is shown, 503 is `instrumental`, 504 is `none`
    /// with `checking` set. Collapsing that order is the defect `Lyrics.kt:83`
    /// has a comment for — a synced document whose lines turn out unusable
    /// still has its plain text, and "no lyrics" over a song whose words
    /// arrived is the quiet failure.
    @MainActor
    func testMusicMore_LyricsSayWhatThereIsForEachState() throws {
        musicMoreReset()
        let (app, landed) = openMusicMore()
        XCTAssertTrue(landed, "Music never drew")

        XCTAssertTrue(openTrackSheet(app, MusicMoreUnderTest.pellucid), "502's sheet never opened")
        XCTAssertTrue(
            startingWith(app, MusicMoreUnderTest.plainFirstWords).waitForExistence(timeout: 25),
            "a plain document's text is not shown"
        )
        evidence(app, "musicmore-100-lyrics-plain")
        closeSheet(app)

        XCTAssertTrue(openTrackSheet(app, MusicMoreUnderTest.ashkeep), "503's sheet never opened")
        XCTAssertTrue(
            containing(app, MusicMoreUnderTest.instrumental).waitForExistence(timeout: 25),
            "an instrumental is not said to be one"
        )
        evidence(app, "musicmore-101-lyrics-instrumental")
        // And it is not the plain branch wearing another sentence: 502's
        // words must be gone with 502's sheet.
        XCTAssertEqual(
            0, countContaining(app, MusicMoreUnderTest.plainFirstWords),
            "the previous track's words are still on screen"
        )
        closeSheet(app)

        XCTAssertTrue(openTrackSheet(app, MusicMoreUnderTest.quillon), "504's sheet never opened")
        XCTAssertTrue(
            containing(app, MusicMoreUnderTest.looking).waitForExistence(timeout: 25),
            "a document still being looked for does not say so"
        )
        evidence(app, "musicmore-102-lyrics-looking")
    }

    /// A house that refuses the lyrics read draws **nothing at all** — not
    /// even the section header.
    ///
    /// Track 505's read answers 503 with "lyrics are not set up on this
    /// server yet", which `MusicLyricsStore.isHiddenRefusal` reads as hidden.
    /// A header with a `retry` under it would refuse on every tap, which is
    /// the control the owner's rule forbids and the shape
    /// `LyricsPanel.kt:395` guards.
    ///
    /// The second half opens a track whose read **does** answer, so a sheet
    /// that simply never draws a lyrics header cannot pass this.
    @MainActor
    func testMusicMore_LyricsAreHiddenWhenTheHouseRefuses() throws {
        musicMoreReset()
        let (app, landed) = openMusicMore()
        XCTAssertTrue(landed, "Music never drew")

        XCTAssertTrue(openTrackSheet(app, MusicMoreUnderTest.sablewind), "505's sheet never opened")
        // **Synchronised on the absence, not sampled after a sibling.**
        //
        // Waiting for `add to playlist` said nothing about the 503 having
        // landed: `HomeLink(addToPlaylist)` and the lyrics header are emitted
        // in the same body pass, and until the refusal arrives `result` is
        // nil, `isHidden` is false, and the panel legitimately draws its
        // header over a spinner. So the four `.exists` checks raced one round
        // trip and would fail on a loaded Simulator as though the app had
        // drawn a header over a refused read. The sheet opens with
        // `open: true`, so the header is `hide lyrics`, and its going away is
        // the refusal landing.
        XCTAssertTrue(
            waitUntilGone(app.buttons[MusicMoreUnderTest.hideLyrics], timeout: 25),
            "a refused read left the lyrics header up"
        )
        evidence(app, "musicmore-110-lyrics-refused")
        XCTAssertFalse(
            app.buttons[MusicMoreUnderTest.showLyrics].exists,
            "a refused read drew a lyrics header"
        )
        XCTAssertFalse(
            app.buttons[MusicMoreUnderTest.retry].exists,
            "a refused read offered a retry that would refuse every time"
        )
        // The rest of the sheet is there — only the panel is gone.
        XCTAssertTrue(
            containing(app, MusicMoreUnderTest.addToPlaylist).exists,
            "505's sheet did not draw at all, so the absences prove nothing"
        )
        closeSheet(app)

        XCTAssertTrue(openTrackSheet(app, MusicMoreUnderTest.pellucid), "502's sheet never opened")
        let header = app.buttons[MusicMoreUnderTest.hideLyrics]
        XCTAssertTrue(
            header.waitForExistence(timeout: 25),
            "no sheet draws a lyrics header at all, so the first half proved nothing"
        )
        evidence(app, "musicmore-111-lyrics-header-present")
    }

    /// A track sheet for a song **no room is playing** shows the words and
    /// the source, and **no timing chips**.
    ///
    /// Track 506 is synced and no room plays it, which is the only
    /// combination that reaches the no-room branch. An offset is stored per
    /// room (`MusicLyricsNudgeStore.set` returns without writing for a blank
    /// one), so with no room behind the panel the three chips were three taps
    /// that changed nothing and did not even move the label beside them —
    /// the control the owner's rule forbids, and the panel's own doc comment
    /// claimed it was not drawn.
    ///
    /// The panel having drawn at all is asserted first, so a sheet that
    /// simply failed to show lyrics cannot pass this. The other half — that
    /// the chips **are** offered once there is a room — is
    /// `MusicRoomLyricsModel.maySetTiming` and is asserted in
    /// `MusicMoreModelTests`: reaching it through the UI would mean opening
    /// the library row of the very track the parlour is playing, whose title
    /// is on screen twice and which no case here opens (the header).
    @MainActor
    func testMusicMore_NoRoomNoTimingChips() throws {
        musicMoreReset()
        let (app, landed) = openMusicMore()
        XCTAssertTrue(landed, "Music never drew")

        XCTAssertTrue(openTrackSheet(app, MusicMoreUnderTest.harrowgate), "506's sheet never opened")
        XCTAssertTrue(
            startingWith(app, MusicMoreUnderTest.harrowgateLine).waitForExistence(timeout: 25),
            "506's synced document drew no lines, so there is nothing to assert about its chips"
        )
        evidence(app, "musicmore-115-no-room-no-timing")
        XCTAssertFalse(
            app.buttons[MusicMoreUnderTest.lyricsEarlier].exists,
            "a panel with no room behind it offered a timing chip that writes nowhere"
        )
        XCTAssertFalse(
            app.buttons[MusicMoreUnderTest.resetLyricsTiming].exists,
            "a panel with no room behind it offered `reset` for an offset it cannot store"
        )
        // And it is not the whole footer that is missing: the source label is
        // the panel's own and is drawn beside where the chips would be.
        XCTAssertTrue(
            containing(app, MusicMoreUnderTest.sidecarSource).exists,
            "the panel drew no footer at all, so the absent chips prove nothing"
        )
    }

    /// A room on a library track carries a lyric line; a room playing a
    /// stream carries none.
    ///
    /// The cellar's room-lyrics answer deliberately carries a **document
    /// beside a null `track_id`** — a contradiction no real house would
    /// write and the only shape that tells a kept guard from a dropped one.
    /// `MusicRoomLyricsModel.apply` keeps a doc only when the track is not
    /// nil, and `isHidden` is true for a nil track; drop either and
    /// `musicmoreCellarLine` goes on the streaming room's card.
    @MainActor
    func testMusicMore_RoomCardShowsALineForALibraryTrackAndNoneForAStream() throws {
        musicMoreReset()
        let (app, landed) = openMusicMore()
        XCTAssertTrue(landed, "Music never drew")

        XCTAssertTrue(
            containing(app, MusicMoreUnderTest.stream).waitForExistence(timeout: 30),
            "the streaming room's card is not on screen, so there is nothing to assert about it"
        )
        XCTAssertTrue(
            startingWith(app, MusicMoreUnderTest.activeSyncedLine).waitForExistence(timeout: 30),
            "the library room's card carries no lyric line"
        )
        evidence(app, "musicmore-120-room-cards")
        XCTAssertEqual(
            0, countContaining(app, MusicMoreUnderTest.cellarLine),
            "the streaming room's card carries words for a track it has no id for"
        )
    }

    // MARK: - 7. playing a playlist

    /// `play` goes to the room the chips were showing as picked, not to
    /// whichever room the array happens to start with.
    ///
    /// The seed's first room is `parlour`; this picks `cellar`, so a model
    /// that sent `rooms.first` would name the wrong one
    /// (`PlaylistDrawer.kt:86`).
    @MainActor
    func testMusicMore_PlaylistPlaysInTheRoomTheSheetOffers() throws {
        musicMoreReset()
        let (app, landed) = openPlaylistsTab()
        XCTAssertTrue(landed, "the playlists tab never drew a row")
        XCTAssertTrue(openPlaylistSheet(app, MusicMoreUnderTest.longwater), "the sheet never opened")

        let chip = app.buttons[MusicMoreUnderTest.cellarRoom]
        XCTAssertTrue(chip.waitForExistence(timeout: 20), "the sheet offers no room chips")
        chip.tap()
        app.buttons[MusicMoreUnderTest.play].tap()

        XCTAssertTrue(
            sheetSaid(app, containing: MusicMoreUnderTest.playingInCellar),
            "the play named a room other than the chip that was picked"
        )
        evidence(app, "musicmore-130-played-in-picked-room")
    }

    /// With no room online, `play` and `shuffle all` are **absent**, and the
    /// section label says why.
    ///
    /// `MusicPlaylistDetailModel.play(shuffle:rooms:)` refuses a room the
    /// server never reported, so a button drawn here would have nothing to
    /// send to (`PlaylistDrawer.kt:222`).
    @MainActor
    func testMusicMore_NoRoomsNoPlayButton() throws {
        musicMoreReset()
        // After the reset, because the reset puts the rooms back.
        control(
            "musicmore-rooms", "{\"rooms\":\"empty\"}",
            port: 6601, what: "a house with no room online"
        )
        let (app, landed) = openPlaylistsTab()
        XCTAssertTrue(landed, "the playlists tab never drew a row")
        XCTAssertTrue(openPlaylistSheet(app, MusicMoreUnderTest.longwater), "the sheet never opened")
        evidence(app, "musicmore-140-no-rooms")

        XCTAssertTrue(
            containing(app, MusicMoreUnderTest.noRoomsOnlineLabel).exists,
            "the section does not say the room list is empty"
        )
        XCTAssertFalse(
            app.buttons[MusicMoreUnderTest.play].exists,
            "`play` is drawn with nowhere to send it"
        )
        XCTAssertFalse(
            app.buttons[MusicMoreUnderTest.shuffleAll].exists,
            "`shuffle all` is drawn with nowhere to send it"
        )
        // The tracks are still listed: the sheet is not broken, only the two
        // controls are gone.
        XCTAssertTrue(
            containing(app, MusicMoreUnderTest.veldrassen).waitForExistence(timeout: 20),
            "the sheet stopped listing its tracks as well"
        )
        // Put it back for whatever case runs next in this shared house.
        control("musicmore-rooms", "{}", port: 6601, what: "the rooms back")
    }

    // MARK: - 8. the tab that is not offered

    /// The tab bar does not offer `player`, and does offer `playlists`.
    ///
    /// Nothing in this app conforms to `MusicMorePlayerEngine`, so
    /// `MusicTab.shown(hasPlayer:)` leaves that tab out entirely — not
    /// greyed, not empty. A bar built from `MusicTab.allCases` would offer
    /// it, and `MusicPlayerTabView` behind it would draw a transport row over
    /// nothing.
    ///
    /// **Delete this case when the engine lands.** It is an assertion about
    /// an absence that is deliberate today and will be wrong the day
    /// `use(_:)` is called with a real engine.
    @MainActor
    func testMusicMore_ThePlayerTabIsNotOfferedWithoutAnEngine() throws {
        musicMoreReset()
        let (app, landed) = openMusicMore()
        evidence(app, "musicmore-150-tab-bar")
        XCTAssertTrue(landed, "Music never drew")

        XCTAssertTrue(
            startingWith(app, MusicMoreUnderTest.tabPlaylists).exists,
            "the playlists tab is not offered, so this says nothing about the player tab"
        )
        XCTAssertFalse(
            app.buttons[MusicMoreUnderTest.tabPlayer].exists,
            "the tab bar offers a player tab with no engine behind it"
        )
    }

    // MARK: - 9. Dynamic Type

    /// The playlists tab's own text grows with the type size.
    ///
    /// **Measured, not read off a headline.** A named Dynamic Type audit
    /// finding has twice been a false positive on this track, so this
    /// compares one row's height at `ordinaryTypeSize` against
    /// `AccessibilityXXXL`. A fixed `frame(width:)` on a glyph, a
    /// `minimumScaleFactor`, or a row that should become a column all show up
    /// as a height that did not move.
    ///
    /// The **cover** is expected not to grow and is not measured: it is a
    /// picture, not a glyph, and a 40-point thumbnail scaled 3.6x would push
    /// the title it belongs to off the row. `MusicCoverView`'s doc comment is
    /// where that is argued.
    @MainActor
    func testMusicMore_PlaylistsSurviveAnAccessibilityTextSize() throws {
        func rowHeight(_ mode: String, _ name: String) throws -> CGFloat {
            musicMoreReset()
            let (app, landed) = openPlaylistsTab(mode)
            XCTAssertTrue(landed, "the playlists tab never drew a row at \(mode)")
            let row = containing(app, MusicMoreUnderTest.longwaterNote)
            XCTAssertTrue(row.waitForExistence(timeout: 25), "no row to measure at \(mode)")
            evidence(app, name)
            let height = row.frame.height
            app.terminate()
            return height
        }

        let ordinary = try rowHeight(Self.ordinaryTypeSize, "musicmore-160-playlists-ordinary")
        let huge = try rowHeight(
            "UICTContentSizeCategoryAccessibilityXXXL",
            "musicmore-161-playlists-ax5"
        )

        XCTAssertGreaterThan(
            huge, ordinary * 1.5,
            "the playlist row's text did not grow with the type size "
                + "(\(ordinary) -> \(huge) points)"
        )
    }

    /// **The measurement the audit's Dynamic Type finding claims to be
    /// about** — the tab strip's own four words.
    ///
    /// Train S007's audit named all four tab buttons: "User will not be able
    /// to change the font size of this element", with the element attached.
    /// They are the first named Dynamic Type findings this item has seen, and
    /// this track has now had three: `favorites only`
    /// (``knownScalingFalsePositive``) and the five leaves
    /// ``testTheVideosGridTextGrowsAtAX5`` measures were both false. The
    /// shape is the same here — a `Text` with `.fixedSize(horizontal: false,
    /// vertical: true)` inside a `Button` that merges it, so the audit has no
    /// font to read on the element it can see — but a belief is not a
    /// measurement, so this is the measurement.
    ///
    /// **Why the assertion is "bigger", and not "half again bigger" like the
    /// playlist row's.** All four buttons measure exactly 44.0 at the
    /// ordinary size, and 44 is ``MusicTabBar``'s `minHeight` floor, not the
    /// text's height — a `.subheadline` leaf there is about 18 points. So a
    /// ratio would be a ratio against the floor: `.subheadline` at AX5 is
    /// around 49 points, which is the control growing by every point it has
    /// and still only 1.1x of 44. What discriminates is the floor itself —
    /// a strip whose font could not change would measure 44.0 at **both**
    /// sizes, because the floor is the only thing holding it up.
    @MainActor
    func testMusicMore_TheTabStripTextGrowsAtAX5() throws {
        func heights(_ mode: String) -> [String: CGFloat] {
            let (app, landed) = openMusicMore(mode)
            XCTAssertTrue(landed, "the tab strip never drew at \(mode)")
            var out: [String: CGFloat] = [:]
            for word in MusicMoreUnderTest.tabWords {
                let button = app.buttons
                    .matching(NSPredicate(format: "label BEGINSWITH %@", word))
                    .firstMatch
                if button.exists { out[word] = button.frame.height }
            }
            evidence(app, "musicmore-16A-tabstrip-\(mode == Self.ordinaryTypeSize ? "large" : "ax5")")
            app.terminate()
            return out
        }

        musicMoreReset()
        let ordinary = heights(Self.ordinaryTypeSize)
        let huge = heights("UICTContentSizeCategoryAccessibilityXXXL")
        for word in MusicMoreUnderTest.tabWords {
            // Printed whatever the verdict: a run where the numbers are
            // wanted is a run where the case already failed, and a log that
            // only prints on failure prints nothing the day the case is
            // right for the wrong reason.
            let large = ordinary[word].map { "\($0)" } ?? "not on screen"
            let ax5 = huge[word].map { "\($0)" } ?? "not on screen"
            print("SCALE [musicmore-tab] \(word) large=\(large) ax5=\(ax5)")
        }

        // Every one of the four, measured on both sides: a strip where one
        // word is pinned and three grow is still a defect, and an average
        // would hide it.
        for word in MusicMoreUnderTest.tabWords {
            guard let small = ordinary[word] else {
                XCTFail("the '\(word)' tab was not on screen at the ordinary size")
                continue
            }
            guard let big = huge[word] else {
                XCTFail("the '\(word)' tab was not on screen at AX5")
                continue
            }
            // Two points of margin, not none: the measurement comes back in
            // thirds of a point and a strict `>` on two equal 44.0s is a
            // comparison of floats.
            XCTAssertGreaterThan(
                big, small + 2,
                "the '\(word)' tab did not grow with the type size "
                    + "(\(small) -> \(big) points) — it is still on its 44-point "
                    + "floor at AX5, so the audit's Dynamic Type finding "
                    + "against it is real after all"
            )
        }
    }

    // MARK: - 10. the audit

    /// Apple's own accessibility audit over the playlists tab and the sheet
    /// it opens.
    ///
    /// Classified **by signature**, never by a pinned total: a floor that is
    /// a number goes stale the first time a row is added, and
    /// `floorAndRest(_:)` is where the known anonymous findings live. `rest`
    /// is what this case is about — a contrast failure on the new ink, an
    /// icon-only control with no `accessibilityLabel`, or a sheet that is not
    /// modal (which the audit reports as a finding naming an element
    /// *behind* the sheet).
    @MainActor
    func testMusicMore_AuditThePlaylistsTab() throws {
        guard #available(iOS 17.0, *) else {
            throw XCTSkip("performAccessibilityAudit needs iOS 17")
        }
        musicMoreReset()
        let (app, landed) = openPlaylistsTab()
        XCTAssertTrue(landed, "the playlists tab never drew a row")

        let tab = try auditIssues(app, "musicmore-playlists-tab")
        let onTab = Self.floorAndRest(tab)
        evidence(app, "musicmore-170-audit-tab")
        let tabLeft = musicMoreUnexplained(onTab.rest, in: app)
        XCTAssertEqual(
            [], tabLeft,
            "the playlists tab has findings beyond the known floor of \(onTab.floor) "
                + "that neither a measurement nor the viewport edge explains: \(tabLeft)"
        )

        XCTAssertTrue(openPlaylistSheet(app, MusicMoreUnderTest.longwater), "the sheet never opened")
        let sheet = try auditIssues(app, "musicmore-playlist-sheet")
        let onSheet = Self.floorAndRest(sheet)
        evidence(app, "musicmore-171-audit-sheet")
        let sheetLeft = musicMoreUnexplained(onSheet.rest, in: app)
        XCTAssertEqual(
            [], sheetLeft,
            "the playlist sheet has findings beyond the known floor of \(onSheet.floor) "
                + "that neither a measurement nor the viewport edge explains: \(sheetLeft)"
        )
    }

    /// The named findings this item has an **answer** for, taken out — and
    /// everything else left in.
    ///
    /// Four exclusions, each with its reason, and none of them "the audit is
    /// usually wrong". The shape is `testTheVideosGridAudits`'s, which is
    /// where both of the first two were settled first.
    ///
    /// Anything else — a contrast failure on a card that is fully on screen,
    /// an icon-only control with no label, a finding naming an element
    /// *behind* the sheet (which is how a non-modal sheet reports) — is
    /// returned and fails the case.
    @MainActor
    func musicMoreUnexplained(_ rest: [String], in app: XCUIApplication) -> [String] {
        // The outer scroll view: the tallest on screen. The others are the
        // tab strip, lying on its side, and the phone bar. Kept as the
        // **element**, not just its frame: rule 2 needs to know what is
        // inside it, and a rectangle cannot say.
        let page = app.scrollViews.allElementsBoundByIndex
            .max(by: { $0.frame.height < $1.frame.height })
        let pageBox = page?.frame ?? .null
        // Read **once**, before the filter: one snapshot of the page's
        // subtree, not one per finding.
        let pageContent = page?.descendants(matching: .any)
            .allElementsBoundByIndex.map(\.frame) ?? []

        return rest.filter { line in
            // 1. A Dynamic Type headline **with** an element. The four tab
            //    buttons arrive this way and
            //    ``testMusicMore_TheTabStripTextGrowsAtAX5`` measures those
            //    very four at both sizes, which is the test; this is only
            //    the classification that makes room for it. Nothing is
            //    excluded here on a belief — if the strip ever does stop
            //    scaling, the measurement fails and this line is why the
            //    audit stayed quiet, not what kept it quiet.
            if Self.knownAnonymousHeadlines.contains(where: { line.hasPrefix($0) }) {
                return false
            }
            // 2. A contrast finding against an element of the page's own
            //    content that the page's clip is cutting — or has carried
            //    **wholly** past. Pixels the element does not have are
            //    pixels the audit read off whatever is drawn there instead.
            //
            //    S007's was the cut half: `playing in parlour` at y
            //    184.3–198.7 against a content area starting at 197.3 — 1.3
            //    points of it visible — while `playing in cellar`, the same
            //    string in the same colour on the same card two rooms down
            //    and fully on screen, passed.
            //
            //    **I001 is why "cutting" is no longer enough.** The merge
            //    put ``PhoneSourceSwitchView``'s pinned pill above this page
            //    (`RouteScreen.swift:74`), moving the content area's top
            //    from about 149 to 197.3 while the scroll distance to the
            //    playlists tab is unchanged. At the position this case
            //    audits from, `Veldrassen Overture` sits at
            //    `(100.0, 148.67, 140.0, 18.0)` — 30 points **above** the
            //    clip, not across it, so `intersects` was false and the row
            //    that used to straddle the edge now fails to be forgiven by
            //    the rule written for it. The screen behind those pixels is
            //    the pill, at `{{15.5, 144.8}, {362.0, 45.0}}`: the audit
            //    measured the pill's colours and named the room card's
            //    title. The control that says so is one card down —
            //    `Vantersk Night Radio`, the same `Text(song.displayTitle)`
            //    of the same `identity(_:)` in the same unstyled primary
            //    ink (`MusicRoomCards.swift:109`), fully on screen, and
            //    passed.
            //
            //    The condition is therefore "not wholly inside the page",
            //    and the discriminator that keeps that from forgiving the
            //    pill itself — which sits in the very same band — is
            //    ``musicMoreIsPageContent(_:_:)``.
            //
            //    Measured on the **vertical** axis alone, which is the axis
            //    this page clips on: it does not scroll sideways, so a row
            //    whose frame ends a third of a point outside the page's left
            //    or right edge is a rounding difference and not a row with no
            //    pixels. Two-axis `contains` would forgive it.
            if line.hasPrefix("Contrast failed"), let box = Self.frame(in: line),
               !pageBox.isNull,
               !self.musicMoreInside(box, pageBox, vertical: true),
               Self.musicMoreIsPageContent(box, pageContent) {
                return false
            }
            // 3. The phone-source segmented control, which is the PhoneMedia
            //    item's (`PhoneSourceSwitchView.swift:171`) and not this
            //    one's to edit — Apple reads its label `127.0.0.1:6601` as
            //    not human-readable, and it is right. Recorded in
            //    `overnight/musicmore/DECISIONS.md` under "Needs a shared
            //    change" rather than silently fixed in another item's file.
            //    Pinned to that one control by its identifier: a second
            //    unreadable label anywhere on Music still fails this case.
            if line.hasPrefix("Label not human-readable"),
               line.contains("id='\(MusicMoreUnderTest.phoneSourceServer)'") {
                return false
            }
            // 4. The two findings Apple reads off a playlist's **cover
            //    emoji**, and only off that one label
            //    (``MusicPlaylistBadge``). Neither has an answer in the
            //    drawing, and three trains tried to answer them in the
            //    accessibility tree instead before S012 showed that a merge
            //    at the call site puts the glyph back whatever the badge
            //    says about itself:
            //
            //    * `Text clipped` — "may be clipped at larger Dynamic Type
            //      sizes". The box is `@ScaledMetric` and
            //      ``testMusicMore_ThePlaylistEmojiIsDecorative`` measures
            //      it at both sizes, 36 points to about 94. The audit has
            //      only the `frame(width:height:)` to look at and cannot
            //      see what the number in it is.
            //    * `Contrast failed` — an emoji is drawn by the system
            //      emoji font in its own colours. There is no foreground
            //      style to change and no app can pass this.
            //
            //    Pinned to the emoji's own label, so a contrast or clipping
            //    finding against any other element on either screen still
            //    fails this case.
            if line.hasPrefix("Text clipped") || line.hasPrefix("Contrast failed"),
               line.contains("label='\(MusicMoreUnderTest.playlistEmoji)'") {
                return false
            }
            return true
        }
    }

    /// Whether the element a finding names at `box` belongs to the page's
    /// own scrolling content.
    ///
    /// **The discriminator geometry alone cannot give.** After the merge the
    /// phone-source pill occupies `{{15.5, 144.8}, {362.0, 45.0}}` and a room
    /// card's title carried off the top of the page occupies
    /// `(100.0, 148.67, 140.0, 18.0)` — the same band, both outside the
    /// page's clip. Forgiving a contrast finding by position would forgive
    /// the pill too, and the pill is a drawn, visible control whose colours
    /// are a fair thing for Apple to fail. The row that scrolled away is
    /// still a **descendant** of the page; the pill is its sibling
    /// (``PhoneSourceSwitchView``'s `VStack`, `PhoneSourceSwitchView.swift:80`).
    ///
    /// Matched **by frame, not by label**: the label is the only thing the
    /// finding gives and it is also the thing a screen can repeat, while two
    /// elements of one snapshot cannot share a rectangle. A third of a point
    /// of slack, because that is the resolution the frames come back in.
    static func musicMoreIsPageContent(_ box: CGRect, _ content: [CGRect]) -> Bool {
        content.contains { other in
            abs(other.minX - box.minX) < 0.5
                && abs(other.minY - box.minY) < 0.5
                && abs(other.width - box.width) < 0.5
                && abs(other.height - box.height) < 0.5
        }
    }
}

/// What the stub house's music half holds, by the words that appear on
/// screen.
///
/// Spelt out here rather than imported from `DomovoiKit`, like every other
/// string this file matches on: a query that silently followed a renamed
/// constant would stop testing what it claims to. These are the stub's own
/// (`design-notes/ios/stubhouse/fixtures/musicmore.py`) and the app's
/// pinned wordings, written out.
enum MusicMoreUnderTest {

    // the tabs
    static let tabPlaylists = "playlists"
    static let tabPlayer = "player"

    /// The four words the strip offers with no engine registered, each one
    /// the **start** of a button's label: two of them carry a count
    /// (`library · 6`, `playlists · 3`) and the count moves with the
    /// fixtures, so a whole label would be a second thing to keep true.
    static let tabWords = ["library", "room queue", "playlists", "stats"]

    /// The phone-source switch's server segment, by identifier
    /// (`PhoneSourceSwitchView.swift:171`). Not this item's control; it is
    /// here because it is on Music's screen and so in Music's audit, and an
    /// identifier is the only stable handle on it — its label is the house's
    /// address.
    static let phoneSourceServer = "phone-source-server"

    // the playlists
    static let favorites = "Favorites"
    static let longwater = "Longwater Drive"
    static let longwaterNote = "songs for the Vantersk road"
    /// Longwater Drive's `cover_emoji` (`fixtures/musicmore.py:269`) — the
    /// one glyph on this screen that nothing may read out, and the label the
    /// audit's two drawing findings are classified against
    /// (``musicMoreUnexplained``).
    static let playlistEmoji = "\u{1F6E3}"
    static let quietHours = "Quiet Hours"
    static let derivedFromFavorites = "derived from your favorites"
    /// Longwater Drive's count — the line it must **not** show, because it
    /// has a description.
    static let threeTracks = "3 tracks"
    static let noTracks = "0 tracks"
    static let inThisPlaylist = "in this playlist"
    static let newPlaylist = "Grindlewood Nights"

    // the tracks
    static let veldrassen = "Veldrassen Overture"
    static let pellucid = "Pellucid Marrow"
    static let ashkeep = "Thrumming Ashkeep"
    static let quillon = "Quillon Drift"
    static let sablewind = "Sablewind Figure"
    static let stream = "Vantersk Night Radio"

    // the rooms
    static let cellarRoom = "cellar"
    static let playingInCellar = "in cellar"

    // the lyrics
    /// The parlour's **permanently active** line.
    ///
    /// Not the first line. The room is seeded past the last timed line
    /// (26 s) at 30 s, because a room's position runs on at wall-clock rate:
    /// any seed inside the line list makes "which line is showing" a question
    /// about how long the Simulator took, and the active line would move on
    /// twice inside a 30 s wait.
    static let activeSyncedLine = "Down past the hollow tide"
    /// 501's FIRST line, which at the seeded position must **not** be the one
    /// the card shows — the half of the assertion that discriminates.
    static let firstSyncedLine = "Hold the greenlit awning"
    /// 506: synced lyrics, no room playing it.
    static let harrowgate = "Harrowgate Lantern"
    static let harrowgateLine = "Lanterns down the harrowgate"
    static let lyricsEarlier = "lyrics earlier"
    static let resetLyricsTiming = "reset lyrics timing"
    /// 501's and 506's `source_label`, which the panel's footer draws.
    static let sidecarSource = "lyric file beside the song"
    static let plainFirstWords = "Pellucid marrow, bleach the rafters"
    /// The whole phrase, not the first word: `instrumental break` is a gap
    /// line's own label elsewhere in the panel.
    static let instrumental = "no words to show"
    static let looking = "looking for lyrics"
    static let showLyrics = "show lyrics"
    static let hideLyrics = "hide lyrics"
    static let retry = "retry"
    /// The words the cellar's contradictory document carries. Their presence
    /// **anywhere** is the failure.
    static let cellarLine = "Cellar words nobody should see"

    // the covers
    static let coverArt = "cover art"
    static let noCoverArt = "no cover art"

    // the controls
    static let close = "close"
    static let edit = "edit"
    static let deletePlaylist = "delete playlist"
    static let moveUp = "move up"
    static let play = "play"
    static let shuffleAll = "shuffle all"
    static let noRoomsOnlineLabel = "play in room · no rooms online"
    static let addToPlaylist = "add to playlist"
    static let createNewPlaylist = "create new playlist"
    static let playlistNameHint = "playlist name…"
    static let create = "create"
    /// What a sheet says about its own write, **inside** the sheet
    /// (`MusicMoreSheetStatus.identifier`). Asserted by identifier rather
    /// than by a label query, because a label query reaches elements behind
    /// the sheet and that is exactly the bug this replaced.
    static let sheetStatus = "musicmore-sheet-status"

    // the sentences a toast says, as prefixes
    //
    // A query string over 128 characters throws (train S001), and these are
    // matched with `startingWith`, so each is the leading phrase rather than
    // the whole sentence.
    static let alreadyInPrefix = "\"Sablewind Figure\" is already in"
    static let favoritedPrefix = "favorited \"Sablewind Figure\""
    static let createdPrefix = "created Grindlewood Nights"
    /// The word a 409 or a favourite must **not** produce.
    static let failedWord = "failed"
}
