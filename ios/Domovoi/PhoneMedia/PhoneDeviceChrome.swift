import DomovoiKit
import SwiftUI

/// The pieces every device page draws the same way: the header, the library
/// picker, the filter, the three emptinesses and the list's foot.
///
/// `ui/screens/local/LocalMediaScreens.kt` draws each of these inline in both
/// of its screens, which is why the two drifted — the videos grid's loading
/// line and the music list's are different sentences for the same state. They
/// are small views here so that the device sides of Music, Videos and Files
/// cannot drift that way, and so that each is one place to fix a contrast or
/// a Dynamic Type finding.
///
/// Not one scaffold taking the rows as a closure: a `LazyVStack` of rows and
/// a `LazyVGrid` of tiles want different containers, and a generic wrapper
/// over both would make the near-end guard the wrapper's job rather than the
/// screen's.

/// A device page's title and subtitle.
struct PhoneDeviceHeader: View {

    let title: String
    let subtitle: String

    var body: some View {
        VStack(alignment: .leading, spacing: 4) {
            Text(title)
                .font(.title2)
                .accessibilityAddTraits(.isHeader)
                .fixedSize(horizontal: false, vertical: true)
            Text(subtitle)
                .font(.footnote)
                .foregroundStyle(Color.fgMuted)
                .fixedSize(horizontal: false, vertical: true)
        }
        .frame(maxWidth: .infinity, alignment: .leading)
    }
}

/// Which phone library is being read, when there is more than one.
///
/// **Not drawn for a single library**, which is the common case on a phone
/// with one folder added: a picker with one item in it is a control that
/// cannot do anything.
struct PhoneLibraryPicker: View {

    let list: PhoneMediaListModel

    var body: some View {
        if list.libraries.count > 1 {
            Menu {
                ForEach(list.libraries) { library in
                    Button {
                        list.select(library.id)
                    } label: {
                        VStack(alignment: .leading) {
                            Text(library.label)
                            // The reason a row cannot be opened, in the menu
                            // where somebody is choosing it, rather than
                            // after the choice.
                            Text(list.isGone(library)
                                ? PhoneMediaStrings.folderNoLongerReadable
                                : library.pickerDetail)
                        }
                    }
                }
            } label: {
                HStack(spacing: 10) {
                    Image(systemName: FilesView.libraryGlyph(list.currentLibrary?.icon ?? "folder"))
                        .foregroundStyle(Color.brandInk)
                    Text(list.currentLibrary?.label ?? FileStrings.selectLibrary)
                        .font(.headline)
                        .fixedSize(horizontal: false, vertical: true)
                        .multilineTextAlignment(.leading)
                    Spacer(minLength: 4)
                    Image(systemName: "chevron.down")
                        .font(.footnote)
                        .foregroundStyle(Color.fgMuted)
                }
                .padding(.horizontal, 12)
                .frame(minHeight: 44)
                .frame(maxWidth: .infinity, alignment: .leading)
                .overlay(
                    RoundedRectangle(cornerRadius: 8)
                        .stroke(Color.fgMuted.opacity(0.35), lineWidth: 1)
                )
                .contentShape(.rect)
            }
            .accessibilityLabel(FileStrings.chooseLibrary)
            .accessibilityValue(list.currentLibrary?.label ?? FileStrings.selectLibrary)
        }
    }
}

/// `filter…` (`LocalMediaScreens.kt:171`), debounced by the model.
struct PhoneFilterField: View {

    let list: PhoneMediaListModel

    var body: some View {
        TextField(
            PhoneMediaStrings.filterPlaceholder,
            text: Binding(get: { list.filter }, set: { list.typed($0) })
        )
        .textFieldStyle(.roundedBorder)
        .autocorrectionDisabled()
        .textInputAutocapitalization(.never)
        // A single-line field, so a `search` return key is honest. The rule
        // this app has broken three times is the other one: a
        // `TextField(axis: .vertical)` gets **no** `submitLabel`, because its
        // Return makes a newline.
        .submitLabel(.search)
        .accessibilityLabel(PhoneMediaStrings.filterPlaceholder)
    }
}

/// A title and a line under it: the empty states, and the stated absences.
struct PhoneDeviceNote: View {

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

/// The foot of a phone list: the next page in flight, the failure with its
/// retry, or a `load more` link.
///
/// The link is drawn **as well as** the scroll trigger, not instead of it
/// (``FilesView``'s own note): the trigger cannot fire for a list the person
/// has stopped scrolling, and VoiceOver's reader does not scroll at all.
struct PhoneListFooter: View {

    let list: PhoneMediaListModel

    var body: some View {
        let state = list.rows.state
        if state.loading, !state.items.isEmpty {
            ProgressView()
                .frame(maxWidth: .infinity, alignment: .center)
        } else if let trouble = state.error {
            VStack(alignment: .leading, spacing: 8) {
                Text(PhoneMediaStrings.folderNoLongerReadable)
                    .font(.subheadline)
                    .fixedSize(horizontal: false, vertical: true)
                // Only when the words differ: a failure whose message IS the
                // line above would be said twice.
                if trouble != PhoneMediaStrings.folderNoLongerReadable {
                    Text(trouble)
                        .font(.footnote)
                        .foregroundStyle(Color.errInk)
                        .fixedSize(horizontal: false, vertical: true)
                }
                HomeLink(FileStrings.retry) { list.rows.retry() }
            }
        } else if state.canLoadMore {
            HomeLink(FileStrings.loadMore) { list.rows.loadMore() }
        }
    }
}
