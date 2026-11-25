import DomovoiKit
import SwiftUI

/// Whether the phone's own chat is a place here (``AssistantPlaceSwitch``
/// sets it on the house's screen). Read by ``ChatPrivacyBand`` for the
/// first-use note, which only makes sense where that chat exists.
private struct PhoneChatHereKey: EnvironmentKey {
    static let defaultValue = false
}

extension EnvironmentValues {
    var phoneChatHere: Bool {
        get { self[PhoneChatHereKey.self] }
        set { self[PhoneChatHereKey.self] = newValue }
    }
}

/// Private chats (1.5.0, PRIVATE-CHATS.md §2.5): who can see the open chat,
/// the "Share with household" switch for the phone that started it (or an
/// admin), the admin's banner over another device's private chat, and the
/// once-only note pointing at the phone's own chat. Every decision is
/// ``ChatModel``'s (``ChatModel/privacyLine``, ``ChatModel/showsShareSwitch``,
/// ``ChatModel/adminBanner``, ``ChatModel/privateNote``); this only draws
/// them. A server from before 1.5.0 says nothing, and then nothing is drawn.
struct ChatPrivacyBand: View {

    let chat: ChatModel

    @Environment(\.phoneChatHere) private var phoneChatHere

    var body: some View {
        VStack(alignment: .leading, spacing: 6) {
            if let banner = chat.adminBanner {
                Text(banner)
                    .font(.footnote)
                    .foregroundStyle(Color.fgMuted)
                    .fixedSize(horizontal: false, vertical: true)
            }
            if let line = chat.privacyLine {
                Label {
                    Text(line)
                } icon: {
                    Image(systemName: chat.privacyShared ? "person.2" : "lock")
                        .accessibilityHidden(true)
                }
                .font(.footnote)
                .foregroundStyle(Color.fgMuted)
            }
            if chat.showsShareSwitch {
                Toggle(isOn: Binding(
                    get: { chat.privacyShared },
                    set: { chat.shareSwitched($0) }
                )) {
                    VStack(alignment: .leading, spacing: 2) {
                        Text(ChatPrivacyStrings.shareSwitch)
                            .font(.subheadline)
                        Text(ChatPrivacyStrings.shareHelp)
                            .font(.caption)
                            .foregroundStyle(Color.fgMuted)
                            .fixedSize(horizontal: false, vertical: true)
                    }
                }
                .accessibilityIdentifier("chat-share-switch")
            }
            if let note = chat.privateNote {
                HStack(alignment: .top, spacing: 8) {
                    Text(note)
                        .font(.footnote)
                        .foregroundStyle(Color.fgMuted)
                        .fixedSize(horizontal: false, vertical: true)
                    Spacer(minLength: 4)
                    Button {
                        chat.closePrivateNote()
                    } label: {
                        Image(systemName: "xmark")
                            .frame(minWidth: 44, minHeight: 44)
                            .contentShape(.rect)
                    }
                    .buttonStyle(.plain)
                    .foregroundStyle(Color.fgMuted)
                    .accessibilityLabel("close")
                }
            }
        }
        .frame(maxWidth: .infinity, alignment: .leading)
        .padding(.vertical, chat.privacyLine == nil && chat.adminBanner == nil && chat.privateNote == nil ? 0 : 4)
        .task(id: chat.openThread) {
            chat.phoneChatHere = phoneChatHere
            chat.showPrivateNoteIfDue()
        }
        .alert(ChatPrivacyStrings.unshareTitle, isPresented: Binding(
            get: { chat.unsharing },
            set: { if !$0 { chat.cancelUnshare() } }
        )) {
            Button(ChatPrivacyStrings.unshareConfirm, role: .destructive) { chat.confirmUnshare() }
            Button(ChatStrings.renameCancel, role: .cancel) { chat.cancelUnshare() }
        } message: {
            Text(ChatPrivacyStrings.unshareAsk)
        }
    }
}
