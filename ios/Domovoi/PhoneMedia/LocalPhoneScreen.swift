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
/// * **Music does not play here.** The local shell's Music tab lists the
///   audio in the folders the person added and says
///   ``PhoneMediaStrings/noPhonePlayerYet``. Android plays it; this app has
///   no handset audio engine yet (DECISIONS), and the local shell is where
///   that absence is most visible — a phone with no server and no player has
///   a list and a `connect` pill, which map §10.1 says is the design rather
///   than something to paper over with a wizard.
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
