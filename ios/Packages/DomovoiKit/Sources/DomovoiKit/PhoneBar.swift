import Foundation

/// The phone bar: the tabs along the bottom of **this** phone.
///
/// A port of `android/.../ui/shell/PhoneBar.kt` (map §1.7), read on
/// 2026-10-29. Kamron's decision of 2026-10 stands on both platforms: the bar
/// is `home · music · videos · chat` and it is customisable.
///
/// The rules, each of which `PhoneBarTest.kt` pins:
///
///  - **Home is slot 1, always**, and cannot move or be removed. Every
///    answer here begins with it and every edit strips it out, works on the
///    rest, and prepends it again.
///  - **Slots 2…5 are the person's**, from ``choices`` — Home plus
///    ``workspaceRoutes``. Settings, the manual, Rooms and Containers are
///    never eligible.
///  - **Two to five tabs.** A saved bar left with fewer than ``min`` after
///    filtering falls back to the *default*, re-filtered.
///  - **Filtering happens every time the bar is drawn**, not at save time,
///    so the same saved bar yields different tabs on different servers and a
///    shared screen never shows a personal tab whatever was saved.
///  - **It is per install and never synced**: "Kept on this phone only:
///    another phone keeps its own" (``ShellStrings/phoneBarSub(min:max:)``).
///
/// **Android's one migration is deliberately not ported.** `PhoneBar.migrate`
/// turns a bar saved with the retired `"Assistant"` tab into `"Chat"`, once.
/// iOS has never shipped an Assistant tab, so there is no saved bar for it to
/// be about. What *is* ported is the rule that makes skipping it safe:
/// ``decode(_:)`` drops a name this build does not know, so a bar carrying
/// `"Assistant"` loses that one tab rather than failing to load — and
/// `testAnAssistantTabIsJustAnUnknownNameHere` is that claim, checked.
public enum PhoneBar {

    /// Android's `PhoneBar.MAX`/`MIN` (`PhoneBar.kt:21-22`). The user-facing
    /// numbers in ``ShellStrings/phoneBarSub(min:max:)`` are interpolated
    /// from ``ShellStrings/phoneBarMin``/``ShellStrings/phoneBarMax``, which
    /// `testTheLimitsAreTheOnesTheWordsPromise` holds to these.
    public static let max = 5
    public static let min = 2

    /// The screens that may sit on the bar, in the sidebar's order.
    public static let choices: [Route] = [.home] + workspaceRoutes

    /// The tabs, left to right, for a saved bar (`nil` = never saved, or
    /// unreadable) on a server with these capabilities (`PhoneBar.kt:74`).
    public static func routes(saved: [Route]?, visibility: RouteVisibility) -> [Route] {
        func pick(_ list: [Route]) -> [Route] {
            var out: [Route] = [.home]
            for route in list {
                if out.count >= max { break }
                if route != .home,
                   choices.contains(route),
                   !out.contains(route),
                   route.visibleOn(visibility) {
                    out.append(route)
                }
            }
            return out
        }
        if let saved {
            let mine = pick(saved)
            if mine.count >= min { return mine }
        }
        return pick(defaultPhoneBar)
    }

    /// Route names as the store keeps them: a JSON array, `["Home","Music"]`
    /// (`PhoneBar.encode`).
    public static func encode(_ routes: [Route]) -> String {
        let names = routes.map(\.name)
        guard let data = try? JSONEncoder().encode(names),
              let text = String(data: data, encoding: .utf8)
        else {
            // Unreachable: `[String]` always encodes. Answering the default
            // rather than trapping, because a bar is not worth a crash.
            return "[]"
        }
        return text
    }

    /// A saved bar, or nil (never saved, or unreadable — both of which mean
    /// the default). **Names this build does not know are dropped**, which is
    /// what makes a renamed or retired route harmless (`PhoneBar.decode`).
    public static func decode(_ raw: String?) -> [Route]? {
        guard let raw, !ServerCredentials.isBlank(raw) else { return nil }
        guard let data = raw.data(using: .utf8),
              let names = try? JSONDecoder().decode([String].self, from: data)
        else { return nil }
        return names.compactMap { Route(rawValue: $0) }
    }

    /// Whether `tab` is the lit one, with `route` open (`AppShell.kt:796`).
    ///
    /// ```kotlin
    /// val selected = route == r || (r == Route.Home && route !in bar)
    /// ```
    ///
    /// **While a screen that is not on the bar is open — one reached from
    /// Home's grid — the home tab is the one lit**, and there is no "More"
    /// tab on either platform. Without the second clause the bar would show
    /// nothing selected, which reads as a broken bar rather than as "you are
    /// somewhere else".
    public static func selects(tab: Route, route: Route, bar: [Route]) -> Bool {
        route == tab || (tab == .home && !bar.contains(route))
    }

    /// One edit to the bar. Home never moves or goes, and the limits hold: an
    /// edit past them is no edit, and the same list comes back
    /// (`PhoneBar.edit`).
    public enum Op: Sendable { case add, remove, left, right }

    public static func edit(_ current: [Route], _ op: Op, _ route: Route) -> [Route] {
        var rest = current.filter { $0 != .home }
        let i = rest.firstIndex(of: route)
        // Kotlin's `size`: the whole bar, Home included.
        let size = rest.count + 1
        switch op {
        case .add:
            guard route != .home, i == nil, choices.contains(route), size < max else { return current }
            rest.append(route)
        case .remove:
            guard let i, size > min else { return current }
            rest.remove(at: i)
        case .left, .right:
            guard let i else { return current }
            let j = op == .left ? i - 1 : i + 1
            guard j >= 0, j < rest.count else { return current }
            rest.swapAt(i, j)
        }
        return [.home] + rest
    }
}
