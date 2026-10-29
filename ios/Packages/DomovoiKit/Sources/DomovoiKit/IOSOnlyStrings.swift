import Foundation

/// The two sentences this app says that Android has no words for.
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
///  - **``noAddressYet``** is not iOS-only: Android has the same hole and says
///    "nothing answered on port 6369" about a sweep that probed nothing
///    (`ANDROID-SHELL-MAP.md` §3.1, `Discovery.localIpv4()` returning null).
///    That is reported for Android, not fixed, because it is shipping v1
///    behaviour in a soak.
///
/// ### The seam this batch deliberately leaves open
///
/// Whether ``localNetworkDenied`` gets a button that opens Settings —
/// `UIApplication.openSettingsURLString`, which lands on the app's own pane
/// where the Local Network switch is — is the owner's call and is **not
/// built**. ``ServerPickerNotice/offersTheSettingsButton`` is the seam: it
/// answers false for every notice today, the picker draws no button because
/// of it, and the day the answer is yes it is one line here and one `if` in
/// the view. The URL constant is not referenced anywhere in this package,
/// which is why nothing in `DomovoiKit` imports UIKit.
public enum IOSOnlyStrings {

    /// The Local Network permission was refused, so nothing can answer
    /// however many Domovois are on the network.
    ///
    /// The dash is an em dash, U+2014.
    public static let localNetworkDenied =
        "local network access is off — allow it in Settings, then rescan"

    /// On Wi-Fi or Ethernet, but with no site-local IPv4 — a lease that has
    /// not arrived, or an IPv6-only network. Nothing was probed, so nothing
    /// failed to answer.
    ///
    /// The dash is an em dash, U+2014.
    public static let noAddressYet =
        "on wifi but no address yet — wait a moment and rescan, or add an address manually"
}
