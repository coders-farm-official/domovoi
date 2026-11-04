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
    ///
    /// The button is unavailable for the whole of one, which is Android's
    /// `enabled = s.online && s.full_duplex && s.in_call_with == null`
    /// (`PhoneDropinCall.kt:81`) read off this phone's own state rather than
    /// off the roster — the roster reports *this* call a second after it
    /// starts, which is the trap Android's "always composed" comment is about
    /// (``RoomsOverviewView``'s drop-in note).
    private var busy: Bool { call?.state.isBusy ?? false }

    var body: some View {
        Button {
            showing = true
            open()
        } label: {
            Label(DropInStrings.dropInFromThisPhone, systemImage: "phone.bubble")
                .font(.subheadline)
                .fixedSize(horizontal: false, vertical: true)
                .frame(minHeight: 44)
                .contentShape(.rect)
        }
        .buttonStyle(.bordered)
        .tint(Color.brandInk)
        // A room that is not connected cannot be dropped into: the core
        // answers `target_offline` (`phone_dropin.py:80-81`). Unavailable
        // rather than absent, because the room is on screen and the reason is
        // its status pill two lines up.
        .disabled(!room.online || busy)
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
            // The three hardware pieces, and the stand-ins a UI case on a
            // Simulator puts in their place. ``DropInTestSeam`` has the whole
            // argument for why there is a seam here at all and what keeps it
            // honest; the short of it is that the microphone prompt is a
            // system alert, so without this there is no UI coverage of this
            // feature — and that every decision below the hardware stays the
            // app's own either way.
            let audio: any DropInAudio
            let session: PlayerAudioSessionControl
            let permission: any DropInMicPermission
            switch DropInTestSeam.asked(ProcessInfo.processInfo.arguments) {
            case nil:
                audio = DropInAudioEngine()
                permission = AVAudioMicPermission()
                // **Our own instance of the session control, deliberately.**
                // `AVAudioSession.sharedInstance()` is process-wide, so the
                // category this sets has exactly the right effect on the one
                // real session; only `active` is per-instance, and it answers
                // "did *this* object activate something", which is what the
                // release path needs and what a shared object could not say.
                session = AVAudioSessionControl()
            case .deny:
                audio = RecordingDropInAudio()
                permission = FixedMicPermission(false)
                session = RecordingPlayerAudioSession()
            case .pump:
                audio = PumpedDropInAudio()
                permission = FixedMicPermission(true)
                session = RecordingPlayerAudioSession()
            }
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

    /// Closing the sheet hangs up and clears the state, which is Android's
    /// `onClose` (`PhoneDropinCall.kt:101-105`) and its dispose net.
    private func hangUp() {
        call?.hangUp()
        call?.reset()
    }
}
