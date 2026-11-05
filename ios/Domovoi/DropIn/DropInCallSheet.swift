import DomovoiKit
import SwiftUI

/// The call itself, while it is on — a port of `PhoneCallDialog`
/// (`PhoneDropinCall.kt:110-199`).
///
/// ### A sheet, not a card in a `ZStack`
///
/// Android uses a `Dialog`, and the iOS equivalent people reach for is a card
/// drawn over the screen. **That was measured as doing nothing**: with such a
/// card up, Apple's audit reported `Contrast failed` against five views
/// *behind* the 35% scrim and the accessibility tree listed the whole of the
/// screen underneath as the card's siblings (`DECISIONS-FOR-KAMRON.md` #6,
/// train S009). Kamron, answer 6: dialogs become real presentations. A sheet
/// is one, so what is behind it is behind it for VoiceOver and for the audit
/// too.
///
/// ### `hang up` is not a destructive confirmation
///
/// It is the only way out and it is what the person came to press. No
/// confirmation dialog, which Android has none of either — a call nobody can
/// end in one tap is worse than a call ended by mistake, and starting another
/// is one tap away.
struct DropInCallSheet: View {

    let call: DropInCallModel

    /// The room asked for. ``DropInCallModel/State`` carries the peer the
    /// server names once the call is live, which is the same room — but it is
    /// the server's word for it and this is ours, so the title uses this one
    /// and does not move when the call connects.
    let room: String

    let onClose: () -> Void

    @Environment(\.dynamicTypeSize) private var typeSize

    var body: some View {
        // **A scroll view, and a detent that grows** — review round 1 found
        // the bug this fixes. The content was a fixed-height `.medium` sheet
        // with no way to scroll it, and at an accessibility type size this
        // is a `.title2` heading, a wrapping status line, two 44-point
        // buttons **stacked** and a wrapping `.caption` inside 24 points of
        // padding: taller than half a phone, and the thing clipped off the
        // bottom is `hang up` — the only way out of a live microphone in
        // somebody's kitchen.
        //
        // `.scrollBounceBehavior(.basedOnSize)` so the ordinary size still
        // feels like a card and not a scroller.
        ScrollView {
            VStack(spacing: 14) {
                Text(DropInStrings.spoken(room))
                    .font(.title2)
                    .accessibilityAddTraits(.isHeader)
                    .fixedSize(horizontal: false, vertical: true)
                status
                controls
                Text(DropInStrings.twoWayAudio(room: room))
                    .font(.caption)
                    .foregroundStyle(Color.fgMuted)
                    .multilineTextAlignment(.center)
                    .fixedSize(horizontal: false, vertical: true)
            }
            .padding(24)
            .frame(maxWidth: .infinity, alignment: .center)
        }
        .scrollBounceBehavior(.basedOnSize)
        // A sheet that cannot be swiped away by accident: this one holds a
        // live microphone in a room, so the way out is the button that says
        // what it does. `interactiveDismissDisabled` only while the call is
        // on — once it has ended there is nothing to protect.
        .interactiveDismissDisabled(call.state.isBusy)
        // Past `isAccessibilitySize` there is **only** `.large`, so the sheet
        // opens at the height that holds the buttons; below it, `.medium` is
        // the smallest and so the one it opens at, and `.large` is there to
        // be dragged to. Not a `selection:` binding pinned to a constant —
        // that would draw a grabber the sheet snaps back from, which is a
        // control that cannot do what it says.
        .presentationDetents(typeSize.isAccessibilitySize ? [.large] : [.medium, .large])
    }

    /// `connecting…`, the ringing sentence, `live · 0:05`, `call ended`, or
    /// the failure's own words (`PhoneDropinCall.kt:141-163`).
    @ViewBuilder
    private var status: some View {
        switch call.state {
        case .idle, .connecting:
            HomeTonePill(label: DropInStrings.connecting, tone: .idle)
        case .ringing(let peer):
            // **Not Android's.** The server's ring mode sends
            // `dropin_ringing` and opens no audio until somebody in the room
            // answers (`domovoi/phone_dropin.py:26-32`); Android handles no
            // such frame, so over there this looks like a call that will not
            // connect. ``DropInStrings/ringing(room:)`` has the note.
            HStack(spacing: 8) {
                ToneDot(tone: .warn, live: true)
                Text(DropInStrings.ringing(room: peer))
                    .font(.subheadline)
                    .foregroundStyle(Color.warnInk)
                    .multilineTextAlignment(.center)
                    .fixedSize(horizontal: false, vertical: true)
            }
            .accessibilityElement(children: .combine)
        case .live:
            HStack(spacing: 8) {
                ToneDot(tone: .brand, live: true)
                Text(DropInStrings.live(call.elapsedSec))
                    .font(.subheadline)
                    .foregroundStyle(Color.fgMuted)
                    .fixedSize(horizontal: false, vertical: true)
            }
            .accessibilityElement(children: .combine)
        case .ended:
            HomeTonePill(label: DropInStrings.callEnded, tone: .idle)
        case .failed(let note):
            // The sentence, not a code: every one of these comes from
            // ``DropInStrings/refusal(_:)`` or from the microphone, and each
            // says what happened in words somebody can act on.
            Text(note)
                .font(.subheadline)
                .foregroundStyle(Color.errInk)
                .multilineTextAlignment(.center)
                .fixedSize(horizontal: false, vertical: true)
        }
    }

    /// `mute` while the call is live, and the one button that ends it
    /// (`PhoneDropinCall.kt:166-191`).
    @ViewBuilder
    private var controls: some View {
        if typeSize.isAccessibilitySize {
            // Two 44-point buttons with words on them do not share a line at
            // AX5; this screen's own rule, and the broadcast card's.
            VStack(spacing: 12) {
                if call.state.isLive { mute }
                leave
            }
        } else {
            HStack(spacing: 12) {
                if call.state.isLive { mute }
                leave
            }
        }
    }

    private var mute: some View {
        Button {
            call.setMuted(!call.muted)
        } label: {
            Label(
                call.muted ? DropInStrings.unmute : DropInStrings.mute,
                systemImage: call.muted ? "mic.slash" : "mic"
            )
            .font(.subheadline)
            .fixedSize(horizontal: false, vertical: true)
            // **The same measurement as ``RoomsDropInRow``'s control**, taken
            // off this sheet's own screenshot: `.buttonStyle(.bordered)` with
            // `.tint(Color.brandInk)` drew `#975800` on `#ECE1D1`, **4.38:1**
            // (`I005/ALL/att/testRooms_DropInFromThisPhoneIsHeardAndHungUp/
            // rooms-27-dropin-live….png`, this button's frame
            // `{{95.7, 495.0}, {82.3, 58.0}}`), under the 4.5:1 its
            // subheadline needs. The caption below it measures 7.43:1 in the
            // same picture, so this is the one pairing on the sheet under the
            // threshold.
            //
            // Android's is an `OutlinedButton` too (`PhoneDropinCall.kt:168`),
            // so the label belongs on the sheet's white canvas, where
            // ``Color/brandInk`` is 5.66:1. `hang up` beside it is unchanged:
            // it is ``Color/dangerFg`` on ``Color/danger`` and the same
            // picture measures it at 5.93:1.
            .foregroundStyle(Color.brandInk)
            .padding(.horizontal, 12)
            .frame(minHeight: 44)
            .overlay(
                RoundedRectangle(cornerRadius: 12, style: .continuous)
                    .strokeBorder(Color.brandInk.opacity(0.45), lineWidth: 1)
            )
            .contentShape(.rect)
        }
        .buttonStyle(.plain)
    }

    /// `hang up` while the call is on, `close` once it is over — one button,
    /// as Android's is (`PhoneDropinCall.kt:188`).
    private var leave: some View {
        Button {
            onClose()
        } label: {
            Label(
                call.state.isBusy ? DropInStrings.hangUp : DropInStrings.close,
                systemImage: "phone.down"
            )
            .font(.subheadline)
            // **``Color/dangerFg`` and not `.white`, and on the label and not
            // on the `Button`.** `--danger-fg` flips with the theme because
            // white on the dark fill (`#FF5F5B`) is 2.99:1 —
            // ``DangerInk/whiteOnTheDarkFillRatio``, which is the number the
            // pair exists to avoid. The pair measures 5.91:1 light and 6.53:1
            // dark (``DangerInk/lightArithmeticRatio``,
            // ``DangerInk/darkArithmeticRatio``), recomputed by
            // `ShellFoldTests`.
            //
            // It is on the `Label` because `.borderedProminent` picks its own
            // label colour out of the style, and a `.foregroundStyle` applied
            // outside the `Button` is the half of that pair SwiftUI is free to
            // win. Inside, it is the label's own colour.
            .foregroundStyle(Color.dangerFg)
            .fixedSize(horizontal: false, vertical: true)
            .frame(minHeight: 44)
            .contentShape(.rect)
        }
        // **Filled, as Android's is** (`PhoneDropinCall.kt:180-183`:
        // `colors.err` with white on it).
        //
        // ### And deliberately NOT the same shape as `hang up` two sections up
        //
        // The comment this replaced justified `.bordered` as parity with
        // ``RoomsOverviewView``'s `hang up`, "the other way this app says
        // 'this ends something'". Android does not say it the same way twice,
        // and that is the answer rather than a divergence: the dialog's end
        // button is a filled `Button` with the error container
        // (`PhoneDropinCall.kt:180-183`) and the room overview's is an
        // `OutlinedButton` (`SatOverviewTab.kt:520-524`). One is the end of
        // *this phone's* call, on the sheet that is the call; the other ends
        // a call between two rooms that somebody else started, in a row of a
        // list. So the pair here matches the pair there, and review 4 §4.2's
        // finding was that the parity argument had been deleted rather than
        // answered. It is answered here and in ``RoomsOverviewView``.
        //
        // The shell unit added the fill token
        // this needed — ``Color/danger`` with ``Color/dangerFg`` for the text
        // on it, both built from ``DangerInk`` — which is what the rooms
        // unit asked for under "needs a shared change" and why this button
        // shipped `.bordered` with `.tint(Color.errInk)` until the
        // integration. A prominent button tinted with the *ink* token drew
        // its own label at 3.08:1, the measurement ``RoomsBroadcastCard``
        // records; the two-token pair is how that is avoided in both themes.
        .buttonStyle(.borderedProminent)
        .tint(Color.danger)
    }
}
