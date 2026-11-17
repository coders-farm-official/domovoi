import DomovoiKit
import SwiftUI

// The three tabs' lists — Android's `InboxTab`, `FriendsTab` and
// `SharedTab` (394473e2). Read only, except "message" on a friend and "open
// in lists" on an active shared list; everything an admin does is a line of
// words.

// MARK: - the inbox

/// The house inbox, newest first, unread marked.
struct FhInboxList: View {

    let read: FhRead<[FhConversation]>?
    let onOpen: (String) -> Void

    var body: some View {
        switch read {
        case nil:
            FhLoading()
        case .problem(let text)?:
            FhProblemLine(text: text)
        case .ok(let rows)? where rows.isEmpty:
            FhEmptyNote(title: FhWords.noMessages, detail: FhWords.noMessagesSub)
        case .ok(let rows)?:
            ForEach(rows, id: \.house) { row in
                FhConversationRow(conversation: row) { onOpen(row.house) }
                Divider()
            }
        }
    }
}

/// One conversation in the inbox: the name (bold while unread), "no longer
/// a friend" when it is, and the counts as pills.
struct FhConversationRow: View {

    let conversation: FhConversation
    let onTap: () -> Void

    var body: some View {
        // The name and the pills side by side, or the pills under the name
        // at an accessibility size (``FhRowOrColumn``): a name squeezed beside
        // two pills has no width to grow into.
        Button(action: onTap) {
            FhRowOrColumn {
                VStack(alignment: .leading, spacing: 2) {
                    FhPlainText(
                        text: conversation.name ?? "",
                        weight: conversation.unread > 0 ? Font.Weight.semibold : Font.Weight.regular
                    )
                    if conversation.friendId == nil {
                        FhMutedLine(text: FhWords.noLongerAFriend)
                    }
                }
                .frame(maxWidth: .infinity, alignment: .leading)
            } trailing: {
                HStack(spacing: 8) {
                    FhConversationPills(conversation: conversation)
                    Image(systemName: "chevron.right")
                        .font(.footnote)
                        .foregroundStyle(Color.fgMuted)
                        .accessibilityHidden(true)
                }
            }
            .frame(minHeight: 44)
            .contentShape(.rect)
        }
        .buttonStyle(.plain)
        .accessibilityIdentifier("fh.conversation")
    }
}

/// The counts: side by side, or one above the other at an accessibility size,
/// so neither pill's words are squeezed by the other (``FhRowOrColumn``).
struct FhConversationPills: View {

    let conversation: FhConversation

    var body: some View {
        FhRowOrColumn {
            if conversation.notDelivered > 0 {
                HomeTonePill(label: FhWords.notDelivered(conversation.notDelivered), tone: .err)
            }
        } trailing: {
            if conversation.unread > 0 {
                HomeTonePill(label: FhWords.unread(conversation.unread), tone: .brand)
            }
        }
    }
}

// MARK: - friends (read only)

struct FhFriendsList: View {

    let read: FhRead<[FhFriend]>?
    let onMessage: (FhFriend) -> Void

    var body: some View {
        switch read {
        case nil:
            FhLoading()
        case .problem(let text)?:
            FhProblemLine(text: text)
        case .ok(let rows)?:
            if rows.isEmpty {
                FhEmptyNote(title: FhWords.noFriends, detail: FhWords.noFriendsSub)
            }
            ForEach(rows, id: \.id) { friend in
                FhFriendRow(friend: friend) { onMessage(friend) }
                Divider()
            }
            FhFriendsAdminWords()
        }
    }
}

/// A friend: the name this house gave them, verified or not, what a new
/// message from them does (the admin's choice, in words), and "message".
struct FhFriendRow: View {

    let friend: FhFriend
    let onMessage: () -> Void

    var body: some View {
        VStack(alignment: .leading, spacing: 4) {
            // Name and pill side by side, or stacked at an accessibility size.
            FhRowOrColumn {
                FhPlainText(text: friend.name, weight: .semibold)
                    .frame(maxWidth: .infinity, alignment: .leading)
            } trailing: {
                HomeTonePill(
                    label: friend.verified ? FhWords.verified : FhWords.notVerified,
                    tone: friend.verified ? .ok : .warn
                )
            }
            FhMutedLine(text: FhWords.alert(friend.alert))
            if FhPaths.isHouseKey(friend.house) {
                Button(action: onMessage) {
                    FhPlainText(text: FhWords.message, colour: Color.brandInk)
                        .frame(minHeight: 44)
                        .contentShape(.rect)
                }
                .buttonStyle(.plain)
                .accessibilityIdentifier("fh.friend.message")
            }
        }
        .padding(.vertical, 4)
    }
}

/// What the phone follows, and what only an admin may change.
struct FhFriendsAdminWords: View {
    var body: some View {
        VStack(alignment: .leading, spacing: 6) {
            FhMutedLine(text: FhWords.phoneFollows)
            FhMutedLine(text: FhWords.phoneOpenOnly)
            FhMutedLine(text: FhWords.friendsAdmin)
        }
        .padding(.top, 8)
    }
}

// MARK: - shared lists (read only here; the list itself is an ordinary list)

struct FhSharedListsList: View {

    let read: FhRead<[FhSharedList]>?
    let onOpen: (FhSharedList) -> Void

    var body: some View {
        switch read {
        case nil:
            FhLoading()
        case .problem(let text)?:
            FhProblemLine(text: text)
        case .ok(let rows)?:
            let lists = fhSharedOrder(rows)
            if lists.isEmpty {
                FhEmptyNote(title: FhWords.noShared)
            }
            ForEach(lists, id: \.id) { shared in
                FhSharedRow(shared: shared) { onOpen(shared) }
                Divider()
            }
            FhMutedLine(text: FhWords.listsAdmin)
                .padding(.top, 8)
        }
    }
}

/// An active list: its name, whose it is, changes waiting, and "open in
/// lists". An offer an admin hasn't accepted: one sentence, never a button.
struct FhSharedRow: View {

    let shared: FhSharedList
    let onOpen: () -> Void

    private var friend: String { FhWords.friendName(shared.friend) }

    var body: some View {
        VStack(alignment: .leading, spacing: 4) {
            if shared.state == "invited" {
                FhPlainText(text: FhWords.invited(friend, shared.title ?? ""))
            } else {
                FhPlainText(text: shared.listName ?? shared.title ?? "", weight: .semibold)
                FhMutedLine(text: shared.origin == "us" ? FhWords.ourList(friend) : FhWords.theirList(friend))
                if shared.waitingToSend > 0 {
                    FhMutedLine(text: FhWords.waiting(shared.waitingToSend))
                }
                if let id = shared.listId, id > 0 {
                    Button(action: onOpen) {
                        FhPlainText(text: FhWords.openInLists, colour: Color.brandInk)
                            .frame(minHeight: 44)
                            .contentShape(.rect)
                    }
                    .buttonStyle(.plain)
                    .accessibilityIdentifier("fh.shared.open")
                }
            }
        }
        .padding(.vertical, 4)
    }
}
