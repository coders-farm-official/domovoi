import Foundation

/// The four sentences this app says that Android has no words for.
///
/// **Drafts, awaiting Kamron's wording.** They are written down here, in one
/// place and nowhere else, so that rewording either is a one-line change and
/// so that `grep` finds every use. `design-notes/ios/IOS-ONLY-STRINGS.md` is
/// where they came from and where the open question lives; nothing in this
/// batch invented a third.
///
/// Why Android has none:
///
///  - **``localNetworkDenied``** is genuinely iOS-only. Apple shows its own
///    Local Network prompt on the first local connection and there is no API
///    that reports the answer (TN3179); a refusal surfaces only as every
///    connection failing, which ``Discovery/isLocalNetworkDenied(_:)`` reads
///    off `NWPath.unsatisfiedReason`. Android asks for no such permission.
///  - **``nothingAnsweredYet(port:)``** is iOS-only for the same reason:
///    it exists only because the first sweep of an install can be the one
///    Apple's prompt is interrupting, and Android has no such prompt. Its
///    twin ``ShellStrings/nothingAnswered(port:)`` is Android's sentence and
///    is still said once the permission question is settled.
///  - **``noAddressYet``** is not iOS-only: Android has the same hole and says
///    "nothing answered on port 6369" about a sweep that probed nothing
///    (`ANDROID-SHELL-MAP.md` §3.1, `Discovery.localIpv4()` returning null).
///    That is reported for Android, not fixed, because it is shipping v1
///    behaviour in a soak.
///  - **``attentionStale``** is not iOS-only either: Android's `rememberApi`
///    keeps the last value when a read fails and says nothing at all about
///    it (`ANDROID-HOME-MAP.md` §10.4), so a card can show a check that no
///    longer holds with no mark on it. Part 1 shipped ``ApiRead/isStale``
///    with no words; this is the words, for the needs-attention card.
///
/// ### The seam this batch deliberately leaves open
///
/// Whether ``localNetworkDenied`` gets a button that opens Settings —
/// `UIApplication.openSettingsURLString`, which lands on the app's own pane
/// where the Local Network switch is — is the owner's call and is **not
/// built**. ``ServerPickerNotice/offersTheSettingsButton`` names it: it
/// answers false for every notice today and **nothing reads it** — the
/// picker's notice line draws no button because no button is written, which
/// is also why flipping that property would change nothing. The day the
/// answer is yes it is one line there and one `if` in the view. The URL
/// constant is not referenced anywhere in this package, which is why nothing
/// in `DomovoiKit` imports UIKit.
public enum IOSOnlyStrings {

    /// The Local Network permission was refused, so nothing can answer
    /// however many Domovois are on the network.
    ///
    /// The dash is an em dash, U+2014.
    public static let localNetworkDenied =
        "local network access is off — allow it in Settings, then rescan"

    /// Nothing answered, on an install where nothing ever has — so the
    /// permission is still an explanation and the person's server is not
    /// accused of anything.
    ///
    /// **What it may claim, and what it must not.** The app cannot tell
    /// whether Local Network access was granted (TN3179), so the sentence
    /// cannot assert that it was refused: it says *if*. Nor may it leave out
    /// the fact it does have — that nothing answered on the port — because
    /// that is the one thing the sweep measured, and a person whose backend
    /// really is down has to be able to get there from this line. Hence both
    /// halves, the fact first.
    ///
    /// The twin for a settled install is ``ShellStrings/nothingAnswered(port:)``,
    /// which does ask about the web backend, and may: by then something has
    /// answered this install at least once, so the permission is not it.
    ///
    /// The dash is an em dash, U+2014, and the port is interpolated from
    /// ``Discovery/defaultPort`` as Android's sentence interpolates
    /// `Discovery.DEFAULT_PORT`.
    public static func nothingAnsweredYet(port: Int = Discovery.defaultPort) -> String {
        "nothing answered on port \(port) — if iOS asked about local network "
            + "access, allow it and rescan; or add an address below"
    }

    /// On Wi-Fi or Ethernet, but with no site-local IPv4 — a lease that has
    /// not arrived, or an IPv6-only network. Nothing was probed, so nothing
    /// failed to answer.
    ///
    /// The dash is an em dash, U+2014.
    public static let noAddressYet =
        "on wifi but no address yet — wait a moment and rescan, or add an address manually"

    /// One of the card's five sources could not be re-read, so some of what
    /// the card shows may be a few seconds old.
    ///
    /// Used by ``HomeAttentionStrings/stale``. The separator is U+00B7, as
    /// every Home string's is, and the apostrophe is ASCII — this is in
    /// Android's voice even though Android has no such string.
    ///
    /// **What it can honestly claim.** `attentionIsStale` is the OR of five
    /// reads, and a stale `config` alone trips it — the visibility setting
    /// being a few seconds old while every row on screen is the newest answer
    /// there is. So the wording says only that a recheck did not get through,
    /// not that any particular row is out of date: 42 characters that read the
    /// same whichever of the five went stale, against the 59-character draft
    /// that put the consequence first and asserted it of the rows. It is also
    /// only ever drawn when the card is already there, so a house whose health
    /// read went stale while everything was fine is told nothing at all —
    /// which matches Android's silence and is deliberate.
    ///
    /// **Still a draft awaiting the owner.** See this file's heading.
    public static let attentionStale = "couldn't recheck · showing the last answer"
}
