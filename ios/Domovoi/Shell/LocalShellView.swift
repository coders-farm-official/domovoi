import DomovoiKit
import SwiftUI

/// The shell with no server, or none in reach: the phone's own media, and a
/// `connect` pill.
///
/// §2.11's four `Local` rows are this view with a different banner and a
/// different status word, and **all four differences are
/// ``ShellModel/chrome``** — ``localBanner(for:canTrustAgain:)`` decides the
/// words, including the two states that say nothing at all (no server saved,
/// and the person's own "this phone" choice, which is not an outage).
///
/// Nothing opens by itself here. A fresh install with no server lands on its
/// own music with a `connect` pill, and map §10.1 says to resist adding a
/// first-run wizard: the absence is the design.
/// ### At the accessibility sizes this shell's header scrolls away too
///
/// Owner answer 29 is app-wide, and the workspace's half of it is
/// ``ShellScrollAwayChrome`` inside a `NavigationStack`. This shell has no
/// stack — it is a `TabView` — so each **tab** gets one, with the chrome
/// inside it. That is the shape the mechanism needs: the stack's root is the
/// tab's own screen, whose scroll view is the one UIKit collapses the large
/// title against. A single stack wrapped around the whole `TabView` would
/// draw a large title that never collapses, because a navigation bar does not
/// track a scroll view nested inside a tab controller — more chrome than the
/// bar it replaced, which is the opposite of the answer.
///
/// The banner stays above the tabs at every size. It is two short lines that
/// say the server cannot be used, with the one yes there is to offer; it is
/// not chrome a person scrolls past, and at AX5 it is the whole reason the
/// screen looks the way it does.
struct LocalShellView: View {

    @Bindable var model: ShellModel

    @Environment(\.dynamicTypeSize) private var typeSize

    /// Answer 29's rule, from ``ShellHeaderFold`` — the same one
    /// ``WorkspaceView`` reads, so the two shells cannot fold at different
    /// sizes. This shell has no `AuditStrip` variant, so the header is always
    /// drawn.
    private var headerFold: ShellHeaderFold {
        ShellHeaderFold.forHeader(
            drawn: true,
            isAccessibilitySize: typeSize.isAccessibilitySize
        )
    }

    /// No server saved, or "this phone" chosen: the room-mode way in.
    private var showsRoomModeEntry: Bool {
        switch model.screen {
        case .localMedia(.noServer), .localMedia(.choseThisPhone): return true
        default: return false
        }
    }

    var body: some View {
        VStack(spacing: 0) {
            if headerFold.drawsBarAboveTheStack {
                ShellTopBar(
                    leading: ShellStrings.localTitleSpacing + model.serverChoice.localTitle,
                    leadingProminent: true,
                    pill: model.chrome.pill,
                    onPill: { model.openConnection() }
                ) {
                    // No gear here (2026-11-19). It was an `Image` that looked
                    // like a control and opened nothing — this app has no
                    // screen of the phone's own settings yet
                    // (`LocalSettingsScreen.kt` on Android) — and the owner's
                    // rule is to hide a control that can't do what it says.
                    EmptyView()
                }
            }
            if let banner = model.chrome.banner {
                ShellBanner(
                    text: banner,
                    offersTrustAgain: model.chrome.offersTrustAgain,
                    onTrustAgain: { model.askTrustAgain() },
                    busy: model.serverChoice.isLooking
                )
            }
            // A phone with no household server is the spare phone room
            // mode is for (ROOMMODE-MAP D11): Android's own "Domovoi Room"
            // launcher icon is the way in there; an iPhone has one icon.
            // Not while a saved house is only out of reach: starting a room
            // quiets the household side, which is not what a phone away from
            // home for the afternoon is asking for (away-layout audit,
            // 2026-11-19).
            if showsRoomModeEntry { RoomModeEntryLine() }
            TabView(selection: $model.localSelection) {
                ForEach(model.localShellTabs, id: \.self) { route in
                    tab(route)
                        .tabItem {
                            Label(route.label.lowercased(), systemImage: route.systemImage)
                        }
                        .tag(route)
                }
            }
        }
    }

    /// One tab: the phone's own media, with **no uploader** — there is no
    /// server to send anything to (``LocalPhoneScreen``).
    ///
    /// At the accessibility sizes it is wrapped in a `NavigationStack` so that
    /// the shell's header can be that stack's large title and scroll away with
    /// the screen (this type's note says why the stack is per tab). Below them
    /// the tab is exactly what it was — no stack, no navigation bar, no title.
    ///
    /// **This shell cannot keep a stale bar**, which the workspace's can: here
    /// the whole `NavigationStack` goes when the fold turns off, taking its
    /// navigation item with it, so there is nothing left to hold the last
    /// title UIKit was given. ``ShellScrollAwayChrome/body(content:)`` says
    /// what that costs on the other side and how it is undone there.
    @ViewBuilder private func tab(_ route: Route) -> some View {
        let screen = LocalPhoneScreen(model: model, route: route)
        if headerFold.putsTheHeaderInsideTheStack {
            NavigationStack {
                screen
                    // **A short title, not the bar's.** At AX5 a large title
                    // is one line and UIKit truncates it: train I003's
                    // picture of this shell read `domovoi / local media` at
                    // the full 360 points with Apple's audit calling it
                    // clipped. ``ShellStrings/awayCrumb`` carries the
                    // measurement and the reason; the banner below says the
                    // whole sentence.
                    .shellScrollAwayChrome(
                        crumb: model.serverChoice.localCrumb,
                        pill: model.chrome.pill,
                        onPill: { model.openConnection() }
                    )
            }
        } else {
            screen
        }
    }
}
