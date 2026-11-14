import DomovoiKit
import SwiftUI

/// Settings → Rooms: "Use this device as a room" — opens room mode's setup
/// (`roommode/RoomModeEntryCard.kt`). Also on the no-server shell
/// (ROOMMODE-MAP D11), where Android's own "Domovoi Room" launcher icon is
/// the way in on a phone that was never paired; an iPhone has one icon.
///
/// Drawn only where the scene switch is in the environment: without it the
/// button would open nothing.
struct RoomModeEntryCard: View {
    @Environment(\.roomModeScene) private var scene

    var body: some View {
        if let scene {
            VStack(alignment: .leading, spacing: 6) {
                Text(RoomModeStrings.entrySection.uppercased())
                    .font(.caption.weight(.semibold))
                    .foregroundStyle(Color.fgMuted)
                    .accessibilityAddTraits(.isHeader)
                Text(RoomModeStrings.entryTitle)
                    .font(.headline)
                    .fixedSize(horizontal: false, vertical: true)
                Text(RoomModeStrings.entryBody)
                    .font(.subheadline)
                    .foregroundStyle(Color.fgMuted)
                    .fixedSize(horizontal: false, vertical: true)
                RoomPreviewLabel()
                    .padding(.top, 2)
                Button(RoomModeStrings.entryButton) { scene.openSetup() }
                    .buttonStyle(InkOutlineButtonStyle())
                    .frame(minHeight: 44)
                    .padding(.top, 4)
                    .accessibilityIdentifier("room-mode-entry")
            }
            .padding(16)
            .frame(maxWidth: .infinity, alignment: .leading)
            .background(Color(.secondarySystemBackground), in: RoundedRectangle(cornerRadius: 12))
        }
    }
}

/// The no-server shell's one line (D11): a phone with no household server
/// is exactly the spare phone room mode is for. A line, not a card, so the
/// phone's own media keep the screen.
struct RoomModeEntryLine: View {
    @Environment(\.roomModeScene) private var scene

    var body: some View {
        if let scene {
            Button {
                scene.openSetup()
            } label: {
                HStack(spacing: 8) {
                    Image(systemName: "dot.radiowaves.left.and.right")
                        .accessibilityHidden(true)
                    Text(RoomModeStrings.entryButton)
                        .fixedSize(horizontal: false, vertical: true)
                    Spacer(minLength: 0)
                    Text(RoomModeStrings.previewLabel)
                        .font(.caption.weight(.semibold))
                        .foregroundStyle(Color.fgMuted)
                    Image(systemName: "chevron.right")
                        .font(.caption)
                        .foregroundStyle(Color.fgMuted)
                        .accessibilityHidden(true)
                }
                .font(.subheadline)
                .foregroundStyle(Color.brandInk)
                .padding(.horizontal, 16)
                .frame(minHeight: 44)
                .contentShape(Rectangle())
            }
            .buttonStyle(.plain)
            .accessibilityIdentifier("room-mode-entry")
        }
    }
}
