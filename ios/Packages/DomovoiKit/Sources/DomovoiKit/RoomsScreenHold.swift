import Foundation

/// The pushed room screen's hold on ``RoomsModel``, for as long as that
/// screen **exists** rather than for as long as it is uncovered.
///
/// ### The bug this type is
///
/// Review round 4's must. ``RoomsRoomScreen`` drove all four of
/// ``RoomsModel/open(_:)``, ``RoomsModel/hold()``, ``RoomsModel/release()``
/// and ``RoomsModel/close()`` from one `onAppear`/`onDisappear` pair, and
/// that pair does not mean what it reads as: a covered view goes on receiving
/// it while it is still mounted and still drawing. That is the whole reason
/// ``RoomsModel/holders`` is a count. The room screen presents a sheet of its
/// own — ``RoomsDropInRow``'s — so the cover is not hypothetical, and while
/// that sheet was up:
///
/// * `release()` took the count to **zero**, because the room screen is the
///   only holder while it is up (``RoomsView`` released when the room was
///   pushed), and its deferral is one turn of the main actor rather than one
///   that waits for the matching `onAppear`. ``RoomsModel/stop()`` then ran
///   and `stop()` calls ``DropInCallModel/abandon()``: the live call was hung
///   up under the sheet that said it was live.
/// * `close()` cleared ``RoomsModel/openRoomID``, so the uncover's `onAppear`
///   found `open(_:)` no longer a no-op and put the tab strip back to
///   `overview`.
///
/// ### Why a `deinit` is the right line, and the only one
///
/// A cover and a destruction deliver the **same** callback. The one thing
/// that tells them apart is whether the view's `@State` storage survives: a
/// covered screen keeps it, a popped one does not. So the hold is an object
/// in that storage, and the release is its `deinit`.
///
/// That is also where Android draws the line. `PhoneDropinRow` remembers its
/// `DropinCallClient` across the dialog going up (`PhoneDropinCall.kt:68`) —
/// a Compose `Dialog` does not leave the composition that remembers it — and
/// only `onDispose` ends the call (`:124-127`), which runs when the detail
/// screen itself leaves. A call survives the dialog and dies with the screen,
/// on both phones.
///
/// ### What a `deinit` costs, said plainly
///
/// ``PlayerMicArbiter``'s doc comment refuses a `deinit`-based release, and
/// the reason is good: a lease freed at the collector's convenience frees a
/// **microphone** late. It is weighed differently here, in one direction
/// only. Late means the call outlives the screen by the length of a release
/// rather than ending while somebody is in it, and on the one path that
/// matters it cannot be reached at all — the sheet is modal, so the screen
/// cannot be popped while a call is live, and dismissing the sheet hangs up
/// (``RoomsDropInRow``'s `onDismiss`). What the old shape did was the
/// opposite and unconditional.
///
/// If a future SwiftUI keeps a popped destination's state alive, the model
/// stops late rather than never: leaving Rooms destroys ``RoomsView``, whose
/// `@State` is the model itself.
public final class RoomsScreenHold {

    /// The release, as a closure, because a `deinit` runs outside the main
    /// actor and so cannot reach anything isolated to it.
    ///
    /// The hop is what makes that legal — the same shape
    /// ``PlayerModel``'s microphone wiring uses — and it is why
    /// ``RoomsModel/roomScreenGone(_:)`` is guarded on the room's id: by the
    /// time this runs, another room's screen may already have opened.
    private let letGo: @Sendable () -> Void

    /// The room this hold is for, so a failure can print it.
    public let roomID: String

    /// Open the room and hold the model up, as one act.
    ///
    /// Both halves are here and not in the view's `onAppear` so that there is
    /// exactly one place that pairs with the `deinit`.
    /// ``RoomsModel/open(_:)`` is idempotent, so a repeated `onAppear` that
    /// finds the hold already made does nothing at all — the view's whole
    /// guard is `hold == nil`.
    @MainActor
    public init(_ model: RoomsModel, roomID: String) {
        self.roomID = roomID
        model.open(roomID)
        model.hold()
        letGo = { Task { @MainActor in model.roomScreenGone(roomID) } }
    }

    deinit { letGo() }
}
