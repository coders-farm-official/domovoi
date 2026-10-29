import DomovoiKit
import SwiftUI

/// The root of the app: whichever of the shell's states is current, plus the
/// things that are owned **above** both shells.
///
/// Every decision this file looks like it is making has already been made in
/// `DomovoiKit` and tested there: which state the shell is in
/// (``ShellModel/screen``), what it looks like (``ShellModel/chrome``), which
/// launcher to draw (``shellLayout(idiom:width:)``), which tabs the bar has
/// (``ShellModel/phoneBar``). **Nothing in this target is compiled by
/// `ios/scripts/pkgtest.sh`** — it tests the packages, and the app target has
/// never been through `xcodebuild` (`ios/README.md`) — so a view here that
/// decided anything would be a decision nothing checks.
///
/// ### What a person has to confirm on a Simulator
///
/// Listed in `design-notes/ios/I1C2-REPORT.md` §6, because this file could
/// not be run, let alone looked at, in the batch that wrote it.
struct AppShellView: View {

    @State private var model: ShellModel
    @Environment(\.scenePhase) private var scenePhase

    init(model: ShellModel) {
        _model = State(initialValue: model)
    }

    var body: some View {
        content
            // The toast is here for the same reason the sheets are: it is
            // raised by the picker, by the pairing screen and by Settings,
            // and the shell underneath any of them can swap while it is up
            // (map §7.10, ``ShellModel/toast``).
            .overlay(alignment: .bottom) {
                if let toast = model.toast {
                    Text(toast.text)
                        .font(.footnote)
                        .padding(.horizontal, 14)
                        .padding(.vertical, 9)
                        .background(.thickMaterial, in: Capsule())
                        .padding(.bottom, 28)
                        .transition(.opacity)
                        .onTapGesture { model.dismissToast() }
                        .accessibilityAddTraits(.isStaticText)
                        .id(toast.id)
                }
            }
            // Both sheets are owned HERE, above both shells, and that is not
            // stylistic (map §10.4): picking a side, or the server dropping
            // out of reach, swaps the shell underneath it, and a dialog owned
            // by a shell would vanish mid-use.
            //
            // **ONE `.sheet` modifier, over ``ShellSheet``.** This had two —
            // one per dialog — and two sheet presentations on a single view is
            // a long-standing SwiftUI hazard: the later modifier commonly
            // wins and the earlier silently never presents. It compiles, and
            // nothing in `pkgtest.sh` can see it, which is why a review found
            // it rather than a test. Which sheet is up is now one value with
            // one answer, decided in the package.
            .sheet(item: $model.sheet) { sheet in
                switch sheet {
                case .connection:
                    ConnectionDialogView(model: model)
                case .trustAgain:
                    TrustAgainSheet(model: model)
                }
            }
            .onChange(of: scenePhase, initial: true) { _, phase in
                // The sender is passed, so the decision and the send are one
                // call: with no sender the cadence slot is not consumed at
                // all (``ShellModel/registerSender``, and I1-C part 2's F9).
                model.scenePhaseChanged(to: ShellPhase(phase), sendRegister: model.registerSender)
            }
            .task {
                // The socket is subscribed to in `DomovoiApp.init`, before it
                // is opened, and NOT here — this runs later, and a connect
                // that landed in between would be one whose capability
                // refresh and device register never happened.
                //
                // What is here is the first manifest fetch at launch, which
                // Android does by having `rememberCapabilities()` read once
                // when the shell composes. The socket's connect refreshes it
                // again; a manifest fetched twice costs one Open GET, and a
                // manifest never fetched hides both gated screens.
                await model.refreshCapabilities()
            }
    }

    @ViewBuilder
    private var content: some View {
        switch model.screen {
        case .localMedia:
            LocalShellView(model: model)
        case .workspace:
            WorkspaceView(model: model)
        case .pairing:
            PairingScreenView(model: model)
        case .tokenTroubleCard(let trouble):
            TokenTroubleCard(model: model, trouble: trouble)
        }
    }

}

extension ShellPhase {

    /// SwiftUI's phase, as the package's.
    ///
    /// `@unknown default` answers ``ShellPhase/background``, which is the safe
    /// direction: a phase this build has never heard of stops the
    /// reachability loop rather than leaving it running across something that
    /// might be a suspension. Stopping early costs a later fallback; stopping
    /// late drops a person beside a healthy server
    /// (``reachabilityAction(serverURL:connected:phase:)``).
    init(_ phase: ScenePhase) {
        switch phase {
        case .active: self = .active
        case .inactive: self = .inactive
        case .background: self = .background
        @unknown default: self = .background
        }
    }
}
