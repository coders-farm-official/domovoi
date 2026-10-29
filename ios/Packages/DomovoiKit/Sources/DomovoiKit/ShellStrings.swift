import Foundation

/// Every user-facing string of the shell and the pairing screens, verbatim
/// from the Kotlin.
///
/// A port of `ANDROID-SHELL-MAP.md` §7, which is itself read from the Kotlin
/// sources named against each group. **Both apps must say the same thing to
/// the same person**, so these are transcribed character for character, which
/// includes the characters that do not survive a careless copy: `—` U+2014 em
/// dash, `→` U+2192 rightwards arrow, `…` U+2026 ellipsis, `·` U+00B7 middle
/// dot, `“ ”` U+201C/201D curly quotes, and the handful with **two leading
/// spaces**, which are the chrome's spacing and not an accident.
///
/// `’` U+2019 is **not** in that list, although the map's §7 header had it:
/// no user-facing string in the fourteen Kotlin files has one. Every
/// apostrophe in the copy of both apps is ASCII `'`. See ``adminAttention``,
/// which is where that flag was and what it nearly caused.
///
/// `ShellStringsTests` reads the Kotlin files out of `android/` at test time
/// and fails if a string here is not in them, rather than comparing one copy
/// against another copy. An em dash typed as a hyphen is a real defect, and
/// that test is what catches it.
///
/// Strings that already live with the logic that chooses them are **not
/// repeated here**: §7.7's token-trouble card is in ``TokenTrouble``, §7.8's
/// field rules, hints and check messages are in ``TokenPasteField``, §7.3's
/// `reason`/`title` are on ``ServerChoice``, and §7.4/§7.5's banners and the
/// trust-again dialog are in `IdentityChanged.swift`. A string has one home.
public enum ShellStrings {

    // MARK: - §7.1 chrome (ui/shell/AppShell.kt)

    /// The local shell's top bar. **Two leading spaces.**
    public static let localTopbar = "  domovoi / local media"

    /// The server pill with no server saved, and its accessibility label.
    public static let connectPill = "connect"
    /// The pill's accessibility label when a server is saved.
    public static let switchServer = "switch server"

    /// The local shell's status beside the pill. **Two leading spaces each.**
    public static let statusAvailable = "  available"
    public static let statusOffline = "  offline"

    /// The workspace top bar's live dot. **Two leading spaces each.**
    public static let statusLive = "  live"

    /// The breadcrumb's home crumb, and the drawer's brand row.
    public static let homeCrumb = "domovoi"

    /// Beside the server name on a `legacy` verdict: trusted, nothing pinned.
    public static let unverified = "unverified"

    /// The drawer's brand row names the platform, and it is **the one visible
    /// string in the chrome that does**. Android's is `/ android`
    /// (`AppShell.kt:892`).
    ///
    /// **This is a choice, not a transcription**, and the report flags it as
    /// one for Kamron: `iphone` rather than `ios`, because every other string
    /// in both apps says "this phone" and "iphone" is the word a person uses.
    /// Nothing else in the app says which platform it is.
    public static let platformCrumb = "/ iphone"

    /// Drawer section labels.
    public static let drawerWorkspace = "workspace"
    public static let drawerSystem = "system"

    /// Icon and click accessibility labels.
    public static let themeLabel = "theme"
    public static let homeLabel = "home"

    /// The container banner's action, and its click label.
    public static let bannerDetails = "details"
    public static let bannerDetailsLabel = "container details"

    /// The container banner on a shared screen: a passer-by learns that an
    /// admin is needed, not which container or why
    /// (`alerts/ContainerIncidents.kt:43`, `ADMIN_ATTENTION_TEXT`).
    ///
    /// **The apostrophe is ASCII `'` (U+0027), and the Kotlin's is too.** This
    /// line used to be flagged `’`, which was wrong twice over: the string
    /// here is right, and the flag invited the next reader to "fix" it into a
    /// curly quote that the Kotlin does not have. The map taught it that
    /// (§7.1), and the map is corrected. There is no U+2019 in any
    /// user-facing string in the fourteen Kotlin files this port reads: the
    /// only one in them is in `searchWords`' strip list
    /// (`SettingsScreen.kt:164`), which removes both kinds of apostrophe
    /// before matching and is not shown to anyone.
    public static let adminAttention = "Something needs an admin's attention."

    /// A badge count above 99.
    public static let badgeOverflow = "99+"

    // MARK: - §7.2 the picker (ui/shell/ServerPicker.kt)

    /// **em dash.** Shown for ``NetworkWatch/onLan`` being false — a
    /// transport question, not an address one.
    public static let notOnWifi = "not on wifi — join your home network to scan, or add an address manually"

    /// `scanning 137/254…` — **the ellipsis character**, not three dots.
    public static func scanning(done: Int, total: Int) -> String { "scanning \(done)/\(total)…" }

    public static let noDomovoisFound = "no domovois found"
    public static let domovois = "domovois"
    public static let rescan = "rescan"

    /// **em dash.** The port is interpolated from ``Discovery/defaultPort``,
    /// as Android interpolates `Discovery.DEFAULT_PORT`.
    public static func nothingAnswered(port: Int = Discovery.defaultPort) -> String {
        "nothing answered on port \(port) — is the web backend running? you can still add an address below"
    }

    public static let addManually = "add manually"
    /// The manual field's placeholder. A literal in the Kotlin, port and all.
    public static let manualPlaceholder = "192.168.1.30:6369"
    public static let add = "add"
    /// **ellipsis.**
    public static let checking = "checking…"

    public static func couldNotReachDashboard(url: String) -> String {
        "couldn't reach a dashboard at \(url)"
    }

    public static let connected = "connected"
    public static let selected = "selected"
    public static let use = "use"
    public static let forget = "forget"

    /// The name shown for a server that advertised none.
    public static let unnamedServer = "domovoi"

    // MARK: - §7.3 the connection dialog (ui/shell/ServerPicker.kt)

    /// The dialog's title. "Connection" is dead as a user-facing word
    /// everywhere **except** here: the Settings section a person sees is
    /// `Phones & browsers`, and this pop-up is a different thing.
    public static let connectionTitle = "connection"

    public static let thisPhone = "this phone"
    public static let thisPhoneSub = "music, videos and files on the phone"
    public static let inUse = "in use"
    public static let close = "close"

    /// The click accessibility label is `use <title>`, so these are the two
    /// titles it is built from: ``thisPhone`` and ``ServerChoice/title``.
    public static func useLabel(_ title: String) -> String { "use \(title)" }

    // MARK: - §7.6 the trust dialog (ui/shell/ServerPicker.kt)

    public static let trustTitle = "trust this server?"
    public static let trustCancel = "cancel"
    public static let trustConfirm = "trust this server"

    /// The body, assembled: an optional first sentence naming the server,
    /// then the common part, then **one** tail.
    ///
    /// Each tail begins with **a leading space**, because the Kotlin
    /// concatenates the parts. The name is wrapped in **plain ASCII double
    /// quotes** (`\"$it\"` in the Kotlin), not curly ones — the one place in
    /// the copy that is deliberately ASCII.
    ///
    /// - Parameters:
    ///   - name: what the server calls itself, if it said. A blank name is
    ///     treated as none, as Kotlin's `takeIf { it.isNotBlank() }` does.
    ///   - offersIdentity: whether the server advertised an identity. The
    ///     tail that says nothing can be pinned is for an older web backend.
    public static func trustBody(name: String?, offersIdentity: Bool) -> String {
        var text = ""
        if let name, !ServerCredentials.isBlank(name) {
            text += "It calls itself \"\(name)\". "
        }
        text += "Check that this address is your Domovoi before you continue: the app "
            + "will load the screens this server advertises and send it your "
            + "requests. Nothing is saved until you confirm."
        if offersIdentity {
            text += " The identity above should match Settings → About & updates on the dashboard; "
                + "trusting pins it, and the household token goes out only after the "
                + "server proves it holds that key."
        } else {
            text += " This server offers no identity (an older web backend), so nothing "
                + "can be pinned: the app will not be able to tell it from another "
                + "server at the same address."
        }
        return text
    }

    // MARK: - §7.8 the pairing screen (ui/screens/settings/PairingScreen.kt)

    public static let pairTitle = "pair this phone"

    /// **em dash.**
    public static let pairExplanation = "This Domovoi asks devices to prove they belong to the household "
        + "before they can change anything. Paste the household token below — "
        + "once, for this phone."

    /// **`→` twice.**
    public static let pairHint = "An admin finds it on the dashboard under Settings → Phones & browsers → Household token."

    public static let notNow = "not now"
    public static let pairedToast = "this phone is paired"

    /// The field's placeholder.
    public static let householdTokenField = "household token"

    /// The button: `pair` with no token saved, `replace` with one
    /// (`ui/screens/settings/ConnectionPanel.kt:225`).
    public static let pair = "pair"
    public static let replace = "replace"

    // MARK: - §7.7 the token-trouble card's buttons (ui/shell/TokenTroubleScreen.kt)

    /// The card's own paste field is labelled differently from the pairing
    /// screen's. Its title, body, wait line and hint are in ``TokenTrouble``.
    public static let pairAgain = "pair again"
    public static let pairedAgainToast = "paired again"
    public static let tryOldTokenAgain = "try the old token again"
    public static let tryingOldTokenToast = "trying the old token once more"
    public static let useThisPhonesMedia = "use this phone's media"

    // MARK: - §7.9 Settings (ui/screens/settings/SettingsScreen.kt)

    public static let settingsTitle = "Settings"
    public static let settingsSub = "Everything Domovoi does, in groups. Search to jump straight to a setting."

    /// **middle dot and ellipsis.**
    public static let settingsSearchPlaceholder = "search settings · wake, voice history…"

    /// **curly quotes** around the trimmed query.
    public static func nothingMatches(_ query: String) -> String {
        "nothing matches “\(query.trimmingCharacters(in: .whitespacesAndNewlines))”"
    }

    public static let allSettings = "all settings"
    /// The back row inside a group: `back to Voice`.
    public static func backTo(_ label: String) -> String { "back to \(label)" }

    /// `DashboardPanel` — a section only the dashboard changes.
    public static let onTheDashboard = "On the dashboard"
    public static let onTheDashboardSub = "Changed on the Domovoi dashboard, not in this app."

    /// The first word is the section's label.
    public static func changedOnTheDashboard(section label: String) -> String {
        "\(label) is changed on the Domovoi dashboard, where the admin signs in, "
            + "so changes are handled properly. The dashboard runs at the same address "
            + "this app is connected to."
    }

    public static let openOnTheDashboard = "Open on the dashboard"
    /// **em dash.**
    public static let noServerForDashboard = "No server connected — add one under Phones & browsers first."
    public static let noBrowserForDashboard = "no browser found to open the dashboard"

    /// `LocalSettingsScreen` — the gear with no server.
    public static let localSettingsSub = "This phone's voice and its problem reports. Server settings appear once a Domovoi is connected."
    public static let back = "back"

    // MARK: - §7.9 the phone bar (ui/screens/settings/PhoneBarPanel.kt)

    public static let phoneBarTitle = "Phone bar"

    /// The numbers are interpolated from Android's `PhoneBar.MIN`/`MAX`, which
    /// are 2 and 5. `"everything"` uses **ASCII** double quotes.
    public static func phoneBarSub(min: Int = phoneBarMin, max: Int = phoneBarMax) -> String {
        "Home is always first; \(min) to \(max) tabs. Everything else is on Home's "
            + "\"everything\" grid. Kept on this phone only: another phone keeps its own."
    }

    public static let alwaysFirst = "always first"
    /// **Two leading spaces.**
    public static let defaultBar = "  default"

    /// The toast after `default`: the route labels, lowercased, joined with
    /// ` · ` (**middle dot**).
    public static func phoneBarBackTo(_ labels: [String]) -> String {
        "phone bar back to " + labels.map { $0.lowercased() }.joined(separator: " · ")
    }

    public static let barIsFull = "The bar is full"
    public static let addToTheBar = "Add to the bar"
    /// The number is written out in the Kotlin, not interpolated.
    public static let fiveTabsAtMost = "Five tabs at most: take one off to add another."
    public static let sharedScreenNote = "This is a shared screen: people, chat, files and news stay off it, whatever the bar says."

    public static func moveLeft(_ label: String) -> String { "move \(label) left" }
    public static func moveRight(_ label: String) -> String { "move \(label) right" }
    public static func takeOffTheBar(_ label: String) -> String { "take \(label) off the bar" }
    public static func addLabelToTheBar(_ label: String) -> String { "add \(label) to the bar" }

    /// Android's `PhoneBar.MIN` and `MAX` (`ui/shell/PhoneBar.kt:21-22`).
    public static let phoneBarMin = 2
    public static let phoneBarMax = 5

    // MARK: - §7.9 a hidden route's placeholder (ui/shell/RouteNotes.kt)

    /// Why a route a link reached is not shown here: the title and the line
    /// under it. Chat hidden because the server's AI features are off says
    /// so; anything else is a plugin that is not installed.
    ///
    /// `label` is the route's label; the title lowercases it, as the Kotlin
    /// does.
    public static func unavailableNote(label: String, isChat: Bool, aiEnabled: Bool) -> (title: String, detail: String) {
        if isChat && !aiEnabled {
            return ("\(label.lowercased()) is turned off", "this server has its AI features turned off")
        }
        return ("\(label.lowercased()) isn't available", "the plugin providing this screen isn't installed on this server")
    }

    // MARK: - §6/§7.9 Settings → Phones & browsers (ui/screens/settings/ConnectionPanel.kt)

    /// The three card titles this batch draws. The section's own subtitle is
    /// ``SettingsSection/sub`` for `phones-and-browsers`, not repeated here.
    public static let serverCard = "Server"
    public static let householdTokenCard = "Household token"
    public static let trustedServersCard = "Trusted servers"

    /// **em dash.**
    public static let serverCardSub =
        "The Domovoi web backend this app talks to — usually http://<domovoi-ip>:6369 on your LAN."

    public static let householdTokenCardSub =
        "What this phone presents so the server knows it belongs here. "
        + "An admin finds it on the dashboard under Settings → Phones & browsers."

    /// **em dash.**
    public static let trustedServersCardSub =
        "The Domovois this phone will connect to without asking again. Forgetting one also "
        + "forgets its household token and its pinned identity; forgetting the one you are "
        + "connected to returns you to the server list — the way to re-pair after a server "
        + "was reinstalled or replaced."

    /// The live socket, which is **not** the same question as the picker's
    /// `connected` pill: this one is `bus.connected` and that one is the
    /// reachability-and-identity verdict. Both read "connected" to a person,
    /// and §11.6 is about exactly that.
    public static let notConnected = "not connected"

    public static let serverURLPlaceholder = "http://192.168.1.10:6369"
    public static let saveAndReconnect = "Save & reconnect"

    public static let paired = "paired"
    public static let notPaired = "not paired"

    public static let pairedExplanation =
        "This phone sends the household token with every request to this server, "
        + "and to nothing else. Paste a new one here after an admin rotates it."

    /// It forgets **only** the token — not the trust, not the pin, not the
    /// server.
    public static let forgetTheToken = "forget the token"

    public static let identityLabel = "identity"
    public static let pinThisIdentity = "pin this identity"

    /// Under a trusted row with no pin. A normal, visible state.
    public static let identityNotPinnedYet = "identity not pinned yet"

    /// The trusted-servers card with nothing in it.
    public static let noTrustedServersYet = "none yet"

    // MARK: - §7.10 toasts

    /// Toasts are bottom-centre pills, one at a time off the head of a list,
    /// auto-dismissed after ``toastMs``.
    public static let toastMs: Int64 = 2400

    /// Forgetting the **active** server leaves the app with none and asks for
    /// the list. **em dash.**
    public static func forgotActive(address: String) -> String { "forgot \(address) — pick a server" }
    /// Forgetting any other saved server.
    public static func forgot(address: String) -> String { "forgot \(address)" }

    public static let noLongerPaired = "this phone is no longer paired"
    public static func pinned(fingerprint: String) -> String { "pinned \(fingerprint)" }
    /// **em dash.**
    public static let serverSaved = "server saved — reconnecting"
    public static let couldNotSwitch = "couldn't switch to that server"

    public static func deviceRenamed(to name: String) -> String { "this device is now \"\(name)\"" }
    public static func renameFailed(_ message: String) -> String { "rename failed: \(message)" }
    public static let nameCannotBeBlank = "name can't be blank"

    public static func belongsTo(_ name: String) -> String { "this phone belongs to \(name)" }
    public static let belongsToNobody = "this phone belongs to nobody in particular"

    public static func listeningAs(_ name: String) -> String { "listening as \(name)" }
    public static let listeningAsThisDevice = "listening as this device"
}
