import Foundation

/// The manual route's hold on ``ManualModel``: its reads run for as long as
/// the screen **exists**, not for as long as it is uncovered.
///
/// ### The bug this type is
///
/// Answer 32 (`fx-manual32`) made a level of the manual a **pushed screen**
/// on the shell's own stack (``ManualLevelStack``). ``ManualView`` carried
/// `onDisappear { manual?.stop() }` from the days when the manual pushed
/// nothing, so that line used to mean "the route was popped" — and the moment
/// a level went on top of it, it meant "a screen covered me" as well. A
/// pushed destination delivers `onDisappear` to the screen it covers while
/// that screen stays mounted and keeps drawing; this app has measured that in
/// a dozen files (`FilesView.swift:107`, `HomeView.swift:131`,
/// `RoomsDetailView.swift:187` — "that can tell a cover from a destruction;
/// `onDisappear` cannot" — and ten more).
///
/// ``ManualModel/stop()`` is not a cheap thing to call by accident. It
/// cancels the document load and the `heard` walk, stops all six reads —
/// rooms, handlers, plugins, version, people, tracks — and takes the model
/// off ``AppContainer/onServerChange(_:)``. And those reads feed the screens
/// now mounted **above** the one that stopped them: a piece's page is depth 3
/// and ``ManualLiveSlotView`` is what draws `rooms`, `plugins`, `handlers`
/// and `version` on it. So opening the map used to cancel a read still in
/// flight, nothing retried it for the rest of the visit, and the live slots
/// two screens up drew empty. Losing the server-change sink is the worse
/// half: it re-opens the read-after-await class this track has paid for
/// twice, by a teardown rather than by an `await`.
///
/// Nothing could see it. Against the stub house the six reads land during the
/// dwell on Start here before any case opens a level, and ``ApiRead/stop()``
/// keeps the `data` it already has — it cancels the task and drops the sink.
/// Every one of the manual's UI cases passed over it. Review round 1 of
/// answer 32 found it by reading.
///
/// ### Why a `deinit` is the right line, and the only one
///
/// ``RoomsScreenHold``'s reason, word for word: a cover and a destruction
/// deliver the **same** callback, and the one thing that tells them apart is
/// whether the view's `@State` storage survives — a covered screen keeps it,
/// a popped one does not. So the lifetime is an object in that storage and
/// the teardown is its `deinit`.
///
/// It is also what pairs the two halves. ``ManualModel/start()`` is called
/// here and not from the view's `onAppear`, so there is exactly one `start()`
/// per screen and exactly one `stop()`, and an uncover — which is an
/// `onAppear` the view cannot tell from the first one — adds neither. That
/// matters more here than a tidier pair: `start()` calls
/// ``ManualModel/refreshLive()``, so an `onAppear`-driven start would re-fetch
/// six routes every time somebody came back down to Start here from the map.
///
/// ### What a `deinit` costs, said plainly
///
/// The reads stop one main-actor hop after the screen goes, rather than
/// inside the callback. They are six GETs of a server this app is connected
/// to anyway, and `stop()` is idempotent, so late costs one round trip at
/// worst — where the line it replaces cancelled reads that were still being
/// read. If a future SwiftUI keeps a popped destination's `@State` alive, the
/// model stops late rather than never: leaving the app's workspace destroys
/// the whole stack, and ``ManualModel`` holds nothing but its own tasks.
public final class ManualScreenHold {

    /// The teardown, as a closure, because a `deinit` runs outside the main
    /// actor and so cannot reach anything isolated to it. The hop is what
    /// makes it legal — ``RoomsScreenHold``'s shape exactly.
    private let letGo: @Sendable () -> Void

    /// Start the manual's reads and hold them up, as one act.
    @MainActor
    public init(_ model: ManualModel) {
        model.start()
        letGo = { Task { @MainActor in model.stop() } }
    }

    deinit { letGo() }
}
