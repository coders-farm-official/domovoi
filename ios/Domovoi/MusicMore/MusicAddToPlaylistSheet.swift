import DomovoiKit
import SwiftUI

/// "Add to playlist" for one track: every playlist, which of them hold it,
/// and one tap to toggle.
///
/// `AddToPlaylistSheet.kt`. The three things this screen has to get right are
/// all the model's and all have their reason written there:
/// **Favorites routes through the library PATCH** (the playlist routes 400
/// for id 0), **a 409 is not a failure**, and the toast names the row that
/// was tapped rather than whatever row the refresh left in that position.
///
/// ### What is not, and why there is no control for it
///
/// * **Bulk add** — a checkbox per library row and a bar over the list
///   (`LibraryTab.kt:229`). Left out for the whole item: the iOS library row
///   is a tap-to-open-sheet row that already carries two controls, and a
///   second interaction model on top of it is the opposite of
///   [[feedback-mass-appeal-not-power-user]]. This sheet reaches every track
///   one at a time (DECISIONS).
/// * **Removing a track from a playlist that is not open.** A tap on a row
///   this track is already in **does** remove it, which is Android's toggle;
///   there is no separate control.
struct MusicAddToPlaylistSheet: View {

    let add: MusicAddToPlaylistModel

    @Environment(\.dismiss) private var dismiss

    var body: some View {
        NavigationStack {
            ScrollView {
                VStack(alignment: .leading, spacing: 12) {
                    Text(add.track.displayTitle)
                        .font(.footnote)
                        .foregroundStyle(Color.fgMuted)
                        .fixedSize(horizontal: false, vertical: true)
                    if add.isFirstLoad {
                        ProgressView()
                            .frame(maxWidth: .infinity, alignment: .center)
                            .accessibilityLabel(MusicMoreStrings.addToPlaylist)
                    }
                    if let trouble = add.all.error {
                        VStack(alignment: .leading, spacing: 8) {
                            Text(trouble)
                                .font(.footnote)
                                .foregroundStyle(Color.errInk)
                                .fixedSize(horizontal: false, vertical: true)
                            HomeLink(MusicStrings.retry) { add.all.refresh() }
                        }
                    }
                    ForEach(add.playlists) { one in
                        row(one)
                    }
                    if let note = add.emptyNote {
                        Text(note)
                            .font(.footnote)
                            .foregroundStyle(Color.fgMuted)
                            .fixedSize(horizontal: false, vertical: true)
                            .frame(maxWidth: .infinity, alignment: .center)
                            .padding(.vertical, 16)
                    }
                    Divider()
                    create
                }
                .padding(16)
                .frame(maxWidth: .infinity, alignment: .leading)
            }
            .navigationTitle(MusicMoreStrings.addToPlaylist)
            .navigationBarTitleDisplayMode(.inline)
            .toolbar {
                ToolbarItem(placement: .topBarTrailing) {
                    Button(MusicStrings.close) { dismiss() }
                }
            }
        }
        .task { add.start() }
        .onDisappear { add.stop() }
    }

    /// One playlist row. A tap toggles; the tick and the second line are the
    /// only things that say which way it will go.
    private func row(_ playlist: Playlist) -> some View {
        let inIt = add.holdsTrack(playlist)
        return Button {
            add.toggle(playlist)
        } label: {
            HStack(alignment: .top, spacing: 12) {
                MusicPlaylistBadge(playlist: playlist)
                VStack(alignment: .leading, spacing: 2) {
                    Text(playlist.name)
                        .font(.subheadline)
                        .fixedSize(horizontal: false, vertical: true)
                    Text(add.subtitle(playlist))
                        .font(.caption)
                        .foregroundStyle(Color.fgMuted)
                        .fixedSize(horizontal: false, vertical: true)
                }
                .frame(maxWidth: .infinity, alignment: .leading)
                Image(systemName: inIt ? "checkmark.circle.fill" : "plus.circle")
                    .frame(minWidth: 44, minHeight: 44)
                    // Decorative: the row's own combined label already says
                    // the name and `in this playlist`, and the tick repeated
                    // after it is noise.
                    .accessibilityHidden(true)
                    .foregroundStyle(inIt ? Color.brandInk : Color.fgMuted)
            }
            .contentShape(.rect)
        }
        .buttonStyle(.plain)
        .disabled(add.busy)
        .accessibilityElement(children: .combine)
        // `isSelected` is how a tick is said rather than drawn: colour and a
        // glyph are both invisible to VoiceOver.
        .accessibilityAddTraits(inIt ? [.isButton, .isSelected] : .isButton)
    }

    /// Create a playlist and put this track in it — two calls, one gesture
    /// (`AddToPlaylistSheet.kt:116`).
    private var create: some View {
        VStack(alignment: .leading, spacing: 8) {
            Text(MusicMoreStrings.createNewPlaylist)
                .font(.caption)
                .foregroundStyle(Color.fgMuted)
                .fixedSize(horizontal: false, vertical: true)
            // A single-line field, so `submitLabel(.done)` is honest here —
            // its Return key really does end the entry. The multi-line rule
            // is about `axis: .vertical` fields, which this is not.
            TextField(MusicMoreStrings.playlistNamePlaceholder, text: newName)
                .textFieldStyle(.roundedBorder)
                .textInputAutocapitalization(.words)
                .autocorrectionDisabled()
                .submitLabel(.done)
                .onSubmit { add.create() }
                .accessibilityLabel(MusicMoreStrings.playlistNamePlaceholder)
            if add.mayCreate {
                // Drawn only when it can do something: a `create` over an
                // empty field would refuse on every tap.
                HomeLink(MusicMoreStrings.create) { add.create() }
            }
        }
    }

    /// Written out rather than `@Bindable`: the model is held one level up as
    /// an optional `@State`, and a binding through an optional is a binding
    /// to a value that can vanish — the same reason ``MusicFilters`` spells
    /// its four out.
    private var newName: Binding<String> {
        Binding(get: { add.newName }, set: { add.newName = $0 })
    }
}
