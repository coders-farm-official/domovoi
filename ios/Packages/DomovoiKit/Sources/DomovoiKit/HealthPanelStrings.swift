import Foundation

// ---------------------------------------------------------------------------
// Settings → Health's own words, ported character for character from
// `ui/screens/containers/ContainersScreen.kt` (375 lines) and
// `alerts/ContainerIncidents.kt` (479), read at `898b3af2`.
//
// **The checks' words are NOT here.** `HealthStrings` (`HealthChecks.swift`)
// already holds every check's label and text, and one set of checks feeds two
// screens — Home's "needs attention" and this panel. A second copy of
// `every container Domovoi expects looks right` is how the two screens come to
// disagree, so this enum holds only what the *panel* says: the summary line,
// the section titles, the incident meta line's pieces, the freeze list and
// the dashboard foot.
//
// `ShellStrings.adminAttention` ("Something needs an admin's attention.") is
// `ContainerIncidents.kt:43`'s `ADMIN_ATTENTION_TEXT` and is reused from
// there rather than copied.
//
// `HealthPanelStringsTests` reads both Kotlin files off disk and fails if one
// of these is not a whole string in them (or, for the fragments either side
// of an interpolated value, not in them at all) — the method
// `ShellStringsTests` uses, so a curly apostrophe or a bullet typed for a
// middle dot is a failing test rather than a divergence somebody finds on a
// screen.
// ---------------------------------------------------------------------------

/// The panel's words. Every middle dot is **U+00B7** with one space either
/// side, every apostrophe is ASCII `'`, and the arrow in ``adminClearsThese``
/// is **U+2192** `→` because it is U+2192 in the Kotlin.
public enum HealthPanelStrings {

    // MARK: - the summary line (`ContainersScreen.kt:193-198`)

    /// `:195`.
    public static let allFine = "everything checked looks fine"

    /// `:196`.
    public static let oneThing = "1 thing needs attention"

    /// `:197`, after the count. A fragment: the Kotlin writes
    /// `"${problems.size} things need attention"`.
    public static let thingsSuffix = " things need attention"

    // MARK: - the two branches that show no detail (`:151-157`)

    /// `:152`.
    public static let oneThingAdmin = "1 thing needs the admin's attention"

    /// `:152`, after the count. A fragment, as ``thingsSuffix`` is.
    public static let thingsAdminSuffix = " things need the admin's attention"

    /// `:157`, with no problem to report.
    public static let nothingNeedsAttention = "nothing needs attention"

    /// `:157`, with problems this phone may not be told about.
    public static let adminSeesTheHouse = "the admin sees the house's health"

    /// `NoDetails`' second line on a shared screen (`:210`) — **and the
    /// Containers part's guest line there** (`:254`). One sentence, two
    /// places, and the Kotlin writes it out twice.
    public static let sharedScreenNote =
        "this is a shared screen · an admin sees the details on their own device"

    /// `NoDetails`' second line on a household phone (`:211`).
    public static let adminSeesDetails = "an admin sees the details on the dashboard"

    /// The Containers part's guest line on a phone that is not paired
    /// (`:256`).
    public static let pairToSee = "pair this phone to see what Domovoi watches"

    // MARK: - the open incidents (`:289-298`)

    /// `:292`.
    public static let needAttentionTitle = "containers that need attention"

    /// `:292`, after the count. A fragment: `"${open.size} open"`.
    public static let openSuffix = " open"

    /// `:327`, the freeze card's title…
    public static let pausedTitle = "paused until an admin reviews"

    /// …and its quiet right-hand half.
    public static let pausedSub = "everything else keeps working"

    /// `:368`, under an incident that holds the freeze. Drawn in `errInk`
    /// (Android's `colors.err`).
    public static let pausesRiskyChanges = "pauses risky changes"

    // MARK: - the Containers part's own lines (`:266-275`)

    /// `:266`. Monospaced, like the meta line.
    public static let resolvedLabel = "last 24 hours · resolved and cleared"

    /// `:275`. The arrow is **U+2192**. This is the sentence that stands in
    /// for the acknowledge and clear buttons neither app draws: both routes
    /// are admin-tier at both hops (`containers.py:133`, `:146`).
    public static let adminClearsThese =
        "an admin acknowledges or clears these on the dashboard's Settings → Health"

    // MARK: - the dashboard foot (`:160-172`)

    /// `:163-165`, two Kotlin literals joined with `+`.
    public static let dashboardFoot =
        "Updates, disk space, rooms waiting for approval and the internet are checked on the "
        + "dashboard's Health, where the admin signs in."

    /// `:169`.
    public static let openTheDashboard = "Open the dashboard's Health"

    /// `:168` — the fragment of the dashboard URL this button opens.
    public static let dashboardHash = "#settings/health"

    /// `:122`, when no browser took the URL. On iOS that is
    /// `openURL`'s completion handler answering `false`.
    public static let noBrowser = "no browser found to open the dashboard"

    // MARK: - a check row's fix link (`:241`)

    /// `"open ${c.fixLabel ?: "it"}"`'s head.
    public static let openFixPrefix = "open "

    /// …and the label when a check has a `fix` but no `fixLabel`.
    ///
    /// **Not pinned against the Kotlin.** It is two characters inside an
    /// interpolation, and a containment test for `it` would pass against any
    /// file in the app — a test that cannot fail. The whole expression it
    /// comes from is quoted above.
    public static let openFixFallback = "it"

    /// `:231` — between a check's text and `relTime(c.at)`.
    public static let atSeparator = " · "

    // MARK: - the incident meta line (`ContainerIncidents.kt:336-347`)

    /// `:337`, before the id.
    ///
    /// **Not pinned against the Kotlin** for ``openFixFallback``'s reason: it
    /// is one character inside `"#${inc.id}"`.
    public static let idPrefix = "#"

    /// `:337`, an incident of class `tamper`…
    public static let tamperingWord = "tampering"

    /// …and of any other class.
    public static let failureWord = "failure"

    /// `:339`, before `relTime(openedAt)`.
    public static let openedPrefix = "opened "

    /// `:341`, on an open incident an admin has acknowledged.
    public static let acknowledgedWord = "acknowledged"

    /// `:343`, a closed incident whose `resolution` is `cleared`…
    public static let clearedWord = "cleared"

    /// …and one that resolved by itself.
    public static let resolvedWord = "resolved"

    /// `:346` — `joinToString(" · ")`.
    public static let metaSeparator = " · "

    // MARK: - `kindWords` (`ContainerIncidents.kt:315-328`)

    public static let kindMissing = "missing"
    public static let kindStopped = "stopped"
    public static let kindRestarting = "restarting"
    public static let kindUnhealthy = "unhealthy"
    public static let kindOneshotFailed = "failed"
    public static let kindDaemon = "docker down"
    public static let kindImage = "different image"
    public static let kindUnknown = "unknown container"
    public static let kindConfig = "risky settings"
    public static let kindOutdated = "older image"

    /// `null -> "problem"`: an incident whose `kind` the server did not send.
    public static let kindNone = "problem"

    // MARK: - the banner a payload came without (`deriveBanner`, `:171-178`)

    /// `:172` — one incident with no summary of its own.
    public static let bannerOneContainer = "A Domovoi container needs attention."

    /// `:174`, after the count. A fragment:
    /// `"${shown.size} problems with Domovoi's containers need attention."`
    public static let bannerManySuffix = " problems with Domovoi's containers need attention."

    /// `:177`, appended to a tamper banner. **The leading space is the
    /// Kotlin's**, because it is appended to a finished sentence.
    public static let bannerTamperTail =
        " Plugin installs, updates and room upgrades are paused until an admin reviews it."
}

/// What a tamper incident holds back while one is open — `FREEZE_LIST`
/// (`ContainerIncidents.kt:350-355`), the web's list.
///
/// A type of its own rather than four constants, because the card draws them
/// in order with a `·` bullet in front of each and the order is the Kotlin's.
public enum HealthFreezeList {

    public static let lines: [String] = [
        "plugin installs, upgrades and enabling a plugin",
        "room software upgrades and the plugin payloads they carry",
        "preparing a room's SD card",
        "pulling and applying Domovoi updates",
    ]
}
