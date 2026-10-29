import DomovoiKit
import SwiftUI

/// The app, as little of it as the I0 gate needs: it launches, it says what
/// it is, and it is the thing Kamron installs on his iPhone 14 so the Local
/// Network prompt can be read in his own words (`project.yml`,
/// `NSLocalNetworkUsageDescription`).
///
/// The shell — the tab bar, Home's grid, the phone bar — is I1. Nothing here
/// talks to a server, and nothing here has been compiled: there was no Mac
/// when it was written.
@main
struct DomovoiApp: App {
    var body: some Scene {
        WindowGroup {
            FirstRunView()
        }
    }
}

struct FirstRunView: View {
    var body: some View {
        VStack(spacing: 16) {
            Text("Domovoi")
                .font(.largeTitle.weight(.semibold))
            Text("The app for your house. Nothing is set up yet.")
                .font(.body)
                .multilineTextAlignment(.center)
                .foregroundStyle(Color.fgMuted)
            Text(Self.state)
                .font(.footnote)
                .multilineTextAlignment(.center)
                .foregroundStyle(Color.fgMuted)
        }
        .padding(24)
        .frame(maxWidth: 420)
    }

    /// Proof that the package is linked, and the one fact worth showing on a
    /// screen with nothing on it: which addresses this build will talk to in
    /// the clear.
    private static var state: String {
        let suffixes = CleartextPolicy.privateNameSuffixes.joined(separator: " ")
        return "Plain http goes to the home network only: \(suffixes), and private addresses."
    }
}

extension Color {
    /// `--fg-muted` from `web/static/colors_and_type.css`, the ink the
    /// dashboard already uses for labels and meta, in both themes.
    ///
    /// Not SwiftUI's `.secondary`. On 2026-10-28 Apple's own accessibility
    /// audit failed this screen with "Contrast nearly passed", and it was
    /// right: iOS `secondaryLabel` is `rgba(60, 60, 67, 0.6)`, which over
    /// white is **3.44:1** — under the 4.5:1 that body text needs. The token
    /// measures **7.44:1** on the light canvas and **7.88:1** on the dark one.
    ///
    /// The audit's finding was in our own content, not SwiftUI's chrome, so
    /// the fix is the colour, not narrowing the audit (`LaunchUITests`).
    ///
    /// I1 replaces this with a real asset catalogue generated from the CSS,
    /// as plan 1.1 describes; one colour written out by hand is enough for a
    /// screen with three labels on it.
    static let fgMuted = Color(UIColor { traits in
        traits.userInterfaceStyle == .dark
            ? UIColor(red: 0.6555, green: 0.6434, blue: 0.6238, alpha: 1)   // oklch(0.72 0.008 80)
            : UIColor(red: 0.3458, green: 0.3323, blue: 0.3104, alpha: 1)   // oklch(0.45 0.01 80)
    })
}

#Preview {
    FirstRunView()
}
