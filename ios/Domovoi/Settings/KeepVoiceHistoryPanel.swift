import DomovoiKit
import SwiftUI

/// Settings → Privacy → **Keep voice history** (1.5.0), read-only: the value
/// from `GET /api/voice-history` and where an admin changes it
/// (`KeepVoiceHistoryPanel.kt`). The link to the dashboard's own page is
/// drawn only when the server says it is 1.5.0 or later
/// (``KeepVoiceHistory/dashboardHasPage(_:)``): an older dashboard has no
/// such page, and every link here is to a page the dashboard has.
struct KeepVoiceHistoryPanel: View {

    let model: ShellModel

    @Environment(\.openURL) private var openURL
    @State private var read: KeepVoiceHistory.Read?
    @State private var failure: String?
    @State private var noBrowser = false

    var body: some View {
        PanelCard(title: KeepVoiceHistory.valueLabel, sub: ShellStrings.onTheDashboardSub) {
            VStack(alignment: .leading, spacing: 8) {
                if !model.isPaired {
                    muted(KeepVoiceHistory.unpaired)
                } else if let read {
                    if let label = read.label {
                        Text(verbatim: KeepVoiceHistory.valueLine(label))
                            .font(.body.weight(.semibold))
                            .fixedSize(horizontal: false, vertical: true)
                            .accessibilityIdentifier("keep-voice-history-value")
                        muted(KeepVoiceHistory.adminChangesIt)
                        if read.linkable,
                           let address = VoiceSettingsDashboard.url(
                               serverURL: model.inputs.serverURL, section: KeepVoiceHistory.dashboard
                           ) {
                            Button(ShellStrings.openOnTheDashboard) { open(address) }
                                .font(.footnote)
                                .frame(minHeight: 44)
                                .contentShape(.rect)
                                .accessibilityIdentifier("keep-voice-history-open-dashboard")
                        }
                    } else {
                        muted(KeepVoiceHistory.olderServer)
                    }
                } else {
                    muted(failure ?? "reading…")
                }
                if noBrowser {
                    Text(ShellStrings.noBrowserForDashboard)
                        .font(.footnote)
                        .foregroundStyle(Color.errInk)
                        .fixedSize(horizontal: false, vertical: true)
                }
            }
        }
        .task(id: "\(model.inputs.serverURL)|\(model.isPaired)") {
            guard model.isPaired else { read = nil; return }
            do {
                read = try await KeepVoiceHistory.read(model.container.client)
                failure = nil
            } catch is CancellationError {
                return
            } catch {
                // No answer keeps what was shown (`rememberApi`'s rule).
                if read == nil { failure = "couldn’t read it" }
            }
        }
    }

    private func muted(_ text: String) -> some View {
        Text(verbatim: text)
            .font(.footnote)
            .foregroundStyle(Color.fgMuted)
            .fixedSize(horizontal: false, vertical: true)
    }

    private func open(_ address: String) {
        guard let url = URL(string: address) else { noBrowser = true; return }
        noBrowser = false
        openURL(url) { accepted in noBrowser = !accepted }
    }
}
