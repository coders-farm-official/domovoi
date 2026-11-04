import DomovoiKit
import SwiftUI
import UIKit

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

    /// Apple's placeholder grey, overridden for every `TextField` in the app
    /// (owner answer 65).
    ///
    /// ``HintInk`` carries the colour and the four measured ratios. The short
    /// of it: SwiftUI draws a field's hint in `UIColor.placeholderText`, which
    /// train S004's screenshot measures at **1.72:1** on the field's interior,
    /// and `--fg-muted` is 7.44:1 on white — the ink every other muted line in
    /// the app already uses, and the one Android's `OutlinedTextField` hints
    /// are drawn in.
    ///
    /// ### Why an appearance proxy, of all things
    ///
    /// There is no SwiftUI API for a placeholder's colour, and there are **39
    /// files** with a `TextField` in them. One proxy here reaches all of them,
    /// including the ones no batch has written yet, and it leaves the hint as
    /// the field's own `prompt`: `XCUIElement.placeholderValue` keeps working
    /// for the cases that read it, and VoiceOver keeps one element where an
    /// overlay would give it two with the same words.
    ///
    /// ### The vertical-axis fields, and what is NOT known about them
    ///
    /// The proxy names two classes. The single-line leg is settled: a plain
    /// `TextField` is a `UITextField`, the hint is its `placeholder`, and
    /// `XCUIElement.placeholderValue` reads it back — a case does.
    ///
    /// The `axis: .vertical` leg is **not settled, and this comment used to
    /// claim it was** (review 2's must finding: it asserted the field is
    /// "backed by `UITextView`" while the UI case asserted a `UITextView` has
    /// no placeholder of its own, and both cannot be true of the same hint).
    /// What is actually known:
    ///
    ///  * The scope, counted line by line rather than quoted: `grep -rn
    ///    "axis: .vertical" ios/Domovoi/` returns **38 lines in 21 files**,
    ///    and **21 of those lines are comments** (this one included)
    ///    explaining the rule about `submitLabel`. The fields are **17, in 15
    ///    files** — the multi-line shape this app uses wherever the audit
    ///    failed a single line — and only
    ///    `Calendar/CalendarEventEditorView.swift` has more than one (three).
    ///    Six of the 21 files are comment-only: this one,
    ///    `Printing/PrintSheetView.swift`,
    ///    `PhoneMedia/PhoneDeviceChrome.swift`,
    ///    `MusicMore/MusicAddToPlaylistSheet.swift` (whose own comment says
    ///    "which this is not"), `People/PersonMemoryTabView.swift` and
    ///    `News/NewsTopicsView.swift`. None of the 15 is this unit's.
    ///    (Review 2 reported 36 in 20 and the first fix pass 24 in 18, both
    ///    from a grep that was not split into code and comment; the per-file
    ///    list in DECISIONS §3a was wrong with it. The conclusion is the same
    ///    and the numbers were not.)
    ///
    ///    **This 17 is not the 39 below.** The 39 is files with *any*
    ///    `TextField`, which is the proxy's reach; the 17 is the vertical-axis
    ///    subset, which is this leg's fallback cost. Two populations, two
    ///    numbers, and a sentence saying so because a reader meeting them
    ///    forty lines apart read them as one figure that had drifted.
    ///  * Which view SwiftUI builds for them, and whether the prompt inside it
    ///    is a `UILabel` the proxy can reach or something SwiftUI draws
    ///    itself, is UIKit's business. **This file cannot assert it**, and
    ///    the same shape of reasoning — a sentence about a private view tree
    ///    with no reading behind it — is what review 1's S3 struck out of this
    ///    comment the first time.
    ///  * `UITextView` is named in the proxy because it costs nothing if it is
    ///    the wrong class and covers the leg if it is the right one.
    ///
    /// So the leg is **measured, not argued**:
    /// `testShell_TheHintInkIsReadableOnAVerticalField` opens Music at AX5,
    /// where its field becomes `axis: .vertical` (`MusicView.swift:688`),
    /// and reads the hint's own pixels. Apple's grey measures about 1.7:1
    /// there and this ink about 6.7:1, so that case is the answer either way.
    ///
    /// **If it fails, the fix is not a narrower class here.** It is
    /// `TextField(…, prompt: Text(hint).foregroundColor(Color.fgMuted))` at
    /// those 17 call sites, which is SwiftUI's own way of colouring a prompt,
    /// keeps the hint the field's prompt, and costs an edit in 15 files this
    /// unit does not own. That instruction is in
    /// `design-notes/ios/batch-2026-11-03/shell/DECISIONS.md` under "Needs a
    /// shared change" so that the train's fixer has it in hand and does not
    /// have to re-derive it from a doc comment.
    ///
    /// ### What this could recolour besides the hint, and what reads it
    ///
    /// An appearance proxy cannot be narrowed to a placeholder: it says
    /// "every `UILabel` inside one of these", and **what else is a `UILabel`
    /// in there is UIKit's business, not something this file can assert**.
    /// `UITextField` has historically drawn its own text through
    /// `UITextFieldLabel`, a `UILabel` subclass; if it still does on the
    /// train's iOS, this turns typed text `fgMuted` as well, in all **39
    /// files with a `TextField`** — the proxy's whole reach, and not the 17
    /// vertical-axis fields counted in the section above.
    ///
    /// An earlier version of this comment argued that it could not — a claim
    /// about a private view tree with no reading behind it, which is the
    /// shape of reasoning this track's ledger says to treat as a review
    /// target (review 1, S3).
    ///
    /// So it is measured instead, by `testShell_TheHintInkIsReadable` in
    /// `ios/DomovoiUITests/StubHouseUITests+Shell.swift`, three readings:
    ///
    ///  * an **empty** single-line field, whose darkest pixel is the hint —
    ///    about 6.7:1 against this ink and about 1.7:1 against Apple's grey;
    ///  * the same field with a word **typed** into it, whose darkest pixel is
    ///    the typed glyph — the primary ink reads near 20:1 on white and this
    ///    muted one would read about 7, so a floor of 10 tells them apart;
    ///  * Music's field at AX5, where it becomes `axis: .vertical`
    ///    (`MusicView.swift:688`) — **the leg whose backing class this file
    ///    does not assert**, four paragraphs up, and the size a hint matters
    ///    most at. The reading is the answer either way: if the proxy reaches
    ///    that field the ink measures about 6.7:1 there, and if it does not,
    ///    about 1.7:1 and DECISIONS §3a is the fallback. (This bullet said
    ///    "and is a `UITextView`" until the second fix pass — the exact
    ///    sentence the paragraphs above withdraw, left standing thirty-five
    ///    lines below them, so one doc comment contradicted itself.)
    ///
    /// If the typed reading ever fails, the fix is not this proxy with a
    /// narrower class: it is `TextField(…, prompt: Text(hint).foregroundColor(…))`
    /// at the call sites, which is SwiftUI's own way of colouring a prompt and
    /// costs an edit in each of the 39 files with a `TextField` — the whole
    /// reach, because a typed-text regression is not confined to the
    /// vertical-axis subset.
    private static func drawHintsInTheMutedInk() {
        UILabel.appearance(whenContainedInInstancesOf: [UITextField.self])
            .textColor = HintInk.ink
        UILabel.appearance(whenContainedInInstancesOf: [UITextView.self])
            .textColor = HintInk.ink
    }

    init() {
        // Before anything is built, let alone drawn: an appearance proxy is
        // read when a view is created, so a window already on screen would
        // keep the grey.
        Self.drawHintsInTheMutedInk()
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
    ///
    /// **The sRGB lives in ``HintInk`` and nowhere else.** Answer 65 needed
    /// this token as a `UIColor` for an appearance proxy, which meant writing
    /// it out in `DomovoiKit` as well; two copies of one token's literals is
    /// how a token drifts (review 1, N2), so this is that colour rather than a
    /// second spelling of it. ``HintInk``'s own doc comment carries the
    /// conversion from the stylesheet's `oklch` and the four measured ratios.
    static let fgMuted = Color(HintInk.ink)

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
    ///
    /// **This is an ink, not a fill.** A destructive button filled with it
    /// draws its own label at 3.08:1 in dark mode, which is why
    /// `DropInCallSheet` shipped `.bordered` with `.tint(Color.errInk)` until
    /// the pair existed; the fill is ``Color/danger`` with ``Color/dangerFg``
    /// on it, and that is what that button uses now. The note is
    /// here because there is no `Color.err` or `Color.errFill` to grep for —
    /// the stylesheet calls the pair `--danger`, so this target does too, and
    /// anyone looking for Android's `colors.err` lands on this token first.
    static let errInk = Color(UIColor { traits in
        traits.userInterfaceStyle == .dark
            ? UIColor(red: 1.0000, green: 0.3718, blue: 0.3577, alpha: 1)   // oklch(0.70 0.20 25)
            : UIColor(red: 0.7451, green: 0.1339, blue: 0.1637, alpha: 1)   // oklch(0.52 0.19 25)
    })

    /// `--danger`: the **fill** a destructive button is drawn in — hang up,
    /// delete, forget — with ``Color/dangerFg`` for the text on it.
    ///
    /// **The sRGB and the three ratios live in ``DangerInk`` and nowhere
    /// else**, the way ``Color/fgMuted`` is ``HintInk``. The first pass wrote
    /// them out here with the arithmetic in this comment and nothing
    /// recomputing it — the defect `HintInk`'s own stale 7.72:1 was (review 1,
    /// S6), found again in this token by review 3's S5. `ShellFoldTests`
    /// recomputes all three from the kit's constants now. That type's doc
    /// comment carries the reasoning: why the stylesheet's pair and not
    /// Android's white (white on the dark fill is 2.99:1), and why it is two
    /// tokens.
    ///
    /// Its one caller is `ios/Domovoi/DropIn/DropInCallSheet.swift`'s `leave`
    /// button — `.borderedProminent` with `.tint(Color.danger)` and the label
    /// in ``Color/dangerFg``, which is Android's filled `hang up`
    /// (`PhoneDropinCall.kt:180-183`). It had none on `wf/ios-b-shell`,
    /// because that file was the rooms unit's; the call site was typed on the
    /// batch's integration branch (shell's DECISIONS §3b, `INTEGRATION.md`).
    static let danger = Color(DangerInk.fill)

    /// `--danger-fg`: the text drawn **on** ``Color/danger``, the same way
    /// ``Color/brandFg`` is the text on the amber.
    ///
    /// Near-white on the light theme's deep red (**5.91:1**) and near-black on
    /// the dark theme's bright one (**6.53:1**). Never `.white` on both: that
    /// is the 2.99:1 the pair exists to avoid. Both numbers are
    /// ``DangerInk``'s, and recomputed there.
    ///
    /// **This declaration was missing.** The doc comment shipped on
    /// `wf/ios-b-shell` with no `static let` under it, and three other notes
    /// — ``Color/errInk``'s, ``Color/danger``'s and ``DangerInk``'s — all
    /// pointed at it by name. Nothing caught it because the token had no
    /// caller: `swift build` on the package never compiles this file, and
    /// `night/appcheck.sh`, which type-checks the app target, answered
    /// "DomovoiKit did not build" for every unit of the batch until its
    /// `-Xcc -isysroot` was fixed. The first thing it said once it ran was
    /// `type 'Color' has no member 'dangerFg'`.
    static let dangerFg = Color(DangerInk.text)
}

#Preview {
    FirstRunView()
}
