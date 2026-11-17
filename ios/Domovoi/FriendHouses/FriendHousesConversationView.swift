import DomovoiKit
import SwiftUI

/// One conversation: newest at the bottom, earlier pages above ("Load
/// earlier"), the composer under it with its "n / 2000" counter — Android's
/// `ConversationView` (394473e2). ``FhConversationModel`` reads it on open,
/// marks it read, and reads again every 10 s while it is up, marking read
/// again when something new came in.
///
/// A former friend's conversation reads, and has no composer: its line says
/// why (no misleading controls).
struct FhConversationScreen: View {

    let conversation: FhConversation
    let container: AppContainer
    let api: FriendHousesApi
    let gate: FriendHousesGate
    let onBack: () -> Void

    @State private var model: FhConversationModel?
    @State private var visible = false
    /// Left for the background since the reads last ran (see
    /// ``FriendHousesScreenView``'s `away`).
    @State private var away = false

    @Environment(\.scenePhase) private var scenePhase

    var body: some View {
        VStack(alignment: .leading, spacing: 0) {
            if let model {
                FhConversationHead(model: model, onBack: onBack)
                FhThreadView(model: model)
                if model.friendId != nil {
                    FhComposer(model: model)
                }
            } else {
                FhLoading()
            }
        }
        .task {
            if model == nil {
                model = FhConversationModel(conversation: conversation, container: container, api: api, gate: gate)
            }
            model?.start()
        }
        .onAppear { visible = true }
        .onDisappear {
            visible = false
            model?.stop()
        }
        .onChange(of: scenePhase) { _, phase in
            guard visible else { return }
            if phase == .background {
                away = true
                model?.stop()
            }
            if phase == .active, away {
                away = false
                model?.start()
            }
        }
    }
}

/// Back to the inbox, the name, and the one line of words under it.
struct FhConversationHead: View {

    let model: FhConversationModel
    let onBack: () -> Void

    var body: some View {
        VStack(alignment: .leading, spacing: 4) {
            HStack(alignment: .center, spacing: 4) {
                Button(action: onBack) {
                    Image(systemName: "chevron.backward")
                        .frame(minWidth: 44, minHeight: 44)
                        .contentShape(.rect)
                }
                .buttonStyle(.plain)
                .foregroundStyle(Color.brandInk)
                .accessibilityLabel(Text(verbatim: FhWords.allConversations))
                .accessibilityIdentifier("fh.back")
                FhPlainText(text: model.name, font: .headline)
                    .accessibilityAddTraits(.isHeader)
            }
            FhMutedLine(text: model.former ? FhWords.formerFriend : FhWords.conversationAdmin)
            if let problem = model.problem {
                FhProblemLine(text: problem)
            }
        }
        .padding(.horizontal, 16)
        .padding(.top, 8)
        .padding(.bottom, 8)
    }
}

/// The messages, oldest first, the newest scrolled into view whenever a newer
/// one arrives.
struct FhThreadView: View {

    let model: FhConversationModel

    var body: some View {
        ScrollViewReader { proxy in
            ScrollView {
                // **A `VStack`, not a `LazyVStack`.** Train I066's audit
                // failed the first bubble with `Dynamic Type font sizes are
                // partially unsupported` although its font is `.body`: the
                // finding this app has paid for twice before is text inside a
                // lazy container's cell (``HomeEverythingView``'s and
                // ``NewsTopicsView``'s notes, each measured: the same `Text`
                // out of the lazy container audits clean). A conversation is
                // a page of 50, and more only by "Load earlier", so the
                // laziness earns nothing here either.
                VStack(alignment: .leading, spacing: 10) {
                    if model.thread.more {
                        FhLoadEarlier(model: model)
                    }
                    if model.thread.loaded, model.thread.messages.isEmpty, model.problem == nil {
                        FhMutedLine(text: FhWords.noMessages)
                    }
                    ForEach(model.thread.messages, id: \.id) { message in
                        FhMessageBubble(message: message)
                            .id(message.id)
                    }
                    if !model.thread.loaded {
                        FhLoading()
                    }
                }
                .padding(.horizontal, 16)
                .padding(.vertical, 8)
            }
            .onChange(of: model.thread.messages.last?.id) { _, newest in
                guard let newest else { return }
                proxy.scrollTo(newest, anchor: .bottom)
            }
        }
    }
}

struct FhLoadEarlier: View {

    let model: FhConversationModel

    var body: some View {
        Button {
            Task { await model.loadEarlier() }
        } label: {
            FhPlainText(text: FhWords.loadEarlier, colour: Color.brandInk)
                .frame(minHeight: 44)
                .contentShape(.rect)
        }
        .buttonStyle(.plain)
        .accessibilityIdentifier("fh.loadEarlier")
    }
}

/// One message: its text exactly as sent (plain, line breaks kept), when it
/// arrived, the sender's clock if it disagrees, and an outgoing one's state
/// in words.
struct FhMessageBubble: View {

    let message: FhMessage

    private var outgoing: Bool { !message.incoming }

    var body: some View {
        VStack(alignment: outgoing ? .trailing : .leading, spacing: 3) {
            // `.body`, a Dynamic Type style, said here rather than left to the
            // default so the bubble's text style is on the page.
            FhPlainText(text: message.body ?? "", font: .body)
                .padding(.horizontal, 11)
                .padding(.vertical, 8)
                .background(
                    outgoing ? Color(.tertiarySystemBackground) : Color(.secondarySystemBackground),
                    in: RoundedRectangle(cornerRadius: 10)
                )
                .overlay(RoundedRectangle(cornerRadius: 10).strokeBorder(Color(.separator), lineWidth: 1))
                .accessibilityIdentifier("fh.message.body")
            FhMessageMeta(message: message)
        }
        .frame(maxWidth: .infinity, alignment: outgoing ? .trailing : .leading)
    }
}

struct FhMessageMeta: View {

    let message: FhMessage

    private var when: String {
        let now = Int64((Date().timeIntervalSince1970 * 1000).rounded())
        var parts = [relTime(message.createdAt, nowMs: now)]
        if let theirs = fhTheirClock(message) { parts.append(theirs) }
        return parts.joined(separator: " · ")
    }

    var body: some View {
        HStack(alignment: .firstTextBaseline, spacing: 6) {
            FhPlainText(text: when, font: .caption2, colour: Color.fgMuted)
            if let state = fhOutState(message) {
                ToneDot(tone: state.tone)
                FhPlainText(text: state.words, font: .caption2, colour: Color.fgMuted)
                    .accessibilityIdentifier("fh.message.state")
            }
        }
    }
}

/// The box, the counter, and Send — the counter turns red past 2,000, and
/// Send waits for something besides spaces.
struct FhComposer: View {

    let model: FhConversationModel

    var body: some View {
        VStack(alignment: .leading, spacing: 6) {
            Divider()
            TextField(
                text: Binding(get: { model.text }, set: { model.edit($0) }),
                prompt: Text(verbatim: model.placeholder),
                axis: .vertical
            ) {
                Text(verbatim: model.placeholder)
            }
            .lineLimit(2...6)
            .textFieldStyle(.roundedBorder)
            .disabled(model.busy)
            .accessibilityIdentifier("fh.compose")
            if let problem = model.sendProblem {
                FhProblemLine(text: problem)
                    .accessibilityIdentifier("fh.sendProblem")
            }
            FhComposerFoot(model: model)
        }
        .padding(.horizontal, 16)
        .padding(.bottom, 8)
    }
}

struct FhComposerFoot: View {

    let model: FhConversationModel

    var body: some View {
        HStack(alignment: .center) {
            FhPlainText(
                text: model.counter,
                font: .caption.monospacedDigit(),
                colour: model.over ? Color.errInk : Color.fgMuted
            )
            .accessibilityIdentifier("fh.counter")
            .frame(maxWidth: .infinity, alignment: .leading)
            Button {
                Task { await model.send() }
            } label: {
                Text(verbatim: model.busy ? FhWords.sending : FhWords.send)
            }
            .buttonStyle(BrandFillButtonStyle())
            .disabled(!model.canSend)
            .accessibilityIdentifier("fh.send")
        }
    }
}
