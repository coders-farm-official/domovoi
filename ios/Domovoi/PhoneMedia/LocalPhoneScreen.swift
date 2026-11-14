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
/// * **Not playing.** This bullet said music does not play in the local
///   shell, on the premise that the app's one player is keyed to a device id
///   a phone only gets by pairing. **Review 2 of the 2026-11-03 batch
///   measured all three clauses false**: the id is minted locally on first
///   launch (`DeviceRegistry.swift:391-403`), `AppShellView` hands this shell
///   the same ``ShellModel`` as the connected one, and ``PlayerModel``
///   resolves a `file://` src with no server in it. So a phone track plays
///   here exactly as it does on the device side of a connected shell, which
///   is Android's behaviour too (`LocalMediaScreens.kt:158`). What the local
///   shell still has none of is the house's own audio — a podcast episode, a
///   book chapter or a station is a server path, and there is no server.
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
