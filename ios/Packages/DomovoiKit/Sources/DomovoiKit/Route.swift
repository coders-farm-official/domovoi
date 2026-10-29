import Foundation

/// The route table: every destination the app has, what it is called, and
/// which of them a launcher offers.
///
/// A port of `android/.../ui/shell/Routes.kt` (map §1.2-§1.6), read on
/// 2026-10-29. The table is compiled in on both platforms; only *visibility*
/// is data-driven, from the server's capability manifest (design §8).
///
/// **The case order is the Kotlin's declaration order**, because
/// ``Route/allCases`` stands in for Kotlin's `Route.entries` in the
/// invariants ported from `RoutesTest`, and two of them compare whole lists.
///
/// The raw value is the Kotlin **enum name** — `"Home"`, `"Satellites"` —
/// which is what a saved phone bar stores and what a link names. It is not
/// what is drawn: see ``label``, which diverges for one route.
public enum Route: String, CaseIterable, Sendable {
    case home = "Home"
    case chat = "Chat"
    case music = "Music"
    case podcasts = "Podcasts"
    case audiobooks = "Audiobooks"
    case videos = "Videos"
    case images = "Images"
    case news = "News"
    case people = "People"
    /// Not a screen of its own since 2026-10-11: navigating here opens
    /// Settings on its Rooms section (``settingsTabFor(_:)``). The route
    /// survives as the name a link uses.
    case satellites = "Satellites"
    case calendar = "Calendar"
    case stations = "Stations"
    case files = "Files"
    case settings = "Settings"
    case manual = "Manual"
    /// The container guard's details, part of Settings → Health since
    /// 2026-10-21. On no launcher.
    case containers = "Containers"

    /// The Kotlin enum name: what a saved bar stores and a link names.
    public var name: String { rawValue }

    /// What is drawn. **The name and the label diverge for `satellites`**,
    /// which a person sees as `Rooms` (`Routes.kt:55`).
    public var label: String {
        self == .satellites ? "Rooms" : rawValue
    }

    /// The SF Symbol drawn for this route.
    ///
    /// **This is a choice, not a transcription.** Android's `icon` is a
    /// Material icon (`Routes.kt:41-68`) and there is no mapping from those
    /// names to SF Symbols, so each one here is the nearest symbol Apple
    /// ships, picked against the Material glyph: `CellTower` →
    /// `dot.radiowaves.left.and.right`, `Radio` →
    /// `antenna.radiowaves.left.and.right`, `Image` (the generation screen) →
    /// `wand.and.stars`, `HelpOutline` → `questionmark.circle`.
    ///
    /// A name that does not exist draws **nothing at all** rather than
    /// failing, and nothing in this package can see that. The report lists it
    /// as a Simulator check.
    public var systemImage: String {
        switch self {
        case .home: return "house.fill"
        case .chat: return "bubble.left.fill"
        case .music: return "music.note"
        case .podcasts: return "mic.fill"
        case .audiobooks: return "book.fill"
        case .videos: return "film.fill"
        case .images: return "wand.and.stars"
        case .news: return "newspaper.fill"
        case .people: return "person.2.fill"
        case .satellites: return "dot.radiowaves.left.and.right"
        case .calendar: return "calendar"
        case .stations: return "antenna.radiowaves.left.and.right"
        case .files: return "folder.fill"
        case .settings: return "gearshape.fill"
        case .manual: return "questionmark.circle"
        case .containers: return "shield.fill"
        }
    }

    /// The capability this route needs before it renders; nil = always.
    /// Only the two plugin-backed screens are gated (`Routes.kt:76`).
    public var requiredCapability: String? {
        switch self {
        case .stations: return capStations
        case .images: return capImagegen
        default: return nil
        }
    }
}

/// Where the app opens, and where it lands when a route stops existing.
public let startRoute: Route = .home

/// Routes no launcher offers — not the phone bar, not the sidebar, not
/// Home's grid (`Routes.kt:126`).
public let launcherlessRoutes: Set<Route> = [.containers, .satellites]

/// Pages a shared screen (the kitchen tablet) leaves off every launcher
/// (`Routes.kt:115`), the web's `SHARED_SCREEN_HIDDEN`.
///
/// **Presentational only**, on both platforms: the screens still render if
/// something navigates to them, and the device still holds the household
/// token. ``ShellStrings/sharedScreenNote`` is what a person is told.
public let sharedScreenHidden: Set<Route> = [.people, .chat, .files, .news]

/// The web sidebar's "workspace" section, in order. Home, Settings and the
/// manual are deliberately out of it (`Routes.kt:141`).
public let workspaceRoutes: [Route] = [
    .chat, .music, .podcasts, .audiobooks, .videos,
    .images, .news, .people, .calendar,
    .stations, .files,
]

/// The phone bar's default, left to right: home · music · videos · chat
/// (`Routes.kt:148`, and Kamron's decision of 2026-10 that the bar is these
/// four and is customisable).
public let defaultPhoneBar: [Route] = [.home, .music, .videos, .chat]

/// Every destination Home's "everything" grid may hold — the compact
/// width's "more" (`Routes.kt:160`).
public let everythingRoutes: [Route] = [
    .chat, .music, .podcasts, .audiobooks, .videos,
    .images, .news, .people, .calendar, .files,
    .stations, .settings, .manual,
]

/// Routes that are really a Settings section, by that section's key
/// (`Routes.kt:131`).
///
/// `navigate(.satellites)` therefore opens **Settings**, on its Rooms
/// section — it does not push a Rooms screen. This is the
/// "Satellites moved into Settings" change of 2026-10-11, and on iOS it
/// means Rooms is a row inside Settings and never a tab.
public func settingsTabFor(_ route: Route) -> String? {
    switch route {
    case .satellites: return "rooms"
    case .containers: return "health"
    default: return nil
    }
}

// MARK: - visibility (map §1.6)

/// Everything the three visibility filters need, in one value.
///
/// Android reads the third from a **mutable global** that its tests
/// overwrite (`internal var onDeviceAssistant`, `Routes.kt:84`). That is not
/// portable: a `var` at file scope is not `Sendable` under strict
/// concurrency, and a global a test mutates is a global production code can
/// mutate. It is a parameter here, with **no default**, so a caller cannot
/// forget which answer it is asking about — the same reasoning as part 2's
/// non-optional ``DomovoiClient/gate``.
public struct RouteVisibility: Equatable, Sendable {
    /// What the connected server says it can do; ``Capabilities/empty`` when
    /// there is no manifest, which hides both gated screens.
    public let capabilities: Capabilities

    /// Whether this install is a shared screen (the device-registration
    /// reply; ``DeviceRegistry/isSharedScreen(answer:paired:)``). **Until the
    /// server answers, a paired install is treated as shared.**
    public let sharedScreen: Bool

    /// Whether this phone can start a chat on itself: Android's
    /// `AssistantSupport.phoneCanChat`.
    ///
    /// **There is no on-device engine on iOS yet** (the assistant is a later
    /// milestone), so the app passes `false` today and Chat follows the
    /// server's AI switch alone. When the engine lands, this is the one input
    /// that changes.
    public let phoneCanChat: Bool

    public init(capabilities: Capabilities, sharedScreen: Bool, phoneCanChat: Bool) {
        self.capabilities = capabilities
        self.sharedScreen = sharedScreen
        self.phoneCanChat = phoneCanChat
    }
}

/// Whether Chat is offered at all: at least one place can start a chat
/// (`Routes.kt:95`).
///
/// The server can while its AI features are on; this phone can where it has
/// the engine. A server that says nothing about AI counts as **on**
/// (``Capabilities/aiEnabled``), so an older server and a manifest that
/// failed to load both keep Chat.
public func chatAvailable(_ capabilities: Capabilities, phoneCanChat: Bool) -> Bool {
    capabilities.aiEnabled || phoneCanChat
}

public extension Route {

    /// The capability manifest allows this route, and Chat has somewhere to
    /// chat (`Routes.kt:100`).
    ///
    /// This is the filter the **open route** is checked against: a route that
    /// loses its capability while you are on it sends the shell home
    /// (``NavigationState/capabilitiesChanged(_:)``). The shared-screen
    /// filter is deliberately *not* part of it — that one is presentational,
    /// and a shared screen that is handed People by a link still draws it.
    func visibleWith(_ visibility: RouteVisibility) -> Bool {
        if let needed = requiredCapability, !visibility.capabilities.has(needed) { return false }
        if self == .chat, !chatAvailable(visibility.capabilities, phoneCanChat: visibility.phoneCanChat) {
            return false
        }
        return true
    }

    /// Whether a **launcher** should offer this route: the capability
    /// manifest allows it and, on a shared screen, it is not somebody's own
    /// page (`Routes.kt:119`).
    func visibleOn(_ visibility: RouteVisibility) -> Bool {
        visibleWith(visibility) && !(visibility.sharedScreen && sharedScreenHidden.contains(self))
    }

    /// Why a route a link reached is not shown, when it is not
    /// (``ShellStrings/unavailableNote(label:isChat:aiEnabled:)``).
    func unavailableNote(_ capabilities: Capabilities) -> (title: String, detail: String) {
        ShellStrings.unavailableNote(
            label: label,
            isChat: self == .chat,
            aiEnabled: capabilities.aiEnabled
        )
    }
}

/// Home's "everything" grid: every screen that is not on this phone's bar,
/// that this server allows, and — on a shared screen — that is not somebody's
/// own (`ui/screens/home/HomeModels.kt:699`).
///
/// Satellites is not here and neither is Containers: they are Settings
/// sections (``launcherlessRoutes``).
public func everythingTiles(_ visibility: RouteVisibility, bar: [Route]) -> [Route] {
    everythingRoutes.filter { !bar.contains($0) && $0.visibleOn(visibility) }
}

/// The local shell's tabs, left to right: the phone's own music, videos and
/// files, the saved audiobooks once there are any, and Chat where this
/// install has the engine (`Routes.kt:186`).
///
/// A shared screen leaves off what the workspace's launchers leave off
/// (delta review N7): its own files and the chats kept on it.
public func localTabs(
    phoneCanChat: Bool,
    sharedScreen: Bool,
    savedBooks: Bool
) -> [Route] {
    var tabs: [Route] = [.music, .videos, .files]
    if savedBooks { tabs.append(.audiobooks) }
    if phoneCanChat { tabs.append(.chat) }
    return tabs.filter { !(sharedScreen && sharedScreenHidden.contains($0)) }
}

// MARK: - a route asked for from outside the app (map §1.5)

public extension Route {

    /// The route a name from outside the UI stands for — a notification tap,
    /// a deep link — or nil.
    ///
    /// The names are Android's (`MainActivity.kt:151`, `"home"`,
    /// `CONTAINERS_ROUTE`, `CHAT_ROUTE`) and the allow-list is
    /// ``AppContainer/routesFromOutside``: **a name outside it has no route
    /// here at all**, so even a caller that bypasses the container cannot
    /// name an arbitrary screen.
    static func fromOutside(_ name: String) -> Route? {
        guard AppContainer.routesFromOutside.contains(name) else { return nil }
        switch name {
        case "home": return .home
        case "chat": return .chat
        case "containers": return .containers
        default: return nil
        }
    }
}
