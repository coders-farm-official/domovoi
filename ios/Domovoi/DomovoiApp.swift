import DomovoiKit
import SwiftUI

/// The app: one container, one shell model, one scene.
///
/// The counterpart of `DomovoiApplication.onCreate` plus `MainActivity`
/// (`android/.../AppContainer.kt`, `MainActivity.kt`). As there, there is no
/// dependency-injection framework: a small graph, built once, in an order a
/// reader can check — and the order is ``AppContainer``'s own documentation.
///
/// ### The store is in memory, and that is the one thing to know here
///
/// ``InMemoryShellStore`` is still the only ``ShellStore`` there is, so **the
/// saved server does not survive a launch**. Part 1 left the persisted store
/// to the batch that has something to write (the picker and Settings → App
/// layout, part 3's), and a half-ported settings store is worse than none.
///
/// What that means on a Simulator today: every launch is a fresh install with
/// no server, which is §2.11's first row — the local shell with a `connect`
/// pill, no banner, and nothing opening by itself. That is the right screen
/// for that state, and it is the only state this build can reach without a
/// picker. The other states are reachable in the previews beside each view.
///
/// ### Room mode is not here
///
/// Android reads `RoomStore.isActive` in `onCreate` and sends the phone to a
/// different activity **before a container exists**, so nothing can make a
/// request first. ``HouseholdLock`` is the seam here
/// (`AppContainerTests.testRoomModeSealsTheTokenBeforeAnyRequestIsBuilt`) and
/// the scene that reads it is a later batch's; until then this app has no
/// room mode rather than a half of one.
@main
struct DomovoiApp: App {

    private let model: ShellModel

    init() {
        let container = AppContainer(store: InMemoryShellStore())
        // The device id, and the two things only UIKit can answer. Read HERE,
        // on the main actor, once: ``DeviceSelf`` exists so that a `Sendable`
        // closure running anywhere never reads `UIDevice`, which
        // ``DomovoiKit`` cannot even import.
        //
        // The id is kept in `UserDefaults` and deliberately not the Keychain
        // (``DeviceIdentity``), and `ownerPersonID` is nil because no
        // preference holds one yet — see ``DeviceSelf``, which says what the
        // route does with a null and which batch has to fill it in.
        let deviceModel = UIDevice.current.model
        let who = DeviceSelf(
            deviceID: DeviceIdentity.stable(in: DeviceIdentity.UserDefaultsStore()),
            name: DeviceRegistry.suggestedDeviceName(model: deviceModel),
            userAgent: DeviceRegistry.userAgent(
                systemVersion: UIDevice.current.systemVersion,
                model: deviceModel
            ),
            ownerPersonID: nil
        )
        let model = ShellModel(container: container, deviceSelf: { who })
        self.model = model
        // **Subscribed before the socket is opened**, in this order and not
        // in a view's `.task`, which runs later: a connect that landed in
        // between would be a connect whose capability refresh and device
        // register never happened. ``SocketRelay/missedConnections`` is the
        // number that says whether that window was ever entered, and these
        // two lines are why it should read zero.
        model.watchSocket()
        // Registering the path monitors, and opening the state socket, are
        // side effects on the process, which is why they are a separate call
        // the app makes and not something a container does to whoever builds
        // one.
        container.start()
    }

    var body: some Scene {
        WindowGroup {
            AppShellView(model: model)
        }
    }
}

/// The I0 gate's screen: it launched, it said what it was, and it showed the
/// Local Network prompt's wording on Kamron's own iPhone.
///
/// **No longer the app's root** — the shell is — and kept because the two
/// things it documents are still true of every screen from here on: the
/// scroll-when-it-does-not-fit layout that Apple's audit failed this screen
/// for, and ``Color/fgMuted``, which is the only design token this target has
/// until the asset catalogue generated from `web/static/colors_and_type.css`
/// lands (plan 1.1).
struct FirstRunView: View {
    /// Centred when it fits, scrollable when it doesn't.
    ///
    /// Apple's accessibility audit failed this screen with "Text clipped" on
    /// 2026-10-28, because it re-renders at the largest Dynamic Type size and
    /// a plain `VStack` cannot grow past the screen — it clips instead. A
    /// household has people who turn text up; this is the layout every screen
    /// from I1 on needs, so it is worth getting right on the one with three
    /// labels rather than discovering it on Home.
    ///
    /// `minHeight: proxy.size.height` keeps the content centred at ordinary
    /// text sizes and lets it scroll past the bottom at large ones.
    /// `fixedSize(vertical:)` stops the text being squeezed instead of
    /// wrapping. Never `minimumScaleFactor` here: shrinking text to fit is
    /// the opposite of what someone who turned it up asked for.
    var body: some View {
        GeometryReader { proxy in
            ScrollView {
                VStack(spacing: 16) {
                    Text("Domovoi")
                        .font(.largeTitle.weight(.semibold))
                        .fixedSize(horizontal: false, vertical: true)
                    Text("The app for your house. Nothing is set up yet.")
                        .font(.body)
                        .multilineTextAlignment(.center)
                        .foregroundStyle(Color.fgMuted)
                        .fixedSize(horizontal: false, vertical: true)
                    Text(Self.state)
                        .font(.footnote)
                        .multilineTextAlignment(.center)
                        .foregroundStyle(Color.fgMuted)
                        .fixedSize(horizontal: false, vertical: true)
                }
                .padding(24)
                .frame(maxWidth: 420)
                .frame(maxWidth: .infinity, minHeight: proxy.size.height)
            }
        }
    }

    /// Proof that the package is linked, and the one fact worth showing on a
    /// screen with nothing on it: which addresses this build will talk to in
    /// the clear.
    private static var state: String {
        let suffixes = CleartextPolicy.privateNameSuffixes.joined(separator: " ")
        return "Plain http goes to the home network only: \(suffixes), and private addresses."
    }
}

extension Color {
    /// `--fg-muted` from `web/static/colors_and_type.css`, the ink the
    /// dashboard already uses for labels and meta, in both themes.
    ///
    /// Not SwiftUI's `.secondary`. On 2026-10-28 Apple's own accessibility
    /// audit failed this screen with "Contrast nearly passed", and it was
    /// right: iOS `secondaryLabel` is `rgba(60, 60, 67, 0.6)`, which over
    /// white is **3.44:1** — under the 4.5:1 that body text needs. The token
    /// measures **7.44:1** on the light canvas and **7.88:1** on the dark one.
    ///
    /// The audit's finding was in our own content, not SwiftUI's chrome, so
    /// the fix is the colour, not narrowing the audit (`LaunchUITests`).
    ///
    /// I1 replaces this with a real asset catalogue generated from the CSS,
    /// as plan 1.1 describes; one colour written out by hand is enough for a
    /// screen with three labels on it.
    static let fgMuted = Color(UIColor { traits in
        traits.userInterfaceStyle == .dark
            ? UIColor(red: 0.6555, green: 0.6434, blue: 0.6238, alpha: 1)   // oklch(0.72 0.008 80)
            : UIColor(red: 0.3458, green: 0.3323, blue: 0.3104, alpha: 1)   // oklch(0.45 0.01 80)
    })
}

#Preview {
    FirstRunView()
}
