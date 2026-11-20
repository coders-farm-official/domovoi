import DomovoiKit
import SwiftUI

/// Before the saved server first answers (``ShellScreen/connecting``): one
/// line for the moment it takes at home. No workspace behind it, so nothing
/// asks the server for pages it may never answer (`AppShell.kt`'s
/// `ConnectingScreen`, 2026-11-19).
///
/// Android draws its cat glyph above the line; this app has no brand glyph
/// view (``UnclaimedServerView`` says the same), so a spinner stands there.
struct ConnectingView: View {

    let serverName: String

    var body: some View {
        VStack(spacing: 16) {
            ProgressView()
                .accessibilityHidden(true)
            Text(connectingLine(serverName))
                .font(.body)
                .foregroundStyle(Color.fgMuted)
                .multilineTextAlignment(.center)
                .fixedSize(horizontal: false, vertical: true)
        }
        .padding(24)
        .frame(maxWidth: .infinity, maxHeight: .infinity)
        .background(Color(uiColor: .systemBackground))
    }
}

/// The server answered earlier but has gone quiet on screen
/// (``ShellModel/coversWorkspace``): a card over the workspace instead of
/// pages that can only spin. An answer lifts it; the grace's verdict swaps in
/// the phone's own media (`AppShell.kt`'s `LookingCover`).
struct LookingCoverView: View {

    let serverName: String

    var body: some View {
        VStack(spacing: 12) {
            ProgressView()
                .accessibilityHidden(true)
            Text(lookingCoverTitle(serverName))
                .font(.headline)
                .multilineTextAlignment(.center)
                .fixedSize(horizontal: false, vertical: true)
                .accessibilityAddTraits(.isHeader)
            Text(ShellStrings.lookingCoverLine)
                .font(.subheadline)
                .foregroundStyle(Color.fgMuted)
                .multilineTextAlignment(.center)
                .fixedSize(horizontal: false, vertical: true)
        }
        .padding(24)
        .frame(maxWidth: .infinity, maxHeight: .infinity)
        // Opaque, and it takes the touches: nothing underneath can be tapped.
        .background(Color(uiColor: .systemBackground))
        .contentShape(Rectangle())
    }
}
