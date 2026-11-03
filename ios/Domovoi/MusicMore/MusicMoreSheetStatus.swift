import DomovoiKit
import SwiftUI

/// What a sheet's own write just did, said **inside the sheet**.
///
/// ### Why a sheet does not use the shell's toast
///
/// The shell's toast is an `.overlay(alignment: .bottom)` on the shell's
/// content (`AppShellView.swift:35`) — a sibling of the view a sheet is
/// presented *from*, not of the sheet. A page sheet covers it. So every
/// outcome these two sheets produce — `added to Quiet Hours`,
/// `favorited "…"`, `"…" is already in …`, `created … · added "…"`,
/// `playlist updated`, `reorder failed`, `remove failed`, `playing … in
/// cellar…` — was drawn behind the sheet that was still up, and nobody ever
/// read one. The only one that worked was `deleted "…"`, because the delete
/// dismisses the sheet first; that one is still handed to the shell
/// (``MusicView``).
///
/// It also made four UI cases look like they passed:
/// `app.descendants(matching: .any)` reaches an element behind a sheet, so
/// they were asserting a string no person could see — and they were in direct
/// tension with the audit case, whose finding for "a sheet that is not modal"
/// is exactly an element reachable behind one. One set passing meant the
/// other failing.
///
/// ### What is not
///
/// **It does not fade out.** A shell toast is on a timer; this is a line of
/// text in a form, and the next write replaces it. A line that vanished on
/// its own would be one VoiceOver could be reading when it went.
struct MusicMoreSheetStatus: View {

    /// The sentence, or nil for nothing at all.
    let said: String?

    /// Whether it is a failure, which is the model's own answer
    /// (`toastFailed`) rather than a guess at the wording.
    let failed: Bool

    /// What a case looks this line up by. One identifier for both sheets:
    /// only one of them can be up (``MusicView``'s single `.sheet`).
    static let identifier = "musicmore-sheet-status"

    var body: some View {
        if let said, !said.isEmpty {
            Text(said)
                .font(.footnote)
                // Ink tokens both ways. `fgMuted` is measured against the
                // white canvas, which a sheet's background is.
                .foregroundStyle(failed ? Color.errInk : Color.fgMuted)
                .fixedSize(horizontal: false, vertical: true)
                .frame(maxWidth: .infinity, alignment: .leading)
                .accessibilityIdentifier(Self.identifier)
        }
    }
}
