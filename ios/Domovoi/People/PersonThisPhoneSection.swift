import DomovoiKit
import SwiftUI

/// People → a person → profile: **this phone** and **listening history**
/// (2026-11-12). `ThisPhoneSection` and `ListeningHistorySection`
/// (`android/.../ui/screens/people/ThisPhone.kt`, a1e611b6); every word and
/// rule is ``PeopleThisPhoneModel``'s and ``ListeningStrings``'.
///
/// Every press is the model's, never a `Task` held here: the profile is a
/// plain stack, but the model outlives a tab switch and checks the person and
/// the server it asked for before it shows an answer.
struct PersonThisPhoneSection: View {

    let person: Person
    let model: PeopleThisPhoneModel
    /// The roster, re-read after a claim that changed who has a phone.
    let onPeopleChanged: @MainActor () -> Void

    @Environment(\.openURL) private var openURL

    var body: some View {
        VStack(alignment: .leading, spacing: 16) {
            thisPhone
            listening
        }
        .onAppear { model.show(person) }
        .onChange(of: person.id) { _, _ in model.show(person) }
        .alert(
            ListeningStrings.clearTitle(person.name),
            isPresented: Binding(
                get: { model.ask != nil },
                set: { if !$0 { model.dismissAsk() } }
            ),
            presenting: model.ask
        ) { ask in
            switch ask.check {
            case .allowed:
                Button(ListeningStrings.clearConfirm, role: .destructive) { model.confirmClear(person) }
                Button(ListeningStrings.cancel, role: .cancel) { model.dismissAsk() }
            case .notHere(_, _, let offers):
                if offers.contains(.openDashboard), let url = model.dashboardURL {
                    Button(ListeningStrings.openTheDashboard) {
                        model.dismissAsk()
                        openURL(url)
                    }
                }
                if offers.contains(.thisIsMe) {
                    Button(ListeningStrings.thisIsMe) { model.thisIsMeFromClear(person) }
                }
                Button(ListeningStrings.cancel, role: .cancel) { model.dismissAsk() }
            }
        } message: { ask in
            switch ask.check {
            case .allowed:
                Text(ListeningStrings.confirmText(name: person.name, count: model.count(for: person))
                     + "\n\n" + ListeningStrings.cantBeUndone)
            case .notHere(let sentence, let why, _):
                Text(sentence + "\n\n" + why)
            }
        }
    }

    // MARK: - this phone

    private var thisPhone: some View {
        let block = model.block(for: person)
        return VStack(alignment: .leading, spacing: 6) {
            Text(ListeningStrings.thisPhoneLabel)
                .font(.headline)
                .accessibilityAddTraits(.isHeader)
            Text(block.line)
                .font(.subheadline)
                .fixedSize(horizontal: false, vertical: true)
            // Unpaired, a claim would only be refused: no button.
            if model.paired, let action = block.action {
                actionButton(action)
            }
            Text(block.small)
                .font(.caption)
                .foregroundStyle(Color.fgMuted)
                .fixedSize(horizontal: false, vertical: true)
            if let refused = model.refused {
                // The server's own sentence for a refusal — a 403 or 409 is
                // shown, never swallowed.
                Text(refused.message)
                    .font(.footnote)
                    .foregroundStyle(Color.errInk)
                    .fixedSize(horizontal: false, vertical: true)
                if model.refusalOffersDashboard, let url = model.dashboardURL {
                    Button(ListeningStrings.openTheDashboard) { openURL(url) }
                        .font(.subheadline)
                        .foregroundStyle(Color.brandInk)
                        .frame(minHeight: 44)
                }
            }
        }
    }

    @ViewBuilder
    private func actionButton(_ action: PhoneAction) -> some View {
        switch action {
        case .notMe:
            Button(ListeningStrings.notMe) { model.press(person, claim: false, onPeopleChanged: onPeopleChanged) }
                .buttonStyle(.bordered)
                .disabled(model.busy)
                .frame(minHeight: 44)
        case .itsTheirsInstead:
            Button(ListeningStrings.itsTheirsInstead(person.name)) {
                model.press(person, claim: true, onPeopleChanged: onPeopleChanged)
            }
            .buttonStyle(.bordered)
            .disabled(model.busy)
            .frame(minHeight: 44)
        case .thisIsMe:
            Button(ListeningStrings.thisIsMe) { model.press(person, claim: true, onPeopleChanged: onPeopleChanged) }
                .buttonStyle(BrandFillButtonStyle())
                .disabled(model.busy)
                .frame(minHeight: 44)
                .accessibilityLabel(ListeningStrings.thisIsMeDescription(person.name))
        }
    }

    // MARK: - listening history

    private var listening: some View {
        let counted = model.summary.data.map { !$0.off && $0.count(for: person.id) > 0 } ?? false
        return VStack(alignment: .leading, spacing: 6) {
            Text(ListeningStrings.listeningHistoryLabel)
                .font(.headline)
                .accessibilityAddTraits(.isHeader)
            Text(model.line(for: person))
                .font(.subheadline)
                .foregroundStyle(counted ? Color.primary : Color.fgMuted)
                .fixedSize(horizontal: false, vertical: true)
            if model.showsClear(for: person) {
                Button(ListeningStrings.clear, role: .destructive) { model.askToClear(person) }
                    .buttonStyle(.bordered)
                    .disabled(model.clearing)
                    .frame(minHeight: 44)
                    .accessibilityLabel(ListeningStrings.clearDescription(person.name))
            }
            if let refused = model.clearRefused {
                Text(refused)
                    .font(.footnote)
                    .foregroundStyle(Color.errInk)
                    .fixedSize(horizontal: false, vertical: true)
            }
            Text(ListeningStrings.adminsOnly)
                .font(.caption)
                .foregroundStyle(Color.fgMuted)
                .fixedSize(horizontal: false, vertical: true)
        }
    }
}
