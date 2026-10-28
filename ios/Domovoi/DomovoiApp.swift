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
                .foregroundStyle(.secondary)
            Text(Self.state)
                .font(.footnote)
                .multilineTextAlignment(.center)
                .foregroundStyle(.secondary)
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

#Preview {
    FirstRunView()
}
