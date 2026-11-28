import DomovoiKit
import PhotosUI
import SwiftUI

/// Chat from away (1.5.0): the house's chats over Tor while the phone is off
/// the home network, opened from the phone's own shell when the house is out
/// of reach and this phone has away chat set up.
///
/// **The same model as the home chat** (``ChatModel``), on its away path
/// (``ChatModel/useAway(_:)``), the same rows (``ChatRowView``) and the same
/// bubbles (``ChatBubbleView``): what differs is what the five away requests
/// can do. The header carries a small "Away" marker and the connection's
/// state in the shared words ("Connecting to your house…", "Can't reach your
/// house." and the rest, ``AwayStrings``). There is no search, no rename,
/// archive or delete, no retry and no video; a picture can be sent (shrunk
/// and stripped on the phone, ``AwayPicture``), and one this phone doesn't
/// hold is a placeholder, because pictures aren't downloadable away (v1).
///
/// The Tor engine runs while this screen is on (``AwayChatController``):
/// `onAppear` and `onDisappear` are its two hooks, and the shell's scene
/// phase is the third.
struct AwayChatView: View {

    let model: ShellModel
    let onBack: () -> Void

    @State private var chat: ChatModel?
    @State private var typed = ""
    @State private var picture: AwayPicture?
    @State private var photoItem: PhotosPickerItem?
    @State private var pickingPhoto = false
    @State private var preparing = false

    private var controller: AwayChatController { model.away.controller }

    var body: some View {
        VStack(spacing: 0) {
            header
            Divider()
            if controller.lapsed {
                note(AwayStrings.setUpAgainAtHome)
            } else if let chat {
                if chat.conversationOpen {
                    conversation(chat)
                } else {
                    list(chat)
                }
            } else {
                ProgressView().frame(maxWidth: .infinity, maxHeight: .infinity)
            }
        }
        .background(Color(uiColor: .systemBackground))
        .task {
            if chat == nil {
                let made = ChatModel(in: model.container, deviceID: model.deviceID,
                                     privateNote: ChatPrivateNoteBook.forApp())
                made.awayAnswered = { [weak controller] error in
                    if error == nil { controller?.linkAnswered() }
                }
                chat = made
            }
        }
        .onAppear {
            controller.chatOpened()
            if let link = controller.link { chat?.useAway(link) }
        }
        .onDisappear {
            controller.chatClosed()
            chat?.stop()
        }
        .onChange(of: chat == nil) { _, _ in
            if let link = controller.link { chat?.useAway(link) }
        }
        .onChange(of: controller.linkEpoch) { _, _ in
            // A new session (the engine started again): the chat follows it.
            if let link = controller.link { chat?.useAway(link) }
        }
        .onChange(of: chat?.toast) { _, said in
            guard let said else { return }
            model.showToast(said)
            chat?.setToast(nil)
        }
    }

    // MARK: - the header

    private var phaseLine: String? {
        switch controller.phase {
        case .connecting: return AwayStrings.connecting
        case .failed(let line): return line
        case .off, .ready: return nil
        }
    }

    private var header: some View {
        VStack(alignment: .leading, spacing: 4) {
            HStack(spacing: 8) {
                Button {
                    if let chat, chat.conversationOpen { chat.closeChat() } else { onBack() }
                } label: {
                    Image(systemName: "chevron.left")
                        .frame(minWidth: 44, minHeight: 44)
                        .contentShape(Rectangle())
                }
                .accessibilityLabel(ShellStrings.back)
                Text(chat?.conversationOpen == true ? (chat?.openTitle ?? ChatStrings.title) : ChatStrings.title)
                    .font(.headline)
                    .lineLimit(2)
                    .fixedSize(horizontal: false, vertical: true)
                    .accessibilityAddTraits(.isHeader)
                Text(AwayStrings.marker)
                    .font(.caption2.weight(.semibold))
                    .padding(.horizontal, 6)
                    .padding(.vertical, 2)
                    .background(Capsule().fill(Color.fgMuted.opacity(0.16)))
                    .accessibilityLabel(AwayStrings.markerLabel)
                Spacer(minLength: 0)
            }
            if let line = phaseLine {
                Text(line)
                    .font(.footnote)
                    .foregroundStyle(Color.fgMuted)
                    .fixedSize(horizontal: false, vertical: true)
            }
        }
        .padding(.horizontal, 12)
        .padding(.vertical, 6)
    }

    private func note(_ line: String) -> some View {
        Text(line)
            .font(.subheadline)
            .fixedSize(horizontal: false, vertical: true)
            .padding(24)
            .frame(maxWidth: .infinity, maxHeight: .infinity, alignment: .top)
    }

    // MARK: - the list

    private func list(_ chat: ChatModel) -> some View {
        List {
            Button {
                chat.startNewChat()
            } label: {
                Label(ChatStrings.newChat, systemImage: "plus.bubble")
                    .font(.headline)
                    .frame(minHeight: 44)
            }
            .buttonStyle(.plain)
            switch chat.content {
            case .waiting:
                EmptyView()
            case .empty(let line, _):
                Text(line).font(.subheadline).fixedSize(horizontal: false, vertical: true)
            case .rows(let rows):
                ForEach(rows) { row in ChatRowView(row: row, chat: chat) }
            }
        }
        .listStyle(.plain)
        .refreshable { chat.start() }
    }

    // MARK: - the conversation

    private func conversation(_ chat: ChatModel) -> some View {
        VStack(spacing: 0) {
            ScrollViewReader { proxy in
                ScrollView {
                    LazyVStack(alignment: .leading, spacing: 8) {
                        let messages = Array(chat.transcript.drawn)
                        ForEach(Array(messages.enumerated()), id: \.element.ordinal) { index, message in
                            VStack(alignment: message.role == "user" ? .trailing : .leading, spacing: 4) {
                                pictures(message, chat)
                                ChatBubbleView(
                                    message: message,
                                    chat: chat,
                                    more: nil,
                                    isLastRow: index == messages.count - 1,
                                    actions: ChatMoreRowActions(source: { _ in }, draft: { _, _ in }, report: { _ in })
                                )
                            }
                            .frame(maxWidth: .infinity, alignment: message.role == "user" ? .trailing : .leading)
                            .id(message.ordinal)
                        }
                    }
                    .padding(12)
                }
                .onChange(of: chat.transcript.messages.last?.content) { _, _ in
                    if let last = chat.transcript.messages.last { proxy.scrollTo(last.ordinal, anchor: .bottom) }
                }
            }
            composer(chat)
        }
    }

    /// A picture this phone sent (it holds the bytes) or a placeholder.
    @ViewBuilder
    private func pictures(_ message: ChatLiveMessage, _ chat: ChatModel) -> some View {
        if message.pictures > 0 {
            if let id = message.id, let data = chat.awayPictures[id], let image = UIImage(data: data) {
                Image(uiImage: image)
                    .resizable()
                    .scaledToFit()
                    .frame(maxWidth: 220, maxHeight: 220)
                    .clipShape(RoundedRectangle(cornerRadius: 10))
                    .accessibilityLabel(AwayStrings.pictureAttached)
            } else if message.id == nil {
                Label(AwayStrings.pictureAttached, systemImage: "photo")
                    .font(.caption)
                    .foregroundStyle(Color.fgMuted)
            } else {
                ForEach(0..<message.pictures, id: \.self) { _ in
                    Label(AwayStrings.pictureNotHere, systemImage: "photo")
                        .font(.caption)
                        .foregroundStyle(Color.fgMuted)
                        .padding(8)
                        .background(RoundedRectangle(cornerRadius: 10).fill(Color.fgMuted.opacity(0.08)))
                }
            }
        }
    }

    // MARK: - the composer

    @ViewBuilder
    private func composer(_ chat: ChatModel) -> some View {
        if chat.composerOffered && !controller.lapsed {
            VStack(alignment: .leading, spacing: 6) {
                if picture != nil || preparing {
                    HStack(spacing: 8) {
                        if preparing { ProgressView() }
                        Label(AwayStrings.pictureAttached, systemImage: "photo").font(.caption)
                        Spacer(minLength: 0)
                        if picture != nil {
                            Button(AwayStrings.removePicture) { picture = nil }
                                .font(.caption)
                                .frame(minHeight: 44)
                        }
                    }
                }
                HStack(spacing: 8) {
                    Button {
                        pickingPhoto = true
                    } label: {
                        Image(systemName: "photo.badge.plus")
                            .frame(minWidth: 44, minHeight: 44)
                            .contentShape(Rectangle())
                    }
                    .accessibilityLabel(AwayStrings.attachPicture)
                    .disabled(chat.sending || preparing)
                    TextField(ChatStrings.composerHint, text: $typed, axis: .vertical)
                        .textFieldStyle(.roundedBorder)
                        .lineLimit(1...5)
                        .accessibilityLabel(ChatStrings.composerHint)
                    if chat.canCancelAwayReply {
                        Button(AwayStrings.stop) { chat.cancelAwayReply() }
                            .frame(minHeight: 44)
                    } else {
                        Button {
                            if chat.send(typed, picture: picture) {
                                typed = ""
                                picture = nil
                            }
                        } label: {
                            Image(systemName: "arrow.up.circle.fill")
                                .font(.title2)
                                .frame(minWidth: 44, minHeight: 44)
                        }
                        .accessibilityLabel(ChatStrings.send)
                        .disabled(!chat.canSend || preparing
                                  || (typed.trimmingCharacters(in: .whitespacesAndNewlines).isEmpty && picture == nil))
                    }
                }
            }
            .padding(.horizontal, 12)
            .padding(.vertical, 8)
            .photosPicker(isPresented: $pickingPhoto, selection: $photoItem, matching: .images)
            .onChange(of: photoItem) { _, item in
                guard let item else { return }
                photoItem = nil
                preparing = true
                Task {
                    let data = try? await item.loadTransferable(type: Data.self)
                    let ready = await Task.detached { data.flatMap { AwayPicture.prepare($0) } }.value
                    preparing = false
                    if let ready {
                        picture = ready
                    } else {
                        model.showToast(AwayStrings.pictureFailed)
                    }
                }
            }
        } else if let note = chat.aiOffNote {
            Text(note.title).font(.footnote).padding(12)
        }
    }
}

/// The way into away chat from the phone's own shell: shown while the house
/// is out of reach and this phone has away chat set up (or the house said it
/// isn't any more, which the screen then says).
struct AwayChatEntry: View {
    let onOpen: () -> Void

    var body: some View {
        Button(action: onOpen) {
            HStack(spacing: 10) {
                Image(systemName: "bubble.left.and.bubble.right")
                Text(AwayStrings.openAwayChat)
                    .font(.subheadline.weight(.semibold))
                    .fixedSize(horizontal: false, vertical: true)
                Spacer(minLength: 0)
                Image(systemName: "chevron.right").font(.caption).foregroundStyle(Color.fgMuted)
            }
            .padding(.horizontal, 16)
            .frame(minHeight: 44)
            .contentShape(Rectangle())
        }
        .buttonStyle(.plain)
    }
}
