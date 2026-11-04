import DomovoiKit
import SwiftUI

/// What the local shell's tabs draw: this phone's own media, with no server.
///
/// It replaces the one `ScreenPlaceholder(route:onThisPhone: true)` line in
/// ``LocalShellView`` — the "on this phone — not built yet" screen a fresh
/// install with no server landed on. Android's counterparts are
/// `LocalMusicScreen(subtitle:upload:)` and
/// `LocalVideosScreen(subtitle:upload:)` called with `upload = null`
/// (`LocalMediaScreens.kt:118`, `:276`), and `LocalFilesScreen()`
/// (`PhoneFiles.kt:70`).
///
/// ### The one difference from the device side of a connected shell
///
/// **No uploader, anywhere.** `offersUpload: false` is passed down, so no
/// row draws an Upload button, no refused card appears and no "add" door is
/// offered: there is no server to send anything to, and a button that would
/// fail on every tap is the control the owner's rule forbids
/// ([[no-misleading-controls]]). Android passes `upload = null` for exactly
/// this.
///
/// The subtitle changes with it, to Android's own words for the unconnected
/// case — **"on this device — connect to a domovoi for your full library"**
/// (`LocalMediaScreens.kt:119`) — which is where the `connect` pill in the
/// top bar is explained.
///
/// ### What is not, and why there is no control for it
///
/// * **Music does not play here**, and this is now the **only** screen in the
///   app where that is true. D46 put `play` on a phone track's row, but the
///   app's one player is keyed to a device id (``ShellModel/player``) because
///   it writes a listening position per device — and the local shell has no
///   server, so no device id, so no player. The rows get no tap and
///   ``PhoneMediaStrings/noPhonePlayerYet`` says why. Android plays it,
///   because its player needs no identity; the gap is written down in
///   `design-notes/ios/batch-2026-11-03/player/DECISIONS.md`. The local shell
///   is where the absence is most visible — a phone with no server has a list
///   and a `connect` pill, which map §10.1 says is the design rather than
///   something to paper over with a wizard.
/// * **The local shell's Settings screen** (`LocalSettingsScreen.kt:45`) is
///   still a later batch's: ``LocalShellView``'s gear is drawn and this item
///   does not change it. Mapped and not built — it is the phone's voice and
///   its problem reports, neither of which this item touches.
/// * **Chat** has its own tab in the local shell and is not this item's.
struct LocalPhoneScreen: View {

    @Bindable var model: ShellModel
    let route: Route

    var body: some View {
        switch route {
        case .music:
            PhoneDeviceMusicView(model: model, offersUpload: false)
        case .videos:
            PhoneDeviceVideosView(model: model, offersUpload: false)
        case .files:
            PhoneDeviceFilesView(model: model, offersUpload: false)
        default:
            // Chat is its own screen and the rest of
            // ``ShellModel/localShellTabs`` is a later batch's, so the
            // placeholder it had stays — named, rather than an empty page
            // that reads as a loading failure.
            ScreenPlaceholder(route: route, onThisPhone: true)
        }
    }
}
