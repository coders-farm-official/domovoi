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
/// ### A column at an accessibility type size
///
/// Train I002 flagged `Text clipped` against this row's title and, measured
/// at AX5, it was not a false positive: `quenlo lullaby.mp3` had **79 points
/// of width and 406.7 of height** in a 361-point row, and its meta line 36.3.
/// The two trailing boxes are what took the width — the glyph's
/// `@ScaledMetric` 40 is about 113 points at AX5 and
/// ``PhoneUploadRowButton``'s 48 about 135, leaving the name a column one
/// character wide. Both keep scaling (an icon-only control in fixed points is
/// what that file's own note forbids); they move instead. At an accessibility
/// size the row is a `VStack`, the glyph sits above the name rather than
/// beside it, and the meta and the duration stack — so the name gets the
/// whole row. Below those sizes the layout is unchanged, which is what
/// ``testPhoneMedia_TheDeviceSideTextGrowsAtAX5`` measures either side of.
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

    /// The app's existing pattern for a row that has to change shape
    /// (`AssistantPhoneRowView`, `AlertsHintRowView`, `PhoneUploadRowButton`
    /// itself).
    @Environment(\.dynamicTypeSize) private var typeSize

    var body: some View {
        // A column at an accessibility size: the upload box then sits UNDER
        // the face, left-aligned, instead of taking 135 points of the row's
        // width from the name.
        let layout = typeSize.isAccessibilitySize
            ? AnyLayout(VStackLayout(alignment: .leading, spacing: 6))
            : AnyLayout(HStackLayout(spacing: 10))
        return layout {
            if let onTap {
                Button(action: onTap) { face }
                    .buttonStyle(.plain)
                    .accessibilityLabel(item.name)
                    .accessibilityValue(meta)
                    .accessibilityHint(Self.hint(item))
            } else {
                face.accessibilityElement(children: .combine)
            }
            // **Only in the horizontal branch.** A `Spacer` inside a
            // `VStackLayout` is vertical padding, so it would push the upload
            // box to the foot of a growing row rather than keep it beside the
            // name.
            if !typeSize.isAccessibilitySize { Spacer(minLength: 4) }
            uploadControl
        }
        .padding(.vertical, 4)
    }

    private var face: some View {
        // The glyph goes ABOVE the name at an accessibility size. Beside it,
        // the scaled 40-point column is about 113 points at AX5 — a sixth of
        // the row, taken off a name that wraps.
        let faceLayout = typeSize.isAccessibilitySize
            ? AnyLayout(VStackLayout(alignment: .leading, spacing: 4))
            : AnyLayout(HStackLayout(spacing: 10))
        return faceLayout {
            Image(systemName: Self.glyph(item))
                .font(.body)
                // No fixed column in the vertical branch: the glyph is on its
                // own line there, so a width would only indent it.
                .frame(width: glyphWidth)
                .foregroundStyle(Color.fgMuted)
                .accessibilityHidden(true)
            VStack(alignment: .leading, spacing: 2) {
                Text(item.name)
                    .font(.subheadline)
                    .fixedSize(horizontal: false, vertical: true)
                    .multilineTextAlignment(.leading)
                metaLine
            }
            // Flexible only in the vertical branch, so the `large` row is
            // laid out exactly as it was: there the name sizes to its text
            // and the `Spacer` holds the upload box to the trailing edge.
            .frame(maxWidth: nameMaxWidth, alignment: .leading)
        }
        .contentShape(.rect)
    }

    /// The glyph's column, or nil at an accessibility size where it has a
    /// line of its own. A `CGFloat?` by name rather than a ternary in the
    /// argument, so the optional is not something the type checker has to
    /// guess at.
    private var glyphWidth: CGFloat? {
        typeSize.isAccessibilitySize ? nil : art
    }

    /// The name column's ceiling: the whole row at an accessibility size,
    /// and nothing (size to the text) below one. `CGFloat.infinity` spelt
    /// out for the reason above.
    private var nameMaxWidth: CGFloat? {
        typeSize.isAccessibilitySize ? CGFloat.infinity : nil
    }

    /// `"12 MB · 3d ago"` and the duration, side by side or stacked.
    ///
    /// Stacked at an accessibility size: at AX5 the two of them shared 36
    /// points of width in I002 and `3:34` came back over three lines, which
    /// is the shape Apple reported as clipped.
    private var metaLine: some View {
        let metaLayout = typeSize.isAccessibilitySize
            ? AnyLayout(VStackLayout(alignment: .leading, spacing: 2))
            : AnyLayout(HStackLayout(spacing: 6))
        return metaLayout {
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

    /// The upload button, for any file at all.
    ///
    /// **No size branch any more.** ``PhoneUploadModel/offersUpload(_:)`` is
    /// `!item.isDir` since the transfers unit's streaming upload took the
    /// 64 MiB ceiling out, so the `else` that drew `PhoneUploadTooBigNote` (deleted now)
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
