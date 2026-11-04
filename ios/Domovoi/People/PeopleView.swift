import DomovoiKit
import SwiftUI

/// People: who the house knows, what it remembers about them, and what it has
/// heard them say.
///
/// `android/.../ui/screens/people/PeopleScreen.kt`, with what a phone cannot
/// do **left out rather than stubbed**.
///
/// ### What is here
///
/// The household roster with its filter, the green dot for anyone heard in
/// the last five minutes and the amber one for two people who share a name;
/// the voice denylist's count and the paragraph explaining what is and is not
/// kept; and, per person, four tabs — the profile and its four numbers, the
/// memories and favourites a phone may add to and prune, the sessions with
/// each one's turns, and the whole conversation log with a search over it.
///
/// ### What is not, and why there is no control for it
///
/// **Renaming** and **forgetting a person.** `PATCH /api/people/{id}` and
/// `DELETE /api/people/{id}` are `require_admin_mutation` (`people.py:145`,
/// `:355`), which is Bearer-only — a dashboard cookie is 403 and anything
/// else 401 (`admin_auth.py:1288-1305`). This app holds a household device
/// token and never a Bearer, so Android's `rename` button, its rename dialog
/// and its `forget person` button with the type-the-name confirmation would
/// **401 every single time** from a phone. None of the four is drawn, and the
/// profile says where that work is done instead
/// (``PeopleStrings/renamingIsOnTheDashboard``), under the owner's standing
/// rule that a control which cannot do what it says is not drawn
/// ([[no-misleading-controls]]). The forget is the worse of the two to fake:
/// it cascades across the household's whole history.
///
/// **Voice enrolment, and the microphone.** Nothing in Android's People
/// records anything — it shows `voice_profile_count` as a number and never
/// calls `GET /api/people/{id}/profiles` or the per-sample delete. So this
/// screen reads neither, asks for no microphone, and `ios/project.yml` is
/// untouched. A phone-sourced sample reaches the server through the chat mic
/// (`VoiceProfile.source == "phone"`, `schemas.py:325`), which is Chat's
/// business.
///
/// **Changing a preference** (device tier, so it would work — but Android's
/// tab is read-only, and inventing an editor for a JSONB bag with no schema
/// is not a port) and **deleting a denylist entry** (admin tier, and Android
/// does not offer it either).
///
/// Each of these is written up with its reasoning in
/// `design-notes/ios/overnight/people/DECISIONS.md`.
///
/// ### Three screens, one route
///
/// Android's compact branch is a three-way `when` — the denylist, else the
/// selected person, else the roster (`PeopleScreen.kt:89-120`) — and that is
/// what this is, switched on ``PeopleModel``'s own state rather than on a
/// pushed destination. The model holds `selectedID` and `denylistOpen`
/// precisely so that *which of the three is up* is a value something can be
/// asked about, and the two leaf screens carry a `back` control of their own,
/// as Android's do. (MAP.md planned a `navigationDestination`; the swap is in
/// DECISIONS.md, "The detail is a branch, not a push".)
///
/// The expanded two-pane layout (`PeopleScreen.kt:121-156`) is not ported:
/// every iPhone is compact, so the rail's *"pick someone from the list"*
/// empty state has nowhere to be.
struct PeopleView: View {

    let model: ShellModel

    @State private var people: PeopleModel?

    /// **One** detail model, pointed at whoever is selected, rather than one
    /// per row. That is the arrangement ``PersonDetailModel`` is built for —
    /// it captures its generation before every `await` so that person A's
    /// memories cannot land under person B — and the arrangement a per-row
    /// model would have hidden rather than fixed.
    @State private var detail: PersonDetailModel?

    /// The confirmation that is up, held **here** rather than in the tab that
    /// raises it.
    ///
    /// Two reasons, both learned on this track. A dialog drawn inside a
    /// scrolling tab can centre itself off the top of the screen at an
    /// accessibility type size, which is what ``SettingsPlaceholder`` was
    /// fixed for; and the memory rows are in a `LazyVStack`, so state held in
    /// one of them is state that can be recycled out from under an open
    /// dialog.
    @State private var forgetting: PersonForgetAsk?

    /// `onAppear`/`onDisappear`, for ``HomeView``'s reason: this screen is
    /// inside the workspace's `NavigationStack`, so a pushed destination
    /// fires `onDisappear` while this view stays in the hierarchy.
    @State private var visible = false

    var body: some View {
        ZStack {
            screen
                // **Hidden, not merely behind.** `.isModal` stops VoiceOver
                // navigating out of the card, but the content underneath is
                // still rendered under a scrim and Apple's audit reads it as
                // unreadable text — which is how the modal bug was found on
                // Settings. Nothing behind a confirmation is content.
                .accessibilityHidden(forgetting != nil)
            if let ask = forgetting, let detail {
                DialogCard {
                    PersonForgetCard(
                        ask: ask,
                        onCancel: { forgetting = nil },
                        onConfirm: {
                            forgetting = nil
                            // The write is the model's, never the row's: a
                            // `Task` started in a lazy row is cancelled when
                            // the row is recycled, which is a request that
                            // silently did not happen (NIGHT-RULES §3).
                            switch ask {
                            case .memory(let memory): detail.deleteMemory(memory)
                            case .favorite(let favorite): detail.deleteFavorite(favorite)
                            }
                        }
                    )
                }
            }
        }
        .task {
            // Built here and not in `init`, for ``HomeView``'s reason: the
            // models need the container and a `@State` initialiser runs
            // before the view has one.
            if people == nil {
                people = PeopleModel(in: model.container)
                detail = PersonDetailModel(in: model.container)
            }
            people?.start()
        }
        .onAppear { visible = true }
        .onDisappear {
            visible = false
            people?.stop()
        }
        // **The toasts are the shell's**, drawn over the whole app: a toast
        // about a memory that would not save must outlive a scroll, and does
        // outlive this screen.
        //
        // Every toast on this screen is a write's, so ``PersonDetailModel`` is
        // the only producer. ``PeopleModel`` had a `toast` of its own with
        // nothing ever assigning it — Android does not toast a roster read
        // that failed either (`RosterPane` draws `ErrorState` only), so the
        // property and this observer for it were both removed rather than
        // left as a seam with no caller.
        .onChange(of: detail?.toast) { _, said in
            guard let said else { return }
            model.showToast(said)
            detail?.setToast(nil)
        }
        // The roster's push moved somebody's `last_seen_at`, so the open
        // profile's `live` pill and `last heard` tile move with it — and a
        // person forgotten on the dashboard leaves the detail rather than
        // sitting there for ever (``PeopleModel/selected``).
        .onChange(of: people?.selected) { _, person in
            guard let person else { return }
            detail?.show(person)
        }
        .onChange(of: model.inputs.serverURL) { _, _ in
            guard visible else { return }
            people?.serverChanged()
            detail?.serverChanged()
            forgetting = nil
        }
    }

    @ViewBuilder private var screen: some View {
        if let people, let detail {
            if people.denylistOpen {
                PeopleDenylistView(people: people)
            } else if let person = people.selected {
                PersonDetailView(
                    person: person,
                    people: people,
                    detail: detail,
                    forgetting: $forgetting
                )
            } else {
                roster(people)
            }
        } else {
            ProgressView()
                .frame(maxWidth: .infinity, maxHeight: .infinity)
                .accessibilityLabel(PeopleStrings.title)
        }
    }

    // MARK: - the roster

    private func roster(_ people: PeopleModel) -> some View {
        ScrollView {
            LazyVStack(alignment: .leading, spacing: 12) {
                header(people)
                filterField(people)
                rows(people)
                PeopleDenylistCard(people: people)
            }
            .padding(16)
            .frame(maxWidth: .infinity, alignment: .leading)
        }
    }

    /// The title and `"4 enrolled · 1 heard in the last 5 min"`
    /// (`PeopleScreen.kt:85`).
    ///
    /// The subtitle is the one line on this screen only a **completed** read
    /// can produce, which is why the case that proves the roster landed waits
    /// for it rather than for a row.
    private func header(_ people: PeopleModel) -> some View {
        VStack(alignment: .leading, spacing: 4) {
            Text(PeopleStrings.title)
                .font(.title2)
                .accessibilityAddTraits(.isHeader)
                .fixedSize(horizontal: false, vertical: true)
            Text(people.subtitle)
                .font(.footnote)
                .foregroundStyle(Color.fgMuted)
                .fixedSize(horizontal: false, vertical: true)
        }
    }

    /// `filter by name…` — client-side over rows already in hand
    /// (`PeopleScreen.kt:177`), so there is nothing to debounce.
    ///
    /// Single-line, as Android's is (`PeopleScreen.kt:194`), so it keeps a
    /// `submitLabel`: the prohibition on one is about a `TextField` with a
    /// vertical axis, whose Return key makes a newline.
    private func filterField(_ people: PeopleModel) -> some View {
        TextField(
            PeopleStrings.filterPlaceholder,
            text: Binding(get: { people.filter }, set: { people.typed($0) })
        )
        .textFieldStyle(.roundedBorder)
        .autocorrectionDisabled()
        .textInputAutocapitalization(.never)
        .submitLabel(.search)
        .accessibilityLabel(PeopleStrings.filterPlaceholder)
    }

    /// The roster's four states (``PeopleRosterState``).
    ///
    /// The two empty notes are **not** interchangeable: `nobody enrolled yet`
    /// over a house with four people in it is a lie about the house, which is
    /// why the choice is the model's and not an `if` here.
    @ViewBuilder
    private func rows(_ people: PeopleModel) -> some View {
        switch people.rosterState {
        case .loading:
            ProgressView()
                .frame(maxWidth: .infinity, alignment: .center)
                .padding(.vertical, 24)
                .accessibilityLabel(PeopleStrings.title)
        case .failed(let why):
            PersonErrorCard(why: why) { people.roster.refresh() }
        case .nobodyEnrolled:
            PersonCentredNote(PeopleStrings.nobodyEnrolled)
        case .noMatch:
            PersonCentredNote(PeopleStrings.noMatch)
        case .rows(let shown):
            ForEach(shown) { person in
                PersonRosterRow(person: person, people: people)
                Divider()
            }
        }
    }
}

/// Which confirmation is up on the memory tab.
///
/// One value rather than two optionals, because two booleans for one dialog
/// is how a dialog comes to be shown with nothing to confirm. Both of
/// Android's `ConfirmDialog`s (`PersonTabs.kt:349-368`) are cases of it.
enum PersonForgetAsk: Equatable, Identifiable {

    case memory(PersonMemory)
    case favorite(PersonFavorite)

    var id: String {
        switch self {
        case .memory(let memory): return "m\(memory.id)"
        case .favorite(let favorite): return "f\(favorite.id)"
        }
    }

    /// `forget memory` / `forget favorite` (`PersonTabs.kt:351`, `:361`).
    var title: String {
        switch self {
        case .memory: return PeopleStrings.forgetMemoryTitle
        case .favorite: return PeopleStrings.forgetFavoriteTitle
        }
    }

    /// The body **quotes what is about to go** — `Forget "allergic to
    /// peanuts"?` — because a row's own words are the only way to tell two
    /// memories apart in a confirmation.
    var body: String {
        switch self {
        case .memory(let memory):
            return PeopleStrings.forgetMemoryBody(memory.body)
        case .favorite(let favorite):
            return PeopleStrings.forgetFavoriteBody(kind: favorite.kind, value: favorite.value)
        }
    }
}

/// The confirmation card itself: what is about to be forgotten, and the two
/// ways out.
struct PersonForgetCard: View {

    let ask: PersonForgetAsk
    let onCancel: () -> Void
    let onConfirm: () -> Void

    var body: some View {
        VStack(alignment: .leading, spacing: 12) {
            Text(ask.title)
                .font(.headline)
                .fixedSize(horizontal: false, vertical: true)
            Text(ask.body)
                .font(.subheadline)
                .fixedSize(horizontal: false, vertical: true)
            HStack(spacing: 16) {
                HomeLink(PeopleStrings.cancel, action: onCancel)
                Button(action: onConfirm) {
                    Text(PeopleStrings.forget)
                        .font(.subheadline)
                        .fixedSize(horizontal: false, vertical: true)
                        .frame(minHeight: 44)
                        .contentShape(.rect)
                }
                .buttonStyle(.plain)
                // `errInk`, never `.red` as text: 5.65:1 against 3.55:1.
                .foregroundStyle(Color.errInk)
            }
        }
    }
}

/// A read that failed, with its retry — `ErrorState` (`Kit.kt:282`).
///
/// It replaces the list it belongs to and **never** the empty note: a read
/// that failed must not read as "nothing here yet", which is F-A008.
struct PersonErrorCard: View {

    let why: String
    let onRetry: () -> Void

    var body: some View {
        VStack(alignment: .leading, spacing: 8) {
            Text(PeopleStrings.couldNotReach)
                .font(.subheadline)
                .fixedSize(horizontal: false, vertical: true)
            Text(why)
                .font(.footnote)
                .foregroundStyle(Color.errInk)
                .fixedSize(horizontal: false, vertical: true)
            HomeLink(PeopleStrings.retry, action: onRetry)
        }
        .frame(maxWidth: .infinity, alignment: .leading)
        .padding(.vertical, 8)
    }
}

/// `CenterNote` (`PersonTabs.kt:757`): one quiet sentence where a list would
/// be.
struct PersonCentredNote: View {

    let text: String

    init(_ text: String) { self.text = text }

    var body: some View {
        Text(text)
            .font(.footnote)
            .foregroundStyle(Color.fgMuted)
            .multilineTextAlignment(.center)
            .fixedSize(horizontal: false, vertical: true)
            .frame(maxWidth: .infinity, alignment: .center)
            .padding(.vertical, 20)
    }
}

/// An empty state with a second line under it — `EmptyState`, as the sessions
/// and conversations tabs use it (`PersonTabs.kt:466`, `:660`).
struct PersonEmptyNote: View {

    let title: String
    let detail: String

    var body: some View {
        VStack(alignment: .leading, spacing: 4) {
            Text(title)
                .font(.subheadline)
                .fixedSize(horizontal: false, vertical: true)
            Text(detail)
                .font(.footnote)
                .foregroundStyle(Color.fgMuted)
                .fixedSize(horizontal: false, vertical: true)
        }
        .frame(maxWidth: .infinity, alignment: .leading)
        .padding(.vertical, 8)
    }
}

/// A section's heading and what it counts — `SectionHeader`
/// (`PersonTabs.kt:736`).
struct PersonSectionHeader: View {

    let title: String
    let meta: String

    var body: some View {
        HStack(alignment: .firstTextBaseline, spacing: 8) {
            Text(title)
                .font(.headline)
                .accessibilityAddTraits(.isHeader)
                .fixedSize(horizontal: false, vertical: true)
            Text(meta)
                .font(.caption)
                .foregroundStyle(Color.fgMuted)
                .fixedSize(horizontal: false, vertical: true)
            Spacer(minLength: 0)
        }
        .padding(.top, 8)
    }
}
