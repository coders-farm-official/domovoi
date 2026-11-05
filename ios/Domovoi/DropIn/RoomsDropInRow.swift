import AVFoundation
import DomovoiKit
import SwiftUI

/// "Drop in from this phone" — the control, and the call it opens.
///
/// A port of `PhoneDropinRow` (`PhoneDropinCall.kt:61-108`): one button under
/// the drop-in section, and a presentation while the call is on. Kamron,
/// answer 54: "yes: phone-to-room drop-in is an iPhone feature; build it
/// (shared audio layer with the chat mic and room mode)."
///
/// ### The model is the screen's, not this row's
///
/// Android's row holds the client in a `remember` and tears it down in the
/// dialog's `onDispose`. Here the call lives on ``RoomsModel``
/// (``RoomsModel/call``) under this screen's standing rule: a view that holds
/// the request loses it when the view goes away, and a tab change or a scroll
/// is enough to take this row away while a call is live. ``RoomsModel/stop()``
/// ends the call when the screen itself goes, which is Android's safety net
/// ("tear the call down if this UI ever leaves composition",
/// `PhoneDropinCall.kt:124-127`) one layer up.
///
/// What the model cannot save is `showing`, which is this view's: the row
/// losing its **identity** takes the sheet down with it, and on this screen
/// the one state change that happens under a live call is the roster
/// reporting the call. That is why ``RoomsOverviewView`` draws this row once,
/// outside its state switch, and the whole argument is in that property's
/// doc comment. A tab change does take the sheet away, and that is Android
/// too — its dialog's `onDispose` hangs up for the same reason.
///
/// ### What is not here, and why there is no control for it
///
/// * **No `start` for a call between two rooms.** Kamron, answers 5 and 53:
///   "phones never create room-to-room drop-ins; a phone can only drop into a
///   room." ``RoomsStrings/dropInStartIsOnTheDashboard`` says where that one
///   is done.
/// * **No `hang up` of somebody else's call here.** That is the section
///   above, and it is the room's own control
///   (``RoomsModel/hangUp(_:)``) — a different route and a different thing.
/// * **No choice of microphone or speaker.** ``DropInAudio`` says why.
struct RoomsDropInRow: View {

    let shell: ShellModel
    let model: RoomsModel
    let room: RoomsSatellite

    /// Whether the call presentation is up.
    ///
    /// Android keeps `showCall` in the row (`:68`) and that is right here
    /// too: it is which *view* is showing, not what the call is doing, and
    /// the row going away takes the presentation with it in both apps.
    @State private var showing = false

    private var call: DropInCallModel? { model.call }

    /// A call this phone is in, with any room (``DropInCallModel/State/isBusy``).
    private var busy: Bool { call?.state.isBusy ?? false }

    /// Whether this button can do what it says —
    /// ``roomsPhoneDropInEnabled(_:callIsBusy:)``, which is Android's own
    /// enable term plus this one.
    ///
    /// **It is not `!busy` on its own, which review round 1 found.** That
    /// reads only *this phone's* call, and this row is drawn for every state
    /// of the room (``RoomsOverviewView``'s drop-in note: once, outside the
    /// switch, as Android composes it outside its `when`) — so a room already
    /// in a drop-in with another peer offered a live button whose every press
    /// can only get `target_busy` (`domovoi/phone_dropin.py:78-79`). The rule
    /// and the whole argument for it are in DomovoiKit, where a case can
    /// reach them.
    private var canDropIn: Bool { roomsPhoneDropInEnabled(room, callIsBusy: busy) }

    var body: some View {
        Button {
            showing = true
            open()
        } label: {
            Label(DropInStrings.dropInFromThisPhone, systemImage: "phone.bubble")
                .font(.subheadline)
                .fixedSize(horizontal: false, vertical: true)
                // **Not `.buttonStyle(.bordered)`, measured.** That style
                // draws the label in the tint *and* the fill in the tint at
                // low opacity, and train I005's audit failed this button by
                // name: `Contrast nearly passed … label='drop in from this
                // phone'` (`testRooms_DropInFromThisPhoneIsHeardAndHungUp
                // .log:537-544`). The picture settles what the frame cannot:
                // in `att/…/rooms-27-dropin-ended….png` the control is drawn
                // whole, nothing over it and nothing clipping its glyphs, so
                // the sample is of its colours — `#975800` on `#ECE1D1`,
                // **4.38:1**, under the 4.5:1 body text needs. (``Color
                // /brandInk`` itself is 5.66:1 on this page's white canvas,
                // measured off the same pixels: the fill is what loses it.)
                //
                // Android draws an `OutlinedButton` (`PhoneDropinCall.kt:82`)
                // — a tinted label on the surface with an outline, no fill —
                // so the fill was an iOS-only divergence in the first place,
                // and the port keeps the label on the page's own canvas. The
                // Calendar item reached the same place from train S002's four
                // findings against the same pairing
                // (`CalendarScreenView.swift:183-192`).
                //
                // `.plain` draws no disabled state of its own, which is why
                // the ink and the outline are chosen by ``canDropIn`` here: a
                // control that cannot do what it says must not look live
                // (NIGHT-RULES §3), and this one is disabled in three real
                // room states.
                .foregroundStyle(canDropIn ? Color.brandInk : Color.fgMuted)
                .padding(.horizontal, 12)
                .frame(minHeight: 44)
                // The shape `.bordered` drew, kept: a continuous rounded
                // rectangle rather than a capsule, because this label wraps
                // at an accessibility size and a pill around three lines of
                // text is not what that style would have drawn.
                .overlay(
                    RoundedRectangle(cornerRadius: 12, style: .continuous).strokeBorder(
                        (canDropIn ? Color.brandInk : Color.fgMuted).opacity(0.45),
                        lineWidth: 1
                    )
                )
                .contentShape(.rect)
        }
        .buttonStyle(.plain)
        // Unavailable rather than absent, because the room is on screen and
        // the reason is on it: the status pill two lines up for a room that
        // is not connected (`target_offline`, `phone_dropin.py:80-81`), the
        // `in call with X` line directly above for a room that is busy with
        // somebody else (`target_busy`, `:78-79`), and
        // ``RoomsStrings/noAecMic`` directly above for a microphone that
        // cannot do two-way audio at all (`target_no_aec`, `:84-85`).
        // Android's own button is drawn and disabled in all three
        // (`PhoneDropinCall.kt:81`, composed at `SatOverviewTab.kt:564`).
        .disabled(!canDropIn)
        .sheet(isPresented: $showing, onDismiss: hangUp) {
            if let call {
                DropInCallSheet(call: call, room: room.roomID) {
                    showing = false
                }
            }
        }
    }

    /// Build the call the first time, then start it.
    ///
    /// ``DropInCallModel`` is built **here** and not in DomovoiKit: it needs
    /// an `AVAudioEngine`, an `AVAudioSession` and the microphone prompt, and
    /// all three are iOS-only and not compiled where the package's tests run.
    /// ``RoomsModel/adopt(call:)`` takes it, and what the package owns is the
    /// lifetime and every decision in it.
    private func open() {
        let call: DropInCallModel
        if let existing = model.call {
            call = existing
        } else {
            let (audio, session, permission) = hardware()
            call = DropInCallModel(
                client: shell.container.client,
                transport: URLSessionDropInTransport(),
                audio: audio,
                session: session,
                permission: permission,
                // This phone's identity in the house's `active_dropins`, which
                // is what makes `initiator_busy` work: "give it a stable
                // per-device value" (`domovoi/main.py:7064`). Nil before the
                // device register has answered, and the core then uses its own
                // `phone` — see ``dropInSocketURL(house:info:room:phoneID:)``.
                phoneID: shell.deviceID
            )
            model.adopt(call: call)
        }
        Task { await call.start(room: room.roomID) }
    }

    /// The engine, the audio session and the microphone prompt — and, **only
    /// in a build that cannot ship**, the stand-ins a UI case puts in their
    /// place.
    ///
    /// ``DropInTestSeam`` has the whole argument for why there is a seam here
    /// at all and what keeps it honest; the short of it is that the
    /// microphone prompt is a system alert, so without this there is no UI
    /// coverage of this feature — and that every decision below the hardware
    /// stays the app's own either way.
    ///
    /// Review round 1 asked for the `#if DEBUG`, and both halves of the seam
    /// carry it now: outside a debug build there is no reference to the
    /// stand-ins here and no ``DropInTestSeam`` for a launch argument to
    /// reach. A function rather than three `var`s so that a release build has
    /// nothing it declares and never writes.
    ///
    /// **Our own instance of the session control, deliberately.**
    /// `AVAudioSession.sharedInstance()` is process-wide, so the category
    /// this sets has exactly the right effect on the one real session; only
    /// `active` is per-instance, and it answers "did *this* object activate
    /// something", which is what the release path needs and what a shared
    /// object could not say.
    private func hardware() -> (any DropInAudio, PlayerAudioSessionControl, any DropInMicPermission) {
        #if DEBUG
        switch DropInTestSeam.asked(ProcessInfo.processInfo.arguments) {
        case .deny:
            return (RecordingDropInAudio(), RecordingPlayerAudioSession(), FixedMicPermission(false))
        case .pump:
            return (PumpedDropInAudio(), RecordingPlayerAudioSession(), FixedMicPermission(true))
        case nil:
            break
        }
        #endif
        return (DropInAudioEngine(), AVAudioSessionControl(), AVAudioMicPermission())
    }

    /// Closing the sheet hangs up and clears the state, which is Android's
    /// `onClose` (`PhoneDropinCall.kt:101-105`) and its dispose net.
    ///
    /// ``DropInCallModel/abandon()`` is the same two steps with the reason
    /// written down: the room is **told**, rather than the socket being
    /// dropped and the room left open until the core's silence timeout.
    private func hangUp() {
        call?.abandon()
    }
}
