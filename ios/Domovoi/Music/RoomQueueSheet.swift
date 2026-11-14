import DomovoiKit
import SwiftUI

/// A room's queue, one tap from the room (`RoomQueueSheet.kt`, batch 2 §2):
/// Music's room cards, Home's room rows and the room's own screen each carry
/// a queue button that opens this sheet — the same rows, edits, Up next and
/// search box as Music's "room queue" tab, for that one room.
///
/// ### The keyboard (46dc0d35)
///
/// Android's modal sheet ignored the keyboard (`ADJUST_NOTHING`) and covered
/// the search box until it padded itself (`imePadding`). A SwiftUI sheet's
/// scroll view is inset for the keyboard by the system, and nothing here opts
/// out of that (`ignoresSafeArea(.keyboard)`), so the box stays above it;
/// the list also lets the keyboard go on a drag.
///
/// ### What it says, it says inside itself
///
/// A sheet covers the shell's toast (``MusicMoreSheetStatus``'s reason), so
/// the queue's edits are said on a line at the top of the sheet.
struct RoomQueueSheet: View {

    let room: String
    let tools: QueueTools

    @State private var queue: MusicQueueModel?
    @Environment(\.dismiss) private var dismiss

    var body: some View {
        NavigationStack {
            ScrollView {
                if let queue {
                    MusicQueueView(rooms: [room], queue: queue, picker: false, inline: true)
                        .padding(16)
                        .frame(maxWidth: .infinity, alignment: .leading)
                }
            }
            .scrollDismissesKeyboard(.interactively)
            .navigationTitle(QueueStrings.roomQueueLabel(room))
            .navigationBarTitleDisplayMode(.inline)
            .toolbar {
                ToolbarItem(placement: .topBarTrailing) {
                    Button(MusicStrings.close) { dismiss() }
                }
            }
        }
        .environment(\.queueTools, tools)
        .task {
            if queue == nil {
                let made = tools.roomQueue()
                made.start(rooms: [room], playing: room)
                queue = made
            }
        }
        .onDisappear { queue?.stop() }
    }
}

/// The queue button a room shows: 44 points, "<room>'s queue" to VoiceOver.
///
/// **Hidden on a phone that is not paired**: the room's queue is a household
/// read (`RoomQueueSheet.kt:58`).
struct RoomQueueButton: View {

    let room: String
    let tools: QueueTools?
    let onOpen: () -> Void

    var body: some View {
        if let tools, tools.paired {
            Button(action: onOpen) {
                Image(systemName: "list.bullet")
                    .frame(minWidth: 44, minHeight: 44)
                    .contentShape(.rect)
            }
            .buttonStyle(.plain)
            .foregroundStyle(Color.brandInk)
            .accessibilityLabel(QueueStrings.roomQueueLabel(room))
        }
    }
}

/// Which room's queue sheet is up — `sheet(item:)` wants an identity.
struct RoomQueueSheetItem: Identifiable, Equatable {
    let room: String
    var id: String { room }
}
