import DomovoiKit
import SwiftUI

/// Settings → Chat & AI → Chat: "Chat from away" for this phone (1.5.0).
///
/// The offer already-paired phones get (core-0070). It is set up at home
/// only: the house refuses an enrolment from off the home network
/// (`lan_only`), and this card is in the workspace, which is only drawn at
/// home. Off is the house's DELETE and the keys wiped here.
struct AwayChatSettingsCard: View {

    let model: ShellModel

    private var away: AwayModel { model.away }

    var body: some View {
        VStack(alignment: .leading, spacing: 8) {
            Text(AwayStrings.settingsTitle)
                .font(.headline)
                .fixedSize(horizontal: false, vertical: true)
            Text(away.enrolled ? AwayStrings.settingsOn : AwayStrings.offer)
                .font(.footnote)
                .foregroundStyle(Color.fgMuted)
                .fixedSize(horizontal: false, vertical: true)
            if let line = away.line {
                Text(line)
                    .font(.footnote)
                    .fixedSize(horizontal: false, vertical: true)
            }
            if away.busy {
                HStack(spacing: 8) {
                    ProgressView()
                    Text(away.enrolled ? AwayStrings.turningOff : AwayStrings.settingUp).font(.footnote)
                }
            } else if away.enrolled {
                Button(AwayStrings.turnOff) { Task { await away.turnOff() } }
                    .frame(minHeight: 44)
            } else {
                Button(AwayStrings.offerYes) { Task { await away.turnOn() } }
                    .frame(minHeight: 44)
            }
        }
        .frame(maxWidth: .infinity, alignment: .leading)
        .padding(12)
        .background(RoundedRectangle(cornerRadius: 12).fill(Color.fgMuted.opacity(0.06)))
        // The house's word on it, every time the card is looked at.
        .task { await away.checkAtHome(force: true) }
    }
}
