import DomovoiKit
import SwiftUI

/// The one "save to device" control, for every screen that has one.
///
/// Files, Videos and — once the player unit's rows land — Audiobooks and
/// Podcasts all draw this, over one ``SaveToDeviceModel``. Android has no
/// counterpart: there the row hands a URL to `DownloadManager` and the system
/// draws the progress in its own notification (`VideoFeed.kt:459` →
/// `VideosScreen.kt:216`). Here the app carries the file, so the app draws
/// the three states a transfer has.
///
/// ### Three states, and the fourth that is somebody else's
///
/// * `idle` — the tap. One row of a list or a sheet, Android's own words.
/// * `fetching` — the percentage and a **stop**. A multi-gigabyte save with
///   no way out is a phone stuck on a mistake, and a determinate bar is why
///   the progress callback exists at all.
/// * `failed` — nothing here. The screen says it: Files and Videos both put
///   a failed save in the **shell's** toast, because a sentence about a save
///   must outlive a scroll and does outlive the screen.
/// * `ready` — nothing here either. The screen presents the share sheet,
///   because a sheet belongs to a screen and two of them cannot both be up.
///
/// ### Dynamic Type
///
/// The row becomes a column at accessibility sizes (the standing rule), the
/// progress label wraps, and the stop button is a word rather than a glyph —
/// an X beside a filename is the shape of a delete, which is the one thing
/// this control must not look like.
struct SaveToDeviceButton: View {

    let state: SaveToDeviceModel.State
    let onTap: () -> Void
    let onCancel: () -> Void

    @Environment(\.dynamicTypeSize) private var dynamicTypeSize

    /// The glyph's box, scaled rather than fixed: a `frame(width: 20)` is the
    /// Dynamic Type defect this project has fixed four times.
    @ScaledMetric(relativeTo: .body) private var glyphWidth: CGFloat = 22

    var body: some View {
        switch state {
        case .fetching(let name, let share):
            fetching(name, share)
        case .idle, .ready, .failed:
            // `ready` and `failed` draw the tap again: the screen has taken
            // the file (or said what went wrong) and this control is ready
            // for the next one. A disabled button in those states would be a
            // control that cannot do what it says.
            tap
        }
    }

    private var tap: some View {
        Button(action: onTap) {
            Label(TransferStrings.saveToDevice, systemImage: "square.and.arrow.down")
                .frame(minHeight: 44)
        }
    }

    @ViewBuilder
    private func fetching(_ name: String, _ share: Double?) -> some View {
        let said = share.map { TransferStrings.savingPercent(name, Int(($0 * 100).rounded(.down))) }
            ?? TransferStrings.saving(name)
        if dynamicTypeSize.isAccessibilitySize {
            VStack(alignment: .leading, spacing: 8) {
                progress(said, share)
                stop
            }
            .frame(maxWidth: .infinity, alignment: .leading)
        } else {
            HStack(spacing: 12) {
                progress(said, share)
                Spacer(minLength: 0)
                stop
            }
        }
    }

    @ViewBuilder
    private func progress(_ said: String, _ share: Double?) -> some View {
        HStack(spacing: 8) {
            // Determinate when the total is known, which is the whole point
            // of the progress callback; indeterminate otherwise, because a
            // ring pinned at 0 reads as a stall.
            //
            // ``TransferProgressRing`` and **not** `ProgressView(value:)`:
            // with `.progressViewStyle(.circular)` that one ignores the
            // fraction on iOS and draws the indeterminate spinner, so this
            // ring and the upload row's were the same spinner the old code
            // drew while both comments claimed otherwise (REVIEW-1 finding
            // 3). The percentage in `said` was right either way, which is
            // why nothing here failed.
            if let share {
                TransferProgressRing(share: share, diameter: glyphWidth)
            } else {
                ProgressView()
                    .frame(width: glyphWidth, height: glyphWidth)
            }
            Text(said)
                .font(.footnote)
                .foregroundStyle(Color.fgMuted)
                .fixedSize(horizontal: false, vertical: true)
                .multilineTextAlignment(.leading)
        }
        // One label for the pair, so VoiceOver says the percentage once
        // rather than reading a ring and then a sentence.
        .accessibilityElement(children: .combine)
        .accessibilityLabel(said)
    }

    private var stop: some View {
        Button(role: .cancel, action: onCancel) {
            Text(TransferStrings.cancel)
                .font(.footnote)
                .frame(minHeight: 44)
        }
        .buttonStyle(.plain)
        .foregroundStyle(Color.brandInk)
    }
}
