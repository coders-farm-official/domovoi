import Foundation

/// Load as you scroll: the part of it the **shell** owns.
///
/// Owner decision of 2026-10-11, standing for both apps and every list: no
/// files-like list — Music, Videos, Files, Audiobooks, Podcasts, and the
/// phone's own lists — ever holds a whole list. It is not a per-screen choice,
/// which is why it is here rather than waiting for the first screen that
/// scrolls.
///
/// The `Pager` itself, the server's `limit`/`cursor` contract and the
/// re-read-on-event wiring are `android/.../net/Paging.kt` and belong with the
/// first paged screen (I1-D or later). What this batch owes map §1.9 is the
/// three facts that are about **navigation**:
///
/// 1. **A route holds nothing.** Android's pager is created inside the screen
///    by `rememberPager` (`ui/components/Paged.kt:48`) and dies with it, so
///    leaving a route and coming back re-reads page one. There is no cached
///    list behind a route, here or there: ``NavigationState`` carries a
///    ``Route`` and nothing else, and a destination that wants rows asks for
///    them when it appears.
/// 2. **A server switch resets every list.** Android's
///    `LaunchedEffect(serverUrl, *keys) { pager.reset() }` (`Paged.kt:60`).
///    The counterpart here is that a list's state is owned by the screen,
///    inside the per-server state the shell keeps, so a switch starts clean.
/// 3. **Asking for the next page is driven from the list's own state**, not
///    from the server, and it needs the guard below.
public enum Paging {

    /// `net/Paging.kt:32-36`. The server's own contract, repeated here so the
    /// first paged screen does not have to invent them.
    public static let defaultPageSize = 50
    /// The server's hard maximum for one page.
    public static let serverMaxPage = 200
    /// A re-read covers at most this many rows; past it, start over.
    public static let reloadCap = 1000

    /// Android's `LoadMoreNearEnd` thresholds: 8 rows for a list, 12 for a
    /// grid (`Paged.kt:89`, `:102`).
    public static let listThreshold = 8
    public static let gridThreshold = 12

    /// Whether the last visible row is within `threshold` of the end, and so
    /// whether to ask for the next page (`Paged.kt:79`, `nearEnd`).
    ///
    /// ### The guard, which is the whole reason this is a function
    ///
    /// `totalItems >= 2 * threshold` is not tidiness. A screen still showing
    /// its header and a spinner has two or three "rows", the last of which is
    /// trivially within eight of the end — so it read as *near the end* and
    /// asked for page two before anyone had scrolled. Seen on Music,
    /// 2026-10-12; map §1.9 says to port it, and
    /// `testAScreenShowingASpinnerIsNotNearItsEnd` is that port.
    ///
    /// **The iOS shape of the same trap is `onAppear` on the last row**, which
    /// fires for a one-row list exactly as that recomposition did. A screen
    /// that asks this function instead of trusting `onAppear` cannot fall into
    /// it.
    public static func nearEnd(totalItems: Int, lastVisible: Int, threshold: Int) -> Bool {
        totalItems >= 2 * threshold && lastVisible >= totalItems - 1 - threshold
    }
}
