import DomovoiKit
import SwiftUI

/// One row of the phone's own library: what it is, its size and age, how long
/// it runs, and the one thing this app can do with it.
///
/// `ui/screens/local/LocalMediaScreens.kt:204`'s `LocalTrackRow`, and the
/// device side of `FilesScreen.kt`'s row, which are the same row with a
/// different second line.
///
/// ### What is not, and why there is no control for it
///
/// * **No play.** Android plays a window of the phone's neighbouring tracks
///   on the handset (`LocalMediaScreens.kt:159`). There is no handset audio
///   engine in this app — ``MusicView``'s own doc calls the player "its own
///   milestone" — so there is no play arrow here and
///   ``PhoneMediaStrings/noPhonePlayerYet`` says so once at the foot of the
///   list. A **video** row is tappable, because `AVPlayer` over a local file
///   needs no server and no token.
/// * **No delete, rename or move.** Android gets all three free from its
///   `FileSource` interface; this side lists and reads only
///   (``PhoneFolderSource``'s own "What is not", and DECISIONS).
/// * **No upload on a row over the ceiling** — ``PhoneUploadTooBigNote``
///   instead, with the size in it.
struct PhoneMediaRowView: View {

    let item: PhoneMediaItem

    /// `"12 MB · 3d ago"`, from the model's clock.
    let meta: String

    /// Nil in the local shell, and on a page with no server.
    let uploads: PhoneUploadModel?

    /// What tapping the row does, or nil when a tap would do nothing: a
    /// folder opens, a video plays, everything else has no action and is
    /// **not drawn as a button**. A greyed-out row reads as a control that
    /// stopped working (``FileRowView``'s note).
    let onTap: (() -> Void)?

    /// Grows with the type size — never a flat `frame(width:)`. A 26-point
    /// icon column drawn at AX5 is about twice that wide and was found
    /// straight across a row's own words one screen earlier.
    @ScaledMetric(relativeTo: .body) private var art: CGFloat = 40

    var body: some View {
        HStack(spacing: 10) {
            if let onTap {
                Button(action: onTap) { face }
                    .buttonStyle(.plain)
                    .accessibilityLabel(item.name)
                    .accessibilityValue(meta)
                    .accessibilityHint(item.isDir ? FileStrings.folder : VideoStrings.play)
            } else {
                face.accessibilityElement(children: .combine)
            }
            Spacer(minLength: 4)
            uploadControl
        }
        .padding(.vertical, 4)
    }

    private var face: some View {
        HStack(spacing: 10) {
            Image(systemName: Self.glyph(item))
                .font(.body)
                .frame(width: art)
                .foregroundStyle(Color.fgMuted)
                .accessibilityHidden(true)
            VStack(alignment: .leading, spacing: 2) {
                Text(item.name)
                    .font(.subheadline)
                    .fixedSize(horizontal: false, vertical: true)
                    .multilineTextAlignment(.leading)
                HStack(spacing: 6) {
                    Text(meta)
                        .font(.caption)
                        .foregroundStyle(Color.fgMuted)
                    if item.durationSec != nil {
                        // The dash Android shows for a row with no duration
                        // (`LocalMediaScreens.kt:249`) is **not** drawn for a
                        // document or a folder: a `.txt` has no duration to
                        // be missing, and a bare `—` beside it reads as a
                        // value that failed to load.
                        Text(item.durationText)
                            .font(.caption)
                            .foregroundStyle(Color.fgMuted)
                    }
                }
                .fixedSize(horizontal: false, vertical: true)
            }
        }
        .contentShape(.rect)
    }

    @ViewBuilder private var uploadControl: some View {
        if let uploads, !item.isDir {
            if uploads.offersUpload(item) {
                PhoneUploadRowButton(
                    state: uploads.state(for: PhoneUploadKey.of(item, uploads.kind)),
                    // The tap calls the **model**, which runs the upload on
                    // itself. Nothing on this row holds the task.
                    onTap: { uploads.upload(item) }
                )
            } else {
                PhoneUploadTooBigNote(size: item.sizeBytes)
            }
        }
    }

    /// A phone row's glyph. ``FilesView/entryGlyph(_:)``'s vocabulary, over
    /// ``PhoneMediaKind`` rather than the server's string — the same cases,
    /// because the raw values are deliberately the same words.
    static func glyph(_ item: PhoneMediaItem) -> String {
        switch item.kind {
        case .folder: return "folder.fill"
        case .audio: return "music.note"
        case .video: return "film"
        case .image: return "photo"
        case .pdf: return "doc.richtext"
        case .docOffice: return "tablecells"
        case .docText: return "doc.plaintext"
        case .other: return "doc"
        }
    }
}
