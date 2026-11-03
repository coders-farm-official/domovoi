import DomovoiKit
import SwiftUI

/// The mid-answer permission card: "May I look this up online?", where the
/// words would go, with its three answers.
///
/// `LookupCardView` (`assistant/LookupUi.kt:98`), and the row **is** the card
/// (`ChatScreen.kt:984`). Once it is answered the buttons are replaced by
/// ``ChatMoreStrings/cardState(_:)`` rather than disabled: the server 409s a
/// second answer (`already_answered`, `chat.py:2086`), so a live button there
/// is a control that cannot do what it says.
///
/// ### The one sentence that cannot be left out
///
/// **"goes to duckduckgo.com".** The card exists so that a person decides
/// whether their words leave the house, and a card without the destination
/// asks them to decide that with the one fact missing. It is drawn even when
/// the query line wraps to four lines at an accessibility size.
struct ChatMoreLookupCardView: View {

    let message: Int64
    let more: ChatMoreModel

    var body: some View {
        if let card = more.lookupCard(for: message) {
            VStack(alignment: .leading, spacing: 6) {
                Text(ChatMoreLookupRules.title(card))
                    .font(.subheadline.weight(.semibold))
                    .fixedSize(horizontal: false, vertical: true)
                    .multilineTextAlignment(.leading)
                // Quoted for a search, bare for a page (`LookupUi.kt:112`),
                // which is ``ChatMoreLookupRules/queryText(_:)``'s job.
                Text(ChatMoreLookupRules.queryText(card))
                    .font(.body)
                    .fixedSize(horizontal: false, vertical: true)
                    .multilineTextAlignment(.leading)
                Text(ChatMoreStrings.goesTo(card.destination))
                    .font(.caption)
                    .foregroundStyle(Color.fgMuted)
                    .fixedSize(horizontal: false, vertical: true)
                    .multilineTextAlignment(.leading)
                if more.lookupAnswerable(message) {
                    answers
                } else {
                    Text(ChatMoreStrings.cardState(card.state))
                        .font(.caption)
                        .foregroundStyle(Color.brandInk)
                        .fixedSize(horizontal: false, vertical: true)
                }
                streamed
            }
            .frame(maxWidth: .infinity, alignment: .leading)
        }
    }

    /// Allow once · Allow in this chat · Not now.
    ///
    /// A column at an accessibility size and whenever there are three of them
    /// to fit (NIGHT-RULES §3): "Allow in this chat" alone is most of a phone's
    /// width at `large`.
    private var answers: some View {
        VStack(alignment: .leading, spacing: 4) {
            answer(ChatMoreStrings.allowOnce, ChatMoreLookupRules.once, strong: true)
            answer(ChatMoreStrings.allowInThisChat, ChatMoreLookupRules.chat, strong: false)
            answer(ChatMoreStrings.notNow, ChatMoreLookupRules.no, strong: false)
        }
    }

    private func answer(_ label: String, _ decision: String, strong: Bool) -> some View {
        Button(label) {
            more.decideLookup(message: message, decision: decision)
        }
        .buttonStyle(.plain)
        .font(.subheadline)
        .foregroundStyle(strong ? Color.brandInk : Color.fgMuted)
        .frame(minHeight: 44)
    }

    /// The words of the answer as they arrive, where the reply will be
    /// (`ChatScreen.kt:897`), and the refusal if it was one.
    @ViewBuilder
    private var streamed: some View {
        if !more.cardReply.isEmpty {
            Text(more.cardReply)
                .font(.body)
                .fixedSize(horizontal: false, vertical: true)
                .multilineTextAlignment(.leading)
        }
        if let why = more.cardError {
            Text(why)
                .font(.caption)
                .foregroundStyle(Color.errInk)
                .fixedSize(horizontal: false, vertical: true)
                .multilineTextAlignment(.leading)
        }
    }
}

/// A heads-up: the weakness in a sentence, the options as buttons, and "don't
/// warn me about this again".
///
/// `HeadsUpCard` (`ChatDocsUi.kt:379`). Two things here are not cosmetic:
///
///  1. **The options are live only on the thread's last row**
///     (`ChatScreen.kt:1008`), because the server 409s a pick on anything
///     else (`stale`, `chat.py:1132`). ``ChatMoreModel/headsUpAnswerable(_:isLastRow:)``
///     is the gate, and an older card draws its text and no buttons.
///  2. **`choose_another` is not offered.** The option re-opens the picture
///     picker, and there is no picker in this app, so the server offers
///     something this phone cannot carry out —
///     ``ChatMoreHeadsUp/offerable`` drops it and the card says nothing about
///     it. DECISIONS.md §6.
struct ChatMoreHeadsUpCardView: View {

    let message: Int64
    let card: ChatMoreHeadsUp
    let more: ChatMoreModel
    let isLastRow: Bool

    var body: some View {
        VStack(alignment: .leading, spacing: 6) {
            HStack(spacing: 6) {
                // The dot is `warnInk` and not `.orange`, and it is hidden
                // from VoiceOver because the word beside it is what a reader
                // needs (NIGHT-RULES §3; ``ToneDot`` says the same).
                Circle()
                    .fill(Color.warnInk)
                    .frame(width: 8, height: 8)
                    .accessibilityHidden(true)
                Text(ChatMoreStrings.headsUp)
                    .font(.caption.weight(.semibold))
                    .foregroundStyle(Color.warnInk)
                    .fixedSize(horizontal: false, vertical: true)
            }
            Text(card.text)
                .font(.body)
                .fixedSize(horizontal: false, vertical: true)
                .multilineTextAlignment(.leading)
            if let chosen = more.headsUpChosen(for: message) {
                Text(label(of: chosen))
                    .font(.caption)
                    .foregroundStyle(Color.brandInk)
                    .fixedSize(horizontal: false, vertical: true)
            } else if more.headsUpAnswerable(message, isLastRow: isLastRow) {
                options
                never
            }
        }
        .frame(maxWidth: .infinity, alignment: .leading)
    }

    private var options: some View {
        VStack(alignment: .leading, spacing: 4) {
            ForEach(card.offerable, id: \.id) { option in
                Button(option.label) {
                    more.pickHeadsUp(message: message, option: option.id)
                }
                .buttonStyle(.plain)
                .font(.subheadline)
                .foregroundStyle(Color.brandInk)
                .frame(minHeight: 44)
            }
        }
    }

    /// "don't warn me about this again", or — once this phone has been told —
    /// what it was told (``ChatMoreStrings/silenced(_:)``).
    ///
    /// The switch is this device's and no route stores it
    /// (`HeadsUpBody`, `chat_documents.py:567`), so silencing a kind writes
    /// only to ``ChatMoreHeadsUpStore`` and the **next** pick carries it in
    /// its `skip` list (`headsUpBody`, `ChatDocsApi.kt:105`).
    @ViewBuilder
    private var never: some View {
        if more.headsUpSilenced(kind: card.kind) {
            Text(ChatMoreStrings.silenced(card.kind))
                .font(.caption2)
                .foregroundStyle(Color.fgMuted)
                .fixedSize(horizontal: false, vertical: true)
        } else if let label = card.neverLabel, !label.isEmpty {
            Button(label) { more.silenceHeadsUp(kind: card.kind) }
                .buttonStyle(.plain)
                .font(.caption)
                .foregroundStyle(Color.fgMuted)
                .frame(minHeight: 44)
        }
    }

    private func label(of option: String) -> String {
        card.options.first { $0.id == option }?.label ?? option
    }
}

/// The per-thread internet switch, under the conversation's title.
///
/// `ServerInternetChip` (`ChatScreen.kt:1265`). ``ChatView``'s doc comment
/// used to say why this was left out — "this app cannot answer a mid-answer
/// `lookup_permission` card, so a chat told it may go online can still stop
/// and wait for an answer that never comes". The card above is that answer,
/// so the chip is honest now and is drawn.
///
/// **Not tappable under `never` or `always_allow`** (`ChatScreen.kt:1288`):
/// the household's own policy wins, and a chip that looked like a switch and
/// changed nothing is the control [[no-misleading-controls]] forbids. It is
/// still drawn, because "internet always on" and "internet off" are facts a
/// person wants before they type.
struct ChatMoreInternetChipView: View {

    let more: ChatMoreModel

    var body: some View {
        if let chip = more.internetChip {
            Group {
                if more.internetChipTappable {
                    Button { more.setInternetAllowed(!chip.on) } label: { pill(chip) }
                        .buttonStyle(.plain)
                        .accessibilityLabel(chip.label)
                        .accessibilityHint(more.internetChipHint ?? "")
                        .accessibilityAddTraits(.isButton)
                } else {
                    pill(chip)
                        .accessibilityElement(children: .ignore)
                        .accessibilityLabel(chip.label)
                        .accessibilityHint(more.internetChipHint ?? "")
                }
            }
            .frame(maxWidth: .infinity, alignment: .leading)
        }
    }

    private func pill(_ chip: (label: String, on: Bool)) -> some View {
        HStack(spacing: 4) {
            Image(systemName: chip.on ? "globe" : "globe.badge.chevron.backward")
                .font(.caption2)
                .accessibilityHidden(true)
            Text(chip.label)
                .font(.caption)
                .fixedSize(horizontal: false, vertical: true)
                .multilineTextAlignment(.leading)
        }
        .foregroundStyle(chip.on ? Color.brandInk : Color.fgMuted)
        .padding(.horizontal, 8)
        .frame(minHeight: 44)
        .contentShape(.rect)
    }
}
