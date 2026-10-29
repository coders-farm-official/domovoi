import DomovoiKit
import SwiftUI

/// `pair this phone`: the explanation, the token field, the hint and
/// `not now`.
///
/// A port of `PairingScreen` (`ui/screens/settings/PairingScreen.kt`, map
/// §5). It is drawn **in place of the whole shell**, which is why there is
/// nothing to navigate back to and why `not now` is the way out.
///
/// ### What raises it, and what it may ask for
///
/// One thing: the active server's own origin refusing a request for want of
/// the device token, **and** the identity gate admitting the server — so a
/// rogue's 401 cannot invite a token paste (A6-03). There is no proactive
/// "pair now" step; a freshly trusted server is used bare until it refuses
/// something.
///
/// This screen asks the question a third time, in its own words:
/// ``ShellModel/pairingOffersTheTokenField`` is
/// ``pairingOffersTheField(verdict:)`` over the active verdict, and a verdict
/// that does not admit the token gets the warning instead of a field. The two
/// other places that ask are the refusal that raises the screen and
/// ``DomovoiClient/checkDeviceToken(_:)``, which refuses to send a candidate
/// the gate does not admit.
///
/// ### Back, and the keyboard
///
/// **Android's back button does nothing in this state** (map §11.1: the
/// screen registers no `BackHandler`, so a press exits the app).
/// `ANDROID-SHELL-MAP.md` §11.1 calls that a probable defect and the owner
/// has not said what back should do; it is deliberately not being changed
/// during the v1 soak. Nothing here copies it and nothing here invents an
/// answer: an iPhone has no back button, this screen replaces the whole
/// stack so there is no edge-swipe target behind it, and `not now` is the one
/// way out other than pasting a token — which is exactly Android's reachable
/// behaviour, minus the exit. The seam is ``ShellModel/dismissPairing()``:
/// the day back is given a meaning, that is what it calls.
///
/// Android needs `imePadding()` here because the screen renders before any
/// `Scaffold` and its `verticalScroll` would otherwise have a range of
/// **zero** — the content fits a box that is still the full window height, so
/// scrolling to reveal the clipped hint is a genuine no-op. iOS avoids the
/// keyboard by itself inside a `ScrollView`, so there is no counterpart to
/// port; **the shape of the bug is reproducible all the same**, and it is on
/// the list of what a human has to confirm with the keyboard up.
struct PairingScreenView: View {

    let model: ShellModel

    var body: some View {
        GeometryReader { proxy in
            ScrollView {
                VStack(alignment: .leading, spacing: 12) {
                    Text(ShellStrings.pairTitle)
                        .font(.largeTitle.weight(.semibold))
                        .fixedSize(horizontal: false, vertical: true)
                    Text(ShellStrings.pairExplanation)
                        .font(.body)
                        .foregroundStyle(Color.fgMuted)
                        .fixedSize(horizontal: false, vertical: true)
                    Text(ServerCredentials.address(model.inputs.serverURL))
                        .font(.caption.monospaced())
                        .foregroundStyle(Color.fgMuted)
                    if let withheld = model.pairingWithheld {
                        Text(withheld)
                            .font(.footnote)
                            .foregroundStyle(.orange)
                            .fixedSize(horizontal: false, vertical: true)
                    } else {
                        CheckedPairingFieldView(field: PairingFieldModel(
                            container: model.container,
                            label: ShellStrings.pair
                        ) { _ in
                            model.showToast(ShellStrings.pairedToast)
                            model.pairingDone()
                        })
                        Text(ShellStrings.pairHint)
                            .font(.footnote)
                            .foregroundStyle(Color.fgMuted)
                            .fixedSize(horizontal: false, vertical: true)
                    }
                    Button(ShellStrings.notNow) { model.dismissPairing() }
                        .buttonStyle(.bordered)
                }
                .padding(24)
                .frame(maxWidth: 480)
                .frame(maxWidth: .infinity, minHeight: proxy.size.height, alignment: .center)
            }
        }
    }
}

/// `trust this Domovoi again?` — the yes that pins a reinstalled Domovoi's
/// new key, with both fingerprints in view.
///
/// It can only be reached where a key was **proved**
/// (``ShellModel/askTrustAgain()`` refuses to raise it otherwise, with a
/// test), which is why the two fingerprints below can be shown
/// unconditionally.
///
/// The yes is ``ShellModel/confirmTrustAgain()``, and the order inside it is
/// finding S-T1: the old household token is dropped **before** the new key is
/// pinned, so it is never sent to whatever holds that key. The phone then
/// pairs from scratch, which is what the toast says.
struct TrustAgainSheet: View {

    let model: ShellModel

    var body: some View {
        VStack(alignment: .leading, spacing: 12) {
            Label(trustAgainTitle, systemImage: "externaldrive.badge.questionmark")
                .font(.headline)
                .foregroundStyle(.orange)
            Text(ServerCredentials.address(model.inputs.serverURL))
                .font(.title3)
            if let changed = model.trustAgain {
                Text(trustAgainText(pinned: changed.expected, now: changed.seen))
                    .font(.caption.monospaced())
                    .foregroundStyle(Color.fgMuted)
                    .fixedSize(horizontal: false, vertical: true)
                    .textSelection(.enabled)
            }
            HStack {
                Spacer()
                Button(trustAgainNo) { model.dismissTrustAgain() }
                    .buttonStyle(.bordered)
                Button(trustAgainYes) { model.confirmTrustAgain() }
                    .buttonStyle(.borderedProminent)
            }
        }
        .padding(24)
        .frame(maxWidth: .infinity, alignment: .leading)
    }
}
