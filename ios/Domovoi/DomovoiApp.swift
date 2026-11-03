import DomovoiKit
import SwiftUI

/// The app: one container, one shell model, one scene.
///
/// The counterpart of `DomovoiApplication.onCreate` plus `MainActivity`
/// (`android/.../AppContainer.kt`, `MainActivity.kt`). As there, there is no
/// dependency-injection framework: a small graph, built once, in an order a
/// reader can check — and the order is ``AppContainer``'s own documentation.
///
/// ### The store remembers now, and that is the one thing to know here
///
/// ``SavedShellStore`` is what this app builds: the server, the trust, the
/// known-server rows, the pins, the refusals and the "this phone" choice are
/// read from `UserDefaults` in its `init`, and the household tokens from the
/// Keychain.
///
/// **It used to be ``InMemoryShellStore``, on every launch**, which is how
/// the first build on Kamron's own iPhone forgot its server the moment the app
/// was force-quit — and ``TokenVault`` had no caller at all, so the token did
/// not survive either, whatever the comment here used to imply. Part 1 left
/// the persisted store to "the batch that has something to write"; parts 2
/// and 3 wrote the picker, the trust dialogs and the pairing screens and
/// never came back for it. That is the shape of defect this track has a name
/// for: shipped, but not where it is read.
///
/// A fresh install is still §2.11's first row — the local shell with a
/// `connect` pill, no banner, and nothing opening by itself — because that is
/// what an empty store says, not because there is nowhere to keep an answer.
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

    /// The alerts' process-wide half — the three engines, the notification
    /// delegate and the hint row's model (``AlertsAppWiring``, overnight
    /// `alerts`). Everything it does is in that file; this is the property
    /// that keeps it alive for the process.
    private let alerts: AlertsAppWiring

    /// A server and token handed in on the command line, for a UI test.
    ///
    /// **Why this exists.** With no server saved the shell is `.local`, so
    /// Home, the pairing screens and the token-trouble card are unreachable
    /// and a Simulator can only photograph the music placeholder. The
    /// obvious way in — drive the connection sheet and type an address —
    /// **starts the LAN discovery sweep**, and a Simulator shares the Mac's
    /// network, so that is 254 probes across whatever real network the Mac
    /// is on. Measured, not feared: the first run of `StubHouseUITests`
    /// reached `scanning 80/254…` before it was stopped.
    ///
    /// So the test seeds the store instead — **through the picker's own
    /// normaliser**, ``ServerAddress/fromTyped(_:defaultPort:)``, the one
    /// that turns what a person types into what gets saved.
    ///
    /// That used to be a claim rather than a mechanism: this method wrote the
    /// launch argument into the store verbatim and a comment asserted it was
    /// "the same thing the picker would have written". It was not. A saved
    /// address carries its scheme (`http://10.0.0.42:6369`, as every test in
    /// the package spells it) because ``ServerURL/parse(_:)`` requires
    /// `"://"` and returns nil without it, while the *displayed* address has
    /// the scheme stripped (``ServerCredentials/address(_:)``) — and the
    /// launch argument was written in the displayed form.
    ///
    /// What that cost: `ServerCredentials.isBlank` is false for
    /// `127.0.0.1:6601`, so the shell believed a server was saved; nothing
    /// could parse it, so ``DomovoiClient/answers()`` returned false without
    /// sending a byte; and after ``unreachableGraceMs`` the app dropped to
    /// local media saying "Can't reach 127.0.0.1:6601" about a server it had
    /// never contacted. Two Simulator runs went into that. The stub house's
    /// own request log is what settled it — the only two requests it ever saw
    /// came from `stub_run.sh`'s readiness `curl`.
    ///
    /// Going through `fromTyped` executes the claim instead of making it: the
    /// scheme, the default port and ``CleartextPolicy`` are the picker's
    /// rules, applied by the picker's code. An address it refuses trips an
    /// assertion rather than booting into a plausible-looking lie.
    ///
    /// `#if DEBUG` keeps it out of any build that could ship. It is also
    /// why this reads `ProcessInfo` rather than `UserDefaults`: a launch
    /// argument lives for one process, where a default would persist and
    /// could outlive the test that set it.
    ///
    /// **A seeded launch gets the in-memory store, and that is deliberate.**
    /// Now that the ordinary path saves, a stub-house run that used
    /// ``SavedShellStore`` would write the stub's address into the
    /// Simulator's defaults and hand it to the next test — and read back
    /// whatever the previous one left. The seam that keeps a launch argument
    /// from outliving its process is worth nothing if the store it seeds
    /// outlives it instead. So: seeded ⇒ nothing is kept; otherwise ⇒
    /// everything is.
    private static func store(vault: TokenVault) -> any ShellStore {
        #if DEBUG
        let arguments = ProcessInfo.processInfo.arguments
        func value(after flag: String) -> String? {
            guard let index = arguments.firstIndex(of: flag),
                  arguments.index(after: index) < arguments.endIndex
            else { return nil }
            return arguments[arguments.index(after: index)]
        }
        if let server = value(after: "-domovoi-server"), !server.isEmpty {
            guard case .ok(let url)? = ServerAddress.fromTyped(server) else {
                assertionFailure(
                    "-domovoi-server \(server) is not an address the picker would accept"
                )
                return InMemoryShellStore()
            }
            return InMemoryShellStore(serverURL: url, token: value(after: "-domovoi-token"))
        }
        #endif
        return SavedShellStore(vault: vault)
    }

    init() {
        // **One vault, handed to both.** Two `TokenVault()`s would reach the
        // same Keychain items — same service, same accounts — so this is not
        // a correctness fix; it is so that "where do the tokens live" has one
        // answer at the one place that builds the graph, and so that a test
        // or a future seam can replace it in one line rather than two.
        let vault = TokenVault()
        let container = AppContainer(store: Self.store(vault: vault), vault: vault)
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
        // register never happened, because ``SocketRelay`` does not replay.
        // These two lines, in this order, are the whole of what prevents it —
        // which is also why ``ShellModel/watchSocket()`` takes the socket's
        // CURRENT value rather than waiting for the next change.
        model.watchSocket()
        // Registering the path monitors, and opening the state socket, are
        // side effects on the process, which is why they are a separate call
        // the app makes and not something a container does to whoever builds
        // one.
        container.start()
        // After `container.start()`, because the alerts' first act is a read
        // through the container's client, and `DomovoiApplication.onCreate`
        // starts them beside the socket in the same order
        // (`TimerAlerts.kt:97`).
        alerts = AlertsAppWiring(container: container)
    }

    var body: some Scene {
        WindowGroup {
            AppShellView(model: model)
                // ### The app's tint is the **ink**, not the fill
                //
                // The `AccentColor` asset is ``Color/brand``, so every
                // control that inherits a tint — which for a plain `Button`
                // is the colour of its *label* — drew text at **2.05:1** on
                // white. ``Color/brand``'s own doc comment says "a fill,
                // never text" and the asset was quietly making it text
                // everywhere.
                //
                // Apple's audit named the element at last: a plain
                // `Button` reading `back to Rooms & devices` on the pairing
                // screen, "Contrast failed". It is one of several on that
                // screen and the same for every plain button in the app, so
                // the fix belongs here rather than at each call site.
                //
                // ``Color/brandInk`` is 5.65:1 on white and is what the
                // stylesheet has for "the brand as text or a meaningful
                // graphic". The six `.borderedProminent` buttons name
                // ``Color/brand`` back for their fill, because near-black
                // ``Color/brandFg`` on the ink would be 3.08:1 — the fill
                // role and the text role are two tokens for a reason.
                .tint(Color.brandInk)
                // The alerts' hint: the model goes into the environment for
                // Home's timers card to draw a row from, and the lifecycle
                // re-reads this phone's notification status on every return
                // to the foreground (`HomeScreen.kt:241-247`).
                //
                // **On the app's root rather than Home's**, which is the one
                // place this departs from DECISIONS §2's hook 2. Home's root
                // is `HomeView.swift`, and the modifier carries the explain
                // card's overlay: a dialog that belongs to a screen inside a
                // tab is a dialog a tab switch can take away mid-sentence,
                // and `.accessibilityHidden` behind the scrim then covers
                // the tab bar too — which is what `.isModal` promises and
                // what the other `DialogCard` in the app had to be given by
                // hand (`RouteScreen.swift:196-203`). The read is a status
                // read and never a prompt, so doing it once at launch asks
                // nothing of anybody.
                .environment(\.alertsHintModel, alerts.hint)
                .alertsHintLifecycle(alerts.hint)
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

    /// `--brand`: Domovoi's one accent, amber.
    ///
    /// Also the app's `AccentColor` asset, so `.tint` and `.accentColor`
    /// resolve to it without a call site asking. This constant exists for the
    /// few places that name the brand explicitly rather than inheriting it.
    ///
    /// `#F2A618` light and `#F9AD26` dark — the two values Android ships,
    /// which is how the conversion from the stylesheet's `oklch` was checked.
    ///
    /// **A fill, never text.** It is **2.05:1** on white, which is under the
    /// 3:1 that even a meaningful *graphic* needs, let alone text. Use
    /// ``Color/brandInk`` for anything a person has to read or interpret on a
    /// light surface, and ``Color/brandFg`` for text drawn **on** this.
    static let brand = Color(UIColor { traits in
        traits.userInterfaceStyle == .dark
            ? UIColor(red: 0.9758, green: 0.6765, blue: 0.1471, alpha: 1)   // oklch(0.80 0.16 75)
            : UIColor(red: 0.9491, green: 0.6513, blue: 0.0950, alpha: 1)   // oklch(0.78 0.16 75)
    })

    /// `--brand-fg`: text drawn **on** the amber, which the stylesheet calls
    /// "text on amber buttons". Near-black, not white: white on amber is
    /// 2.05:1, this is **8.49:1** light and **9.87:1** dark.
    ///
    /// `#281600` light, matching Android's `brandFg`.
    static let brandFg = Color(UIColor { traits in
        traits.userInterfaceStyle == .dark
            ? UIColor(red: 0.1084, green: 0.0566, blue: 0.0000, alpha: 1)   // oklch(0.18 0.04 75)
            : UIColor(red: 0.1561, green: 0.0879, blue: 0.0000, alpha: 1)   // oklch(0.22 0.05 75)
    })

    /// `--brand-ink`: the brand **as text or a meaningful graphic** on a light
    /// surface — links, the selected tab, the live dot.
    ///
    /// **5.65:1** on white and **9.84:1** on the dark canvas. Dark mode is
    /// `--brand` itself, because that stylesheet's dark block says
    /// `--brand-ink: var(--brand)`.
    ///
    /// The dots take this and not ``Color/brand``: a dot that is the only
    /// thing saying whether the house is live is a graphic that has to be
    /// seen, and 2.05:1 is under the 3:1 such a graphic needs. That is the
    /// one place this port departs from a literal reading of the token
    /// names, and the reason is a measurement.
    static let brandInk = Color(UIColor { traits in
        traits.userInterfaceStyle == .dark
            ? UIColor(red: 0.9758, green: 0.6765, blue: 0.1471, alpha: 1)   // oklch(0.80 0.16 75)
            : UIColor(red: 0.5923, green: 0.3456, blue: 0.0000, alpha: 1)   // oklch(0.52 0.13 70)
    })

    /// `--warn-ink`: the amber hue **as text or an icon**, which is not the
    /// amber used for dots and fills.
    ///
    /// `web/static/colors_and_type.css:166-170` already draws this
    /// distinction and says why: the status fills are "for buttons, dots and
    /// washes; at their lightness amber and green read about 2:1 as text on
    /// white", so there is a second, deeper token for text. The iOS port used
    /// SwiftUI's `.orange` — `systemOrange`, `#FF9500` — for the text role at
    /// every site, and Apple's accessibility audit failed Home with "Contrast
    /// failed" because of it.
    ///
    /// Measured, not assumed: `systemOrange` on white is **2.20:1**, which is
    /// the stylesheet's "about 2:1" exactly. This token is **5.73:1** on
    /// white and **9.84:1** on the dark canvas. Body text needs 4.5:1.
    ///
    /// Dark mode takes `--warn` itself, because the dark block of that
    /// stylesheet says `--warn-ink: var(--warn)` — on a dark canvas the fill
    /// already reads.
    static let warnInk = Color(UIColor { traits in
        traits.userInterfaceStyle == .dark
            ? UIColor(red: 1.0000, green: 0.6604, blue: 0.2897, alpha: 1)   // oklch(0.82 0.16 60)
            : UIColor(red: 0.6167, green: 0.3244, blue: 0.0000, alpha: 1)   // oklch(0.52 0.13 60)
    })

    /// `--ok-ink`: the same rule for green, which is the worst of the three.
    /// `systemGreen` (`#34C759`) is **2.22:1** on white. This is **5.65:1**,
    /// and **10.01:1** on the dark canvas.
    static let okInk = Color(UIColor { traits in
        traits.userInterfaceStyle == .dark
            ? UIColor(red: 0.3807, green: 0.8332, blue: 0.4147, alpha: 1)   // oklch(0.78 0.18 145)
            : UIColor(red: 0.1169, green: 0.4659, blue: 0.1627, alpha: 1)   // oklch(0.50 0.14 145)
    })

    /// `--err-ink`: the same rule for red. SwiftUI's `.red` is `systemRed`,
    /// **3.55:1** on white — the near miss rather than the gross one, and
    /// still under the 4.5:1 body text needs.
    ///
    /// **6.08:1** on white, **6.30:1** on the dark canvas.
    static let errInk = Color(UIColor { traits in
        traits.userInterfaceStyle == .dark
            ? UIColor(red: 1.0000, green: 0.3718, blue: 0.3577, alpha: 1)   // oklch(0.70 0.20 25)
            : UIColor(red: 0.7451, green: 0.1339, blue: 0.1637, alpha: 1)   // oklch(0.52 0.19 25)
    })
}

#Preview {
    FirstRunView()
}
