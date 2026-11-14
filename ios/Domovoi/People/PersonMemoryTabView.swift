import DomovoiKit
import SwiftUI

/// The memory tab: what the house remembers, what it likes, and what it has
/// been told to prefer.
///
/// `MemoryTab` (`PersonTabs.kt:70`) — three sections in one list. **This is
/// the half of People a phone can really do**: adding a memory, approving or
/// rejecting an extracted one, forgetting one, and adding or forgetting a
/// favourite are all `require_device` (`people.py:463`, `:500`, `:553`,
/// `:619`, `:659`), which a paired phone passes. Nothing here is a control
/// that 401s.
///
/// Preferences are **read-only**, as Android's are. `PATCH …/preferences` is
/// device tier and would work, but Android offers no editor and a JSONB bag
/// with no schema is not something to invent one for — NIGHT-RULES §6's
/// Android-faithful default, recorded in DECISIONS.md.
///
/// Every write is a method on ``PersonDetailModel``, which outlives the row
/// that called it: a `Task` started inside a `LazyVStack` row dies when the
/// row is recycled, and that is a `POST` that silently did not happen.
struct PersonMemoryTabView: View {

    let detail: PersonDetailModel

    /// The confirmation is ``PeopleView``'s, for the two reasons its own
    /// comment gives — a dialog centred inside a scrolling tab, and state
    /// held in a recyclable row.
    @Binding var forgetting: PersonForgetAsk?

    var body: some View {
        if detail.memoryTabIsLoading {
            ProgressView()
                .frame(maxWidth: .infinity, alignment: .center)
                .padding(.vertical, 24)
                .accessibilityLabel(PeopleStrings.memories)
        } else {
            VStack(alignment: .leading, spacing: 12) {
                memories
                favorites
                preferences
            }
        }
    }

    // MARK: - memories

    @ViewBuilder private var memories: some View {
        PersonSectionHeader(title: PeopleStrings.memories, meta: detail.memoryCounts)
        addMemoryRow
        if !detail.pendingMemories.isEmpty {
            // The band over the extracted rows (`PersonTabs.kt:194`). It is
            // the one thing that says these were taken from a conversation
            // rather than typed, which is why the pending rows carry
            // `approve` and `reject` and the active ones do not.
            Text(PeopleStrings.pendingBand)
                .font(.caption)
                .foregroundStyle(Color.fgMuted)
                .fixedSize(horizontal: false, vertical: true)
                .frame(maxWidth: .infinity, alignment: .leading)
            LazyVStack(alignment: .leading, spacing: 8) {
                ForEach(detail.pendingMemories) { memory in
                    PersonMemoryRow(memory: memory, detail: detail, pending: true) {
                        forgetting = .memory(memory)
                    }
                    Divider()
                }
            }
        }
        if let why = detail.errors.memories {
            // F-A008: the read failed, so it says so and offers the retry.
            // **Never** the empty note — "no memories yet" about a house
            // whose memories route is 500ing is a claim about the person.
            PersonErrorCard(why: why) { detail.refresh() }
        } else if detail.showsNoMemoriesNote {
            PersonCentredNote(PeopleStrings.noMemoriesYet)
        } else {
            LazyVStack(alignment: .leading, spacing: 8) {
                ForEach(detail.activeMemories) { memory in
                    PersonMemoryRow(memory: memory, detail: detail, pending: false) {
                        forgetting = .memory(memory)
                    }
                    Divider()
                }
            }
        }
        if !detail.rejectedMemories.isEmpty {
            rejectedDisclosure
        }
    }

    /// The add field and its `save` (`PersonTabs.kt:168-188`).
    ///
    /// Single-line, as Android's is (`:182`), so it keeps a `submitLabel`:
    /// the prohibition on one is about a `TextField(axis: .vertical)`, whose
    /// Return key makes a newline and whose `onSubmit` never fires.
    private var addMemoryRow: some View {
        HStack(spacing: 8) {
            TextField(
                PeopleStrings.addMemoryPlaceholder,
                text: Binding(get: { detail.newMemory }, set: { detail.typedMemory($0) })
            )
            .textFieldStyle(.roundedBorder)
            .autocorrectionDisabled()
            .submitLabel(.done)
            .onSubmit { detail.saveMemory() }
            .accessibilityLabel(PeopleStrings.addMemoryPlaceholder)
            // Android's filled `Button` (`PersonTabs.kt:185`), drawn by
            // ``BrandFillButtonStyle`` (unit mx-ink). The untinted `.bordered`
            // it replaces took the app's ``Color/brandInk`` tint
            // (`DomovoiApp.swift:232`) and read it on a wash of itself, about
            // 4.06:1 on a card; disabled, it is ``Color/fgMuted`` with no fill.
            Button(PeopleStrings.save) { detail.saveMemory() }
                .buttonStyle(BrandFillButtonStyle())
                // Disabled while blank, as Android's is — and also while a
                // write is in flight: the memories route does **not** upsert
                // (`people.py:461`), so a second tap on a slow house would
                // save the same sentence twice.
                .disabled(!detail.canSaveMemory)
                .accessibilityLabel(PeopleStrings.saveMemoryControl)
        }
    }

    /// `"1 rejected (hidden)"` (`PersonTabs.kt:247`), and the rows behind it.
    @ViewBuilder private var rejectedDisclosure: some View {
        Button {
            detail.toggleRejected()
        } label: {
            HStack(spacing: 6) {
                Image(systemName: detail.showRejected ? "chevron.down" : "chevron.right")
                    .font(.caption)
                    .foregroundStyle(Color.fgMuted)
                Text(PeopleStrings.rejectedHidden(detail.rejectedMemories.count))
                    .font(.caption)
                    .foregroundStyle(Color.fgMuted)
                    .fixedSize(horizontal: false, vertical: true)
            }
            .frame(minHeight: 44)
            .contentShape(.rect)
        }
        .buttonStyle(.plain)
        .accessibilityAddTraits(.isButton)
        if detail.showRejected {
            LazyVStack(alignment: .leading, spacing: 8) {
                ForEach(detail.rejectedMemories) { memory in
                    PersonMemoryRow(memory: memory, detail: detail, pending: false) {
                        forgetting = .memory(memory)
                    }
                    Divider()
                }
            }
        }
    }

    // MARK: - favourites

    @ViewBuilder private var favorites: some View {
        PersonSectionHeader(title: PeopleStrings.favorites, meta: detail.favoriteCounts)
        addFavoriteRow
        if let why = detail.errors.favorites {
            PersonErrorCard(why: why) { detail.refresh() }
        } else if detail.favoriteRows.isEmpty {
            PersonCentredNote(PeopleStrings.noFavoritesYet)
        } else {
            LazyVStack(alignment: .leading, spacing: 8) {
                ForEach(detail.favoriteGroups) { group in
                    PersonFavoriteGroupRow(group: group) { favorite in
                        forgetting = .favorite(favorite)
                    }
                    Divider()
                }
            }
        }
    }

    /// `kind` + `value` + `save` (`PersonTabs.kt:265-293`).
    ///
    /// A column rather than Android's row of three: a 110dp `kind` field, a
    /// `value` field and a button do not fit across a phone even at the
    /// ordinary type size, and the fields are where a `fixedSize` cannot
    /// help.
    private var addFavoriteRow: some View {
        VStack(alignment: .leading, spacing: 8) {
            TextField(
                PeopleStrings.favoriteKindPlaceholder,
                text: Binding(
                    get: { detail.newFavoriteKind },
                    set: { detail.typedFavoriteKind($0) }
                )
            )
            .textFieldStyle(.roundedBorder)
            .autocorrectionDisabled()
            .textInputAutocapitalization(.never)
            .accessibilityLabel(PeopleStrings.favoriteKindPlaceholder)
            HStack(spacing: 8) {
                TextField(
                    PeopleStrings.favoriteValuePlaceholder,
                    text: Binding(
                        get: { detail.newFavoriteValue },
                        set: { detail.typedFavoriteValue($0) }
                    )
                )
                .textFieldStyle(.roundedBorder)
                .autocorrectionDisabled()
                .submitLabel(.done)
                .onSubmit { detail.saveFavorite() }
                .accessibilityLabel(PeopleStrings.favoriteValuePlaceholder)
                // Android's filled `Button` (`PersonTabs.kt:290-293`), for the
                // memory row's reason above (unit mx-ink).
                Button(PeopleStrings.save) { detail.saveFavorite() }
                    .buttonStyle(BrandFillButtonStyle())
                    // Both fields, as Android requires both: a `save` that
                    // could only ever 422 is a control that cannot do what it
                    // says.
                    .disabled(!detail.canSaveFavorite)
                    .accessibilityLabel(PeopleStrings.saveFavoriteControl)
            }
        }
    }

    // MARK: - preferences

    @ViewBuilder private var preferences: some View {
        PersonSectionHeader(title: PeopleStrings.preferences, meta: detail.preferenceCounts)
        if let why = detail.errors.preferences {
            PersonErrorCard(why: why) { detail.refresh() }
        } else if detail.preferenceRows.isEmpty {
            PersonCentredNote(PeopleStrings.noPreferencesYet)
        } else {
            LazyVStack(alignment: .leading, spacing: 8) {
                ForEach(detail.preferenceRows) { row in
                    PersonPreferenceRow(row: row)
                    Divider()
                }
            }
        }
    }
}

/// One memory: what it says, where it came from, and what can be done to it.
///
/// `MemoryRowView` (`PersonTabs.kt:371`). A pending row gets `approve` and
/// `reject`; every row gets `forget`, which asks first.
struct PersonMemoryRow: View {

    let memory: PersonMemory
    let detail: PersonDetailModel
    let pending: Bool
    let onForget: () -> Void

    @Environment(\.dynamicTypeSize) private var typeSize

    var body: some View {
        VStack(alignment: .leading, spacing: 4) {
            Text(memory.body)
                .font(.subheadline)
                .fixedSize(horizontal: false, vertical: true)
                .multilineTextAlignment(.leading)
            Text(memory.metaLine(nowMs: Int64(Date().timeIntervalSince1970 * 1000)))
                .font(.caption)
                .foregroundStyle(Color.fgMuted)
                .fixedSize(horizontal: false, vertical: true)
            controls
        }
        .frame(maxWidth: .infinity, alignment: .leading)
        // Pending rows are washed with the brand tint on Android
        // (`PersonTabs.kt:383`). The tint here is `brandInk` at low opacity
        // rather than ``Color/brand``, which is 2.05:1 and would put the row's
        // own words on a surface no contrast check can pass.
        .background(
            RoundedRectangle(cornerRadius: 8)
                .fill(pending ? Color.brandInk.opacity(0.10) : Color.clear)
        )
    }

    @ViewBuilder private var controls: some View {
        let buttons = Group {
            if pending {
                Button {
                    detail.setStatus(memory, to: .active)
                } label: {
                    label(PeopleStrings.approve, Color.okInk)
                }
                .buttonStyle(.plain)
                .accessibilityLabel(
                    PeopleStrings.memoryControl(PeopleStrings.approve, memory.body)
                )
                Button {
                    detail.setStatus(memory, to: .rejected)
                } label: {
                    label(PeopleStrings.reject, Color.fgMuted)
                }
                .buttonStyle(.plain)
                .accessibilityLabel(
                    PeopleStrings.memoryControl(PeopleStrings.reject, memory.body)
                )
            }
            Button(action: onForget) {
                label(PeopleStrings.forget, Color.errInk)
            }
            .buttonStyle(.plain)
            .accessibilityLabel(PeopleStrings.memoryControl(PeopleStrings.forget, memory.body))
        }
        // Three words in a row fit at the ordinary size and not at AX5, where
        // `approve` alone is most of the width.
        if typeSize.isAccessibilitySize {
            VStack(alignment: .leading, spacing: 0) { buttons }
        } else {
            HStack(spacing: 14) { buttons }
        }
    }

    private func label(_ word: String, _ ink: Color) -> some View {
        Text(word)
            .font(.subheadline)
            .foregroundStyle(ink)
            .fixedSize(horizontal: false, vertical: true)
            .frame(minHeight: 44, alignment: .center)
            .contentShape(.rect)
    }
}

/// One kind of favourite and its values — `FavKindRow`
/// (`PersonTabs.kt:407`).
///
/// Android's close glyph is 12dp with `contentDescription = "forget"`: under
/// any tap-target rule, and ambiguous when a row has four pills. Here each
/// pill's control is 44 points and its label names **which** favourite it
/// forgets (``PeopleStrings/forgetFavorite(kind:value:)``).
struct PersonFavoriteGroupRow: View {

    let group: PersonFavoriteGroup
    let onForget: (PersonFavorite) -> Void

    @Environment(\.dynamicTypeSize) private var typeSize
    @ScaledMetric(relativeTo: .body) private var kindWidth: CGFloat = 90

    var body: some View {
        let kind = Text(group.kind)
            .font(.caption)
            .foregroundStyle(Color.fgMuted)
            .fixedSize(horizontal: false, vertical: true)
            .multilineTextAlignment(.leading)
        if typeSize.isAccessibilitySize {
            VStack(alignment: .leading, spacing: 6) {
                kind
                values
            }
        } else {
            HStack(alignment: .top, spacing: 12) {
                kind.frame(width: kindWidth, alignment: .leading)
                values
                Spacer(minLength: 0)
            }
        }
    }

    /// One pill per value. A `VStack` rather than a wrapping flow: SwiftUI has
    /// no `FlowRow`, and a value like `oat flat white` beside a 44-point
    /// control is most of a phone's width anyway.
    private var values: some View {
        VStack(alignment: .leading, spacing: 6) {
            ForEach(group.favorites) { favorite in
                HStack(spacing: 6) {
                    Text(favorite.value)
                        .font(.footnote)
                        .fixedSize(horizontal: false, vertical: true)
                        .multilineTextAlignment(.leading)
                    Button {
                        onForget(favorite)
                    } label: {
                        Image(systemName: "xmark")
                            .font(.caption2)
                            .foregroundStyle(Color.fgMuted)
                            .frame(width: 44, height: 44)
                            .contentShape(.rect)
                    }
                    .buttonStyle(.plain)
                    .accessibilityLabel(
                        PeopleStrings.forgetFavorite(kind: favorite.kind, value: favorite.value)
                    )
                }
                .padding(.leading, 10)
                .overlay(
                    Capsule().stroke(Color.fgMuted.opacity(0.35), lineWidth: 1)
                )
            }
        }
    }
}

/// One preference, read-only: the key and ``prettyPersonPref(_:)`` of its
/// value (`PersonTabs.kt:325-342`).
struct PersonPreferenceRow: View {

    let row: PersonPreference

    @Environment(\.dynamicTypeSize) private var typeSize
    @ScaledMetric(relativeTo: .body) private var keyWidth: CGFloat = 120

    var body: some View {
        let key = Text(row.key)
            .font(.caption)
            .foregroundStyle(Color.fgMuted)
            .fixedSize(horizontal: false, vertical: true)
            .multilineTextAlignment(.leading)
        let value = Text(row.display)
            .font(.footnote)
            .fixedSize(horizontal: false, vertical: true)
            .multilineTextAlignment(.leading)
        Group {
            if typeSize.isAccessibilitySize {
                VStack(alignment: .leading, spacing: 2) {
                    key
                    value
                }
            } else {
                HStack(alignment: .top, spacing: 12) {
                    key.frame(width: keyWidth, alignment: .leading)
                    value
                    Spacer(minLength: 0)
                }
            }
        }
        .frame(maxWidth: .infinity, alignment: .leading)
        // "wake_volume, 0.7" as one stop.
        .accessibilityElement(children: .combine)
    }
}
