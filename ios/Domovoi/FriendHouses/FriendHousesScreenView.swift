import DomovoiKit
import SwiftUI

/// Friend houses: "Serverless messaging and friend system between
/// households". The house inbox (one for the whole house), a conversation
/// and its composer, the friends and the shared lists — Android's
/// `FriendHousesScreen` (394473e2), which mirrors the dashboard's Settings →
/// Household → Friend Houses at phone width.
///
/// Reached only while the server says it is on (``FriendHousesGate``, read
/// by ``RouteScreen``'s check). What the household token may do is a
/// control; what only an admin may do is a line of words (``FhWords``).
///
/// Every name, body, title and `last_error` came from another house: shown
/// only through ``FhPlainText`` (no links, no markup, line breaks kept), and
/// never logged.
///
/// The state is ``FriendHousesModel``'s; this file draws it in small typed
/// views, each with a body the type checker can take in one bite.
struct FriendHousesScreenView: View {

    let model: ShellModel

    @State private var fh: FriendHousesModel?

    /// `onAppear`/`onDisappear`, for ``ListsScreenView``'s reason: a pushed
    /// destination fires `onDisappear` while this view stays in the
    /// hierarchy.
    @State private var visible = false

    /// The app went to the background since the reads last ran. A return is
    /// `.background` → `.inactive` → `.active`, so the phase before `.active`
    /// cannot say it; this flag does (``ShellModel``'s `departed`, the same
    /// reasoning).
    @State private var away = false

    @Environment(\.scenePhase) private var scenePhase

    var body: some View {
        content
            .task {
                if fh == nil {
                    fh = FriendHousesModel(container: model.container, gate: model.friendHouses)
                }
                fh?.start()
            }
            .onAppear { visible = true }
            .onDisappear {
                visible = false
                fh?.stop()
            }
            .onChange(of: model.inputs.serverURL) { _, _ in
                guard visible else { return }
                fh?.serverChanged()
            }
            // The reads run while the app is on screen, as Android's
            // `repeatOnLifecycle(STARTED)`: off in the background, at once
            // on coming back.
            .onChange(of: scenePhase) { _, phase in
                guard visible else { return }
                if phase == .background {
                    away = true
                    fh?.stop()
                }
                if phase == .active, away {
                    away = false
                    fh?.cameBack()
                }
            }
    }

    @ViewBuilder private var content: some View {
        if let fh, let conversation = fh.conversation {
            FhConversationScreen(
                conversation: conversation,
                container: model.container,
                api: fh.gate.api,
                gate: fh.gate,
                onBack: { fh.closeConversation() }
            )
            .id(conversation.house)
        } else {
            FhTabsScreen(model: model, fh: fh)
        }
    }
}

/// The header, the status line, the tabs and the open tab.
struct FhTabsScreen: View {

    let model: ShellModel
    let fh: FriendHousesModel?

    var body: some View {
        ScrollView {
            // A `VStack`, for the reason ``FhThreadView`` gives: text in a
            // lazy container's cell is what Apple's audit reads as type that
            // cannot grow. The inbox, friends and lists are short.
            VStack(alignment: .leading, spacing: 12) {
                FhHeader(status: fh?.status)
                if let fh {
                    FhTabBar(fh: fh)
                    FhTabContent(model: model, fh: fh)
                } else {
                    FhLoading()
                }
            }
            .padding(16)
            .frame(maxWidth: .infinity, alignment: .leading)
        }
    }
}

/// The title, the feature's one description, and — stopped, error or
/// starting — the server's own sentence. The inbox still reads and a send
/// queues, so nothing else changes.
struct FhHeader: View {

    let status: FhStatus?

    var body: some View {
        VStack(alignment: .leading, spacing: 4) {
            FhPlainText(text: FhWords.title, font: .title2)
                .accessibilityAddTraits(.isHeader)
                .accessibilityIdentifier("fh.title")
            FhPlainText(text: fhDescription, font: .footnote, colour: Color.fgMuted)
                .accessibilityIdentifier("fh.description")
            if let status, let line = fhStatusLine(status) {
                HStack(alignment: .firstTextBaseline, spacing: 8) {
                    ToneDot(tone: status.status == "starting" ? .idle : .warn)
                    // The only text in its row, with the room the row has.
                    FhPlainText(text: line, font: .footnote, colour: Color.fgMuted)
                        .layoutPriority(1)
                }
                .padding(.top, 4)
                .accessibilityElement(children: .combine)
                .accessibilityIdentifier("fh.statusLine")
            }
            if let lines = fhChangeLines(status?.changes, nowMs: Int64((Date().timeIntervalSince1970 * 1000).rounded())) {
                FhChangesSection(lines: lines)
                    .padding(.top, 8)
            }
        }
    }
}

/// "This week" (Android 3d99f404 `ThisWeek`): who this house started
/// trusting in the last week, newest first, and what to do if it wasn't
/// you. Said aloud too, but quiet hours hold that back, so it stays written
/// here. Each change is one plain line (names are another house's words).
struct FhChangesSection: View {

    let lines: [String]

    var body: some View {
        VStack(alignment: .leading, spacing: 2) {
            FhPlainText(text: FhWords.thisWeek, font: .subheadline, weight: .semibold)
                .accessibilityAddTraits(.isHeader)
                .accessibilityIdentifier("fh.changes.title")
            ForEach(Array(lines.enumerated()), id: \.offset) { _, line in
                FhPlainText(text: line, font: .footnote)
                    .accessibilityIdentifier("fh.change")
            }
            FhMutedLine(text: FhWords.notYou)
                .accessibilityIdentifier("fh.changes.note")
        }
        .frame(maxWidth: .infinity, alignment: .leading)
    }
}

/// messages · friends · shared lists, the inbox's unread count beside the
/// first.
struct FhTabBar: View {

    let fh: FriendHousesModel

    var body: some View {
        ScrollView(.horizontal, showsIndicators: false) {
            HStack(spacing: 8) {
                ForEach(FhTab.allCases, id: \.self) { tab in
                    FhTabChip(
                        tab: tab,
                        selected: fh.tab == tab,
                        unread: tab == .messages ? (fh.status?.unread ?? 0) : 0
                    ) { fh.tab = tab }
                }
            }
        }
    }
}

struct FhTabChip: View {

    let tab: FhTab
    let selected: Bool
    let unread: Int
    let onTap: () -> Void

    var body: some View {
        Button(action: onTap) {
            // One text, not the label and its count side by side: two texts
            // in one row is the shape the audit reads as type that cannot
            // grow (``FhMessageMeta``). The chips scroll sideways, so the one
            // text always has the width it asks for.
            FhPlainText(
                text: unread > 0 ? "\(tab.label) \(unread)" : tab.label,
                font: .subheadline,
                colour: selected ? Color.primary : Color.fgMuted
            )
            .padding(.horizontal, 14)
            .frame(minHeight: 44)
            .background(
                Capsule().strokeBorder(selected ? Color.brandInk : Color(.separator), lineWidth: 1)
            )
            .contentShape(Capsule())
        }
        .buttonStyle(.plain)
        .accessibilityIdentifier("fh.tab.\(tab.rawValue)")
        .accessibilityAddTraits(selected ? [.isButton, .isSelected] : [.isButton])
    }
}

/// The open tab's list.
struct FhTabContent: View {

    let model: ShellModel
    let fh: FriendHousesModel

    var body: some View {
        switch fh.tab {
        case .messages:
            FhInboxList(read: fh.inbox) { fh.openConversation($0) }
        case .friends:
            FhFriendsList(read: fh.friends) { fh.message($0) }
        case .shared:
            FhSharedListsList(read: fh.shared) { shared in
                if fh.openInLists(shared) { model.navigate(.lists) }
            }
        }
    }
}

struct FhLoading: View {
    var body: some View {
        ProgressView()
            .frame(maxWidth: .infinity, minHeight: 44)
            .accessibilityLabel(Text(verbatim: FhWords.loading))
    }
}
