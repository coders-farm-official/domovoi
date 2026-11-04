import DomovoiKit
import SwiftUI

/// One row of the phone's own library: what it is, its size and age, how long
/// it runs, and the one thing this app can do with it.
///
/// `ui/screens/local/LocalMediaScreens.kt:204`'s `LocalTrackRow`, and the
/// device side of `FilesScreen.kt`'s row, which are the same row with a
/// different second line.
///
/// ### Playing arrived in the 2026-11-03 batch
///
/// An audio row in a picked folder plays on the handset, as Android's does
/// (`LocalMediaScreens.kt:158-166`): the tap is this row's, the queue is the
/// neighbouring playable tracks (``PhoneMediaListModel/playQueue(around:)``)
/// and the player is the app's one (``ShellModel/player``). It is the
/// **caller** that decides — the device side of Music passes the tap, the
/// device side of Files does not, which is Android's split too — so this row
/// has no new control on it, only a new ``hint(_:)`` case. A **video** row was
/// already tappable, because `AVPlayer` over a local file needs no server and
/// no token.
///
/// ### What is not, and why there is no control for it
///
/// * **No delete, rename or move.** Android gets all three free from its
///   `FileSource` interface; this side lists and reads only
///   (``PhoneFolderSource``'s own "What is not", and DECISIONS).
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
                    .accessibilityHint(Self.hint(item))
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

    /// The upload button, for any file at all.
    ///
    /// **No size branch any more.** ``PhoneUploadModel/offersUpload(_:)`` is
    /// `!item.isDir` since the transfers unit's streaming upload took the
    /// 64 MiB ceiling out, so the `else` that drew `PhoneUploadTooBigNote`
    /// with the size in it was unreachable code describing a rule that no
    /// longer exists. Kept as an `if` on `offersUpload` rather than inlined,
    /// because that is still the model's question to answer and a later rule
    /// would go there.
    @ViewBuilder private var uploadControl: some View {
        if let uploads, !item.isDir, uploads.offersUpload(item) {
            PhoneUploadRowButton(
                state: uploads.state(for: PhoneUploadKey.of(item, uploads.kind)),
                // The tap calls the **model**, which runs the upload on
                // itself. Nothing on this row holds the task.
                onTap: { uploads.upload(item) }
            )
        }
    }

    /// What tapping this row does, said to VoiceOver.
    ///
    /// **It follows the row's kind, not `isDir`.** The device side of Files
    /// taps three kinds (``PhoneDeviceFilesView/tap(_:_:)``: a folder, a
    /// picture and a video), so the two-way `isDir ? folder : play` announced
    /// "play" on a photograph. Anything else has no tap and therefore no
    /// hint, which is why the last case is the folder's rather than a
    /// fallback that could lie.
    static func hint(_ item: PhoneMediaItem) -> String {
        switch item.kind {
        case .video: return VideoStrings.play
        case .audio: return PlayerStrings.play
        case .image: return PhoneMediaStrings.lookAtThePicture
        default: return FileStrings.folder
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
