import DomovoiKit
import SwiftUI

/// `This Domovoi isn't set up yet`: the one screen a Domovoi nobody has
/// claimed gets, in place of the workspace.
///
/// A port of `UnclaimedServerScreen` (`android/.../ui/shell/UnclaimedServer.kt:84-123`,
/// commits 70ce74be and cfe9b0dd). Every word is ``UnclaimedServerStrings``;
/// when it is drawn is ``unclaimedReplaces(_:blocks:)`` over
/// ``ShellModel/unclaimed``, both in `DomovoiKit` with their tests. This
/// file draws, and its four buttons each call something that already exists:
///
///  - `Check again` → ``UnclaimedServerModel/checkAgain(unreachable:)``. Off,
///    and reading `checking…`, while a read is out — Android's
///    `enabled = !checking`.
///  - `open the dashboard` → `openURL` on the server's plain address
///    (``unclaimedDashboardURL(_:)``), which is where the dashboard's
///    first-run welcome and its claim form are. The completion is the honest
///    half, as on Health's link: nothing taking the URL says so, in Android's
///    words for this screen.
///  - `use this phone's media` → ``ShellModel/chooseThisPhone()``, the token
///    card's own way to the local shell (Android's `setPreferLocal(true)`).
///  - `use a different server` → ``ShellModel/openConnection()``, the pill's
///    own action (Android's `LocalOpenConnection`).
///
/// ### Shape
///
/// Android's column: centred, at most 480 wide, scrolling when it does not
/// fit, a filled button and three text buttons. Two things differ, both on
/// purpose:
///
///  - **No glyph.** Android draws `DomovoiGlyph(48)` above the title; this
///    app has no brand glyph view to draw, and a substitute would be a
///    picture nobody chose.
///  - **The address and the hint are in ``Color/fgMuted``**, not Android's
///    `fgFaint`/`fgSubtle`: those inks are under 4.5:1 on white, which
///    Apple's audit fails, and the track's rule is the measured ink
///    (`InkButtonStyles.swift`'s header).
///
/// The text buttons get a 44-point target, which Android's `TextButton` has
/// by its own minimum size and a borderless SwiftUI button does not.
struct UnclaimedServerView: View {

    let model: ShellModel

    @Environment(\.openURL) private var openURL

    var body: some View {
        let claim = model.unclaimed
        let address = ServerCredentials.address(model.inputs.serverURL)
        GeometryReader { proxy in
            ScrollView {
                VStack(spacing: 12) {
                    Text(UnclaimedServerStrings.title)
                        .font(.title2.weight(.semibold))
                        .multilineTextAlignment(.center)
                        .fixedSize(horizontal: false, vertical: true)
                        .accessibilityAddTraits(.isHeader)
                    Text(address)
                        .font(.caption.monospaced())
                        .foregroundStyle(Color.fgMuted)
                    Text(UnclaimedServerStrings.text(address: address))
                        .font(.body)
                        .foregroundStyle(Color.fgMuted)
                        .multilineTextAlignment(.center)
                        .fixedSize(horizontal: false, vertical: true)
                    Button(claim.checking ? UnclaimedServerStrings.checking : UnclaimedServerStrings.checkAgain) {
                        claim.checkAgain(unreachable: model.unreachable)
                    }
                    .buttonStyle(BrandFillButtonStyle())
                    .disabled(claim.checking)
                    .padding(.top, 4)
                    // Drawn only when there is an address to open: a button
                    // that cannot do what it says is not drawn.
                    if let dashboard = unclaimedDashboardURL(model.inputs.serverURL) {
                        textButton(UnclaimedServerStrings.openTheDashboard) {
                            openURL(dashboard) { accepted in
                                guard !accepted else { return }
                                model.showToast(UnclaimedServerStrings.noBrowser)
                            }
                        }
                    }
                    Text(UnclaimedServerStrings.hint)
                        .font(.footnote)
                        .foregroundStyle(Color.fgMuted)
                        .multilineTextAlignment(.center)
                        .fixedSize(horizontal: false, vertical: true)
                    textButton(UnclaimedServerStrings.useThisPhonesMedia) {
                        model.chooseThisPhone()
                    }
                    textButton(UnclaimedServerStrings.useADifferentServer) {
                        model.openConnection()
                    }
                }
                .padding(24)
                .frame(maxWidth: 480)
                .frame(maxWidth: .infinity, minHeight: proxy.size.height, alignment: .center)
            }
        }
    }

    /// Android's `TextButton`: the label in the app's ink on the page, no
    /// fill, no stroke, and a whole 44-point row to hit.
    private func textButton(_ title: String, action: @escaping () -> Void) -> some View {
        Button(action: action) {
            Text(title)
                .multilineTextAlignment(.center)
                .fixedSize(horizontal: false, vertical: true)
                .frame(minHeight: InkButtonMetrics.target)
                .contentShape(.rect)
        }
        .buttonStyle(.borderless)
    }
}
