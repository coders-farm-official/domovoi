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
            .frame(minHeight: 44)
            .contentShape(.rect)
        }
        .buttonStyle(.bordered)
        .tint(Color.brandInk)
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
            .fixedSize(horizontal: false, vertical: true)
            .frame(minHeight: 44)
            .contentShape(.rect)
        }
        // **Bordered and not prominent, although Android's is filled.**
        // Android fills it with `colors.err` and draws white on it
        // (`PhoneDropinCall.kt:180-183`); this app has no `err` *fill* token,
        // only the ink one — `DomovoiApp.swift` has `errInk` and nothing to
        // pair it with, and that file belongs to another unit. A prominent
        // button tinted with an ink token draws its own label on it at
        // 3.08:1, which is the measurement ``RoomsBroadcastCard`` records.
        // So this is the same shape as the `hang up` control two sections up
        // (``RoomsOverviewView``), which is the other way this app says "this
        // ends something". Written up in DECISIONS under "Needs a shared
        // change".
        .buttonStyle(.bordered)
        .tint(Color.errInk)
    }
}
