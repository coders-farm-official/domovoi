import DomovoiKit
import SwiftUI

// The two tabs' lists — Android's `InboxTab` and `SharedTab` (394473e2),
// with the dashboard's Shared lists card's controls (1.4.1): Accept and
// Decline on an offer, "open in Lists" and Stop sharing on a list shared
// now. The Friends tab went with the admin zone, which the phones hide.

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

// MARK: - shared lists (the dashboard's `FhSharedListsCard`)

/// What sharing does, the offers to this house, then what is shared now;
/// starting a share is an admin's, said in one line.
struct FhSharedListsList: View {

    let fh: FriendHousesModel
    let onOpen: (FhSharedList) -> Void

    var body: some View {
        switch fh.shared {
        case nil:
            FhLoading()
        case .problem(let text)?:
            FhProblemLine(text: text)
        case .ok(let rows)?:
            let sections = fhSharedSections(rows)
            FhMutedLine(text: FhWords.shareExplain)
                .accessibilityIdentifier("fh.shared.explain")
            if !sections.offers.isEmpty {
                FhSectionLabel(text: FhWords.offeredHeading)
                ForEach(sections.offers, id: \.id) { offer in
                    FhOfferRow(fh: fh, offer: offer)
                    Divider()
                }
            }
            if !sections.current.isEmpty {
                FhSectionLabel(text: FhWords.sharedNowHeading)
                ForEach(sections.current, id: \.id) { shared in
                    FhSharedRow(fh: fh, shared: shared) { onOpen(shared) }
                    Divider()
                }
            }
            if sections.offers.isEmpty && sections.current.isEmpty {
                FhEmptyNote(title: FhWords.noShared)
            }
            FhMutedLine(text: FhWords.listsAdmin)
                .padding(.top, 8)
                .accessibilityIdentifier("fh.shared.note")
        }
    }
}

/// A group's small lowercase heading.
struct FhSectionLabel: View {

    let text: String

    var body: some View {
        FhPlainText(text: text, font: .footnote, weight: .semibold, colour: Color.fgMuted)
            .padding(.top, 8)
            .accessibilityAddTraits(.isHeader)
    }
}

/// A friend's list offered to this house: who and what, "give it another
/// name here", Accept and Decline. A name taken here (a 400) opens the name
/// field with the server's sentence, as the dashboard does.
struct FhOfferRow: View {

    let fh: FriendHousesModel
    let offer: FhSharedList

    @State private var naming = false
    @State private var name = ""
    @State private var problem: String?

    private var busy: Bool { fh.busyShared.contains(offer.id) }
    private var named: String { name.trimmingCharacters(in: .whitespacesAndNewlines) }

    var body: some View {
        VStack(alignment: .leading, spacing: 6) {
            FhPlainText(text: FhWords.invited(FhWords.friendName(offer.friend), offer.title ?? ""))
                .accessibilityIdentifier("fh.shared.offer")
            if naming {
                // Typing clears the last refusal (the dashboard's onChange);
                // the name set when a refusal opens the field does not.
                TextField(
                    text: Binding(get: { name }, set: { name = $0; problem = nil }),
                    prompt: Text(verbatim: FhWords.callItHere)
                ) {
                    Text(verbatim: FhWords.callItHere)
                }
                .textFieldStyle(.roundedBorder)
                .disabled(busy)
                .accessibilityIdentifier("fh.shared.name")
            } else {
                Button {
                    name = offer.title ?? ""
                    naming = true
                } label: {
                    FhPlainText(text: FhWords.rename, font: .footnote, colour: Color.brandInk)
                        .frame(minHeight: 44)
                        .contentShape(.rect)
                }
                .buttonStyle(.plain)
                .disabled(busy)
                .accessibilityIdentifier("fh.shared.rename")
            }
            if let problem {
                FhProblemLine(text: problem)
                    .accessibilityIdentifier("fh.shared.problem")
            }
            FhRowOrColumn {
                Button {
                    Task { await accept() }
                } label: {
                    Text(verbatim: FhWords.accept)
                }
                .buttonStyle(BrandFillButtonStyle())
                .disabled(busy || (naming && named.isEmpty))
                .accessibilityIdentifier("fh.shared.accept")
            } trailing: {
                Button {
                    fh.askDecline(offer)
                } label: {
                    Text(verbatim: FhWords.decline)
                }
                .buttonStyle(InkOutlineButtonStyle())
                .disabled(busy)
                .accessibilityIdentifier("fh.shared.decline")
            }
        }
        .padding(.vertical, 4)
    }

    private func accept() async {
        problem = nil
        switch await fh.accept(offer, name: naming ? named : nil) {
        case .nameTaken(let said):
            if !naming {
                name = offer.title ?? ""
                naming = true
            }
            problem = said
        case .accepted, .failed, .dropped:
            break
        }
    }
}

/// A list shared now: its name and whose it is, whether they have accepted
/// ours yet, their name for theirs, changes waiting, "open in Lists" and
/// Stop sharing.
struct FhSharedRow: View {

    let fh: FriendHousesModel
    let shared: FhSharedList
    let onOpen: () -> Void

    private var friend: String { FhWords.friendName(shared.friend) }

    var body: some View {
        VStack(alignment: .leading, spacing: 4) {
            FhPlainText(text: FhWords.sharedLine(fhListName(shared), origin: shared.origin, friend: friend), weight: .semibold)
                .accessibilityIdentifier("fh.shared.list")
            if shared.state == "invited" {
                FhMutedLine(text: FhWords.waitingForThem)
            }
            if let theirs = fhTheirTitle(shared) {
                FhMutedLine(text: theirs)
            }
            if shared.waitingToSend > 0 {
                FhMutedLine(text: FhWords.waiting(shared.waitingToSend))
            }
            FhRowOrColumn {
                if let id = shared.listId, id > 0 {
                    Button(action: onOpen) {
                        FhPlainText(text: FhWords.openInLists, colour: Color.brandInk)
                            .frame(minHeight: 44)
                            .contentShape(.rect)
                    }
                    .buttonStyle(.plain)
                    .accessibilityIdentifier("fh.shared.open")
                }
            } trailing: {
                Button {
                    fh.askStop(shared)
                } label: {
                    Text(verbatim: FhWords.stopSharing)
                }
                .buttonStyle(InkOutlineButtonStyle())
                .disabled(fh.busyShared.contains(shared.id))
                .accessibilityIdentifier("fh.shared.stop")
            }
        }
        .padding(.vertical, 4)
    }
}
