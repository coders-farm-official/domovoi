import Foundation

/// The main navigation's groups — Chat, then **Media** and **Tools** — and
/// the one resolver that turns a list of launcher routes into them.
///
/// A port of the nav regroup in `android/.../ui/shell/Routes.kt` (commit
/// adb2ad06, `NavKeys` … `NavGroupsOpen`, Routes.kt:166-353 at 5d2b1807) and
/// of `tileRuns` (`ui/screens/home/HomeModels.kt:709-731`), which mirror the
/// dashboard's sidebar. One table (``defaultNav``), one resolver
/// (``navTree(_:override:defaults:pluginGroups:)``); every launcher that
/// draws groups asks it — the iPad sidebar (Android's drawer) and Home's
/// "everything" grid (its headings). The phone bar stays flat, as Android's
/// does.
///
/// **People is not in any of this**: it is Settings → Household → People
/// since the regroup (``settingsTabFor(_:)``), so it left ``workspaceRoutes``.

// MARK: - the web's route keys

/// Each route's key in the navigation's data — the web's route strings
/// (`#music`, …; the plugin pages by their web page key: Stations is the radio
/// plugin's `"stations"`, Images the image generation plugin's `"imagegen"`).
/// What a ``NavOverride`` names. The one place a ``Route`` maps to its key
/// (`Routes.kt` `NavKeys`).
public let navKeys: [Route: String] = [
    .chat: "chat",
    .music: "music",
    .podcasts: "podcasts",
    .audiobooks: "audiobooks",
    .videos: "videos",
    .images: "imagegen",
    .stations: "stations",
    .news: "news",
    .calendar: "calendar",
    .files: "files",
    .home: "home",
    .people: "people",
    .settings: "settings",
    .manual: "manual",
]

/// The route a nav key names, or nil for one this build does not know
/// (`routeForNavKey`).
public func routeForNavKey(_ key: String) -> Route? {
    navKeys.first { $0.value == key }?.key
}

// MARK: - the groups

/// A navigation group as a launcher shows it: its id (the key its open or
/// shut state is kept under), label and symbol, and whether it starts open
/// (`NavGroupInfo`).
///
/// The symbol is a choice, as ``Route/systemImage`` is: Android's Material
/// `PermMedia` (Media), `Build` (Tools) and `Apps` (a group a person made)
/// have no SF Symbol counterpart by name.
public struct NavGroupInfo: Equatable, Hashable, Sendable {
    public let id: String
    public let label: String
    public let systemImage: String
    public let openByDefault: Bool

    public init(
        id: String,
        label: String,
        systemImage: String = "square.grid.2x2",
        openByDefault: Bool = false
    ) {
        self.id = id
        self.label = label
        self.systemImage = systemImage
        self.openByDefault = openByDefault
    }
}

/// The main navigation's default groups and which core route sits in which:
/// one table, so a rename is a one-word change here (`NavDefaults`).
public struct NavDefaults: Equatable, Sendable {
    public let groups: [NavGroupInfo]
    public let groupOf: [Route: String]

    public init(groups: [NavGroupInfo], groupOf: [Route: String]) {
        self.groups = groups
        self.groupOf = groupOf
    }
}

/// Media open at first, Tools shut — the dashboard's and Android's
/// `DefaultNav`.
public let defaultNav = NavDefaults(
    groups: [
        NavGroupInfo(id: "media", label: "Media", systemImage: "play.rectangle.on.rectangle", openByDefault: true),
        NavGroupInfo(id: "tools", label: "Tools", systemImage: "wrench.and.screwdriver"),
    ],
    groupOf: [
        .music: "media",
        .podcasts: "media",
        .audiobooks: "media",
        .videos: "media",
        .news: "tools",
        .calendar: "tools",
        .files: "tools",
    ]
)

/// The groups plugin pages declare (the web manifest's `nav_group`,
/// `"media"` or `"tools"`; anything else is top level). This app lists no
/// plugin pages — its two capability-backed routes are compiled in — so they
/// say theirs here, as data: Stations (radio) and Images (image generation)
/// are Media (`PluginNavGroups`).
public let pluginNavGroups: [Route: String] = [
    .stations: "media",
    .images: "media",
]

/// A person's own sidebar, the web's JSON `{groups:[{id,label,items}],
/// top:[...]}`; items are ``navKeys`` values. Nothing edits it yet
/// (`NavOverride`).
public struct NavOverride: Equatable, Sendable {
    public let groups: [NavOverrideGroup]
    public let top: [String]

    public init(groups: [NavOverrideGroup] = [], top: [String] = []) {
        self.groups = groups
        self.top = top
    }
}

public struct NavOverrideGroup: Equatable, Sendable {
    public let id: String
    public let label: String
    public let items: [String]

    public init(id: String, label: String, items: [String] = []) {
        self.id = id
        self.label = label
        self.items = items
    }
}

/// One line of the grouped navigation: a route on its own, or a group of two
/// or more routes (`NavEntry`).
public enum NavEntry: Equatable, Hashable, Sendable {
    case item(Route)
    case group(NavGroupInfo, [Route])
}

/// The one resolver for the grouped navigation — the web's rules, as
/// Android's `navTree` writes them.
///
/// - `visible`: launcher routes already filtered (``Route/visibleOn(_:)``:
///   capabilities, shared screen), in their default order, which is each
///   route's sort position.
/// - `defaults`: the default groups; `pluginGroups`: the groups plugin pages
///   declare; `override`: a person's own sidebar, or nil.
///
/// No override: the default groups, each route in its default (or
/// plugin-declared) group. An override: its groups (and labels) are the
/// groups, and a default group it leaves out is gone. A route listed in a
/// group goes there, in the listed order; one listed in `top` is top level
/// (groups are read first, and a route's first mention wins). Unknown keys and
/// routes not visible are ignored. A route the override does not mention goes
/// to its default group if that group exists, after the listed ones, else top
/// level. Top-level routes and groups are ordered by sort position, a group at
/// its earliest member's. Then a group with one route is that route, flat, and
/// a group with none is not there.
public func navTree(
    _ visible: [Route],
    override: NavOverride? = nil,
    defaults: NavDefaults = defaultNav,
    pluginGroups: [Route: String] = pluginNavGroups
) -> [NavEntry] {
    var routes: [Route] = []
    for route in visible where !routes.contains(route) { routes.append(route) }
    var position: [Route: Int] = [:]
    for (index, route) in routes.enumerated() { position[route] = index }

    let groups: [NavGroupInfo]
    if let override {
        var seen: Set<String> = []
        groups = override.groups.compactMap { g in
            guard seen.insert(g.id).inserted else { return nil }
            if let base = defaults.groups.first(where: { $0.id == g.id }) {
                return NavGroupInfo(
                    id: base.id,
                    label: g.label,
                    systemImage: base.systemImage,
                    openByDefault: base.openByDefault
                )
            }
            return NavGroupInfo(id: g.id, label: g.label)
        }
    } else {
        groups = defaults.groups
    }

    var members: [String: [Route]] = [:]
    for g in groups { members[g.id] = [] }
    var top: [Route] = []
    var placed: Set<Route> = []

    if let override {
        for g in override.groups {
            guard members[g.id] != nil else { continue }
            for key in g.items {
                guard let r = routeForNavKey(key), position[r] != nil, placed.insert(r).inserted else { continue }
                members[g.id, default: []].append(r)
            }
        }
        for key in override.top {
            guard let r = routeForNavKey(key), position[r] != nil, placed.insert(r).inserted else { continue }
            top.append(r)
        }
    }
    for r in routes where !placed.contains(r) {
        if let id = defaults.groupOf[r] ?? pluginGroups[r], members[id] != nil {
            members[id, default: []].append(r)
        } else {
            top.append(r)
        }
    }

    var lines: [(Int, NavEntry)] = []
    for r in top {
        if let at = position[r] { lines.append((at, NavEntry.item(r))) }
    }
    for g in groups {
        let rs = members[g.id] ?? []
        switch rs.count {
        case 0:
            continue
        case 1:
            if let only = rs.first, let at = position[only] { lines.append((at, .item(only))) }
        default:
            let at = rs.compactMap { position[$0] }.min() ?? 0
            lines.append((at, .group(g, rs)))
        }
    }
    // Stable: two lines never share a position (each route is placed once).
    return lines.sorted { $0.0 < $1.0 }.map(\.1)
}

// MARK: - which groups are open

/// Which groups are open in the sidebar, as this device keeps them
/// (`"media=1,tools=0"`; Android's Prefs `nav_groups_open`, the web's
/// localStorage). A group never toggled is ``NavGroupInfo/openByDefault``;
/// arriving on a page opens its group (``openedFor(_:tree:state:)``).
/// Unreadable parts, and ids no group has any more (the retired `listen`,
/// `view`, `play`), are ignored, never fatal (`NavGroupsOpen`).
public enum NavGroupsOpen {

    /// Where the sidebar keeps it: per device, never synced.
    public static let storageKey = "domovoi.nav.groups.open"

    public static func decode(_ raw: String?) -> [String: Bool] {
        var out: [String: Bool] = [:]
        for part in (raw ?? "").split(separator: ",", omittingEmptySubsequences: false) {
            let kv = part.split(separator: "=", omittingEmptySubsequences: false)
            guard kv.count == 2 else { continue }
            let key = kv[0].trimmingCharacters(in: .whitespaces)
            guard !key.isEmpty else { continue }
            switch kv[1].trimmingCharacters(in: .whitespaces) {
            case "1": out[key] = true
            case "0": out[key] = false
            default: continue
            }
        }
        return out
    }

    public static func encode(_ state: [String: Bool]) -> String {
        state.keys.sorted().map { "\($0)=\(state[$0] == true ? 1 : 0)" }.joined(separator: ",")
    }

    public static func isOpen(_ group: NavGroupInfo, _ state: [String: Bool]) -> Bool {
        state[group.id] ?? group.openByDefault
    }

    public static func toggled(_ group: NavGroupInfo, _ state: [String: Bool]) -> [String: Bool] {
        var next = state
        next[group.id] = !isOpen(group, state)
        return next
    }

    /// `state` with the group of `tree` holding `route` open (a group
    /// containing the current page is open); the same map when it already
    /// is, or when the route is in no group.
    public static func openedFor(_ route: Route, tree: [NavEntry], state: [String: Bool]) -> [String: Bool] {
        for entry in tree {
            guard case .group(let group, let routes) = entry, routes.contains(route) else { continue }
            if isOpen(group, state) { return state }
            var next = state
            next[group.id] = true
            return next
        }
        return state
    }
}

// MARK: - Home's grid, under the same headings

/// A run of the grid's tiles: under a group's heading, or (nil) under none
/// (`TileRun`, `HomeModels.kt:710`).
public struct TileRun: Equatable, Sendable {
    public let group: NavGroupInfo?
    public let routes: [Route]

    public init(group: NavGroupInfo?, routes: [Route]) {
        self.group = group
        self.routes = routes
    }
}

/// The grid's `tiles` under the sidebar's group headings (``navTree``): each
/// group with two or more tiles left gets its heading; a lone tile (its
/// group's others on the bar) and the top-level ones (Chat, Settings, the
/// manual) join the heading-less run beside them (`tileRuns`,
/// `HomeModels.kt:719`).
public func tileRuns(_ tiles: [Route]) -> [TileRun] {
    var out: [TileRun] = []
    for entry in navTree(tiles) {
        switch entry {
        case .group(let group, let routes):
            out.append(TileRun(group: group, routes: routes))
        case .item(let route):
            if let last = out.last, last.group == nil {
                out[out.count - 1] = TileRun(group: nil, routes: last.routes + [route])
            } else {
                out.append(TileRun(group: nil, routes: [route]))
            }
        }
    }
    return out
}
