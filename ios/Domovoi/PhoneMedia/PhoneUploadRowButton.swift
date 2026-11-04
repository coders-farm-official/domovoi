import DomovoiKit
import SwiftUI

/// The Upload control on one row of "your device", in its four states.
///
/// `ui/screens/local/DeviceUpload.kt:323`'s `UploadRowButton`.
///
/// ### The percentage, which is real now (D43)
///
/// Android streams the body and reports whole percent as it writes
/// (`PhoneUpload.kt:52`). This app could not: `DomovoiClient.raw` takes
/// `body: Data` and says nothing until it answers, so the ring was
/// indeterminate and ``PhoneUploadButtonState/busy(percent:)`` was always
/// built with `nil`. The streamed upload reports bytes as they go
/// (``DomovoiClient/uploadFile(method:path:fileURL:contentType:trackPairing:onProgress:)``),
/// so the ring is **determinate** when the number is known and indeterminate
/// only when it is not — and ``PhoneMediaStrings/uploadingLabel(_:)``, which
/// always took a percent for Android's sake, now gets one.
///
/// ### What is not drawn, and why
///
/// * **A folder.** The caller asks ``PhoneUploadModel/offersUpload(_:)``
///   first, which is false only for a directory: this app cannot send a tree
///   and the server's streamed door takes one file per request
///   (`files.py:1189`).
/// * **A cancel.** A save can be stopped (``SaveToDeviceModel/cancel()``) and
///   an upload cannot, which is the one asymmetry in this unit. The row's
///   control is a 48-point box with a ring in it — there is nowhere on it for
///   a second control — and a tap on the ring already means "say what is
///   happening" (`DeviceUpload.kt:338`). It is in DECISIONS.md as a thing to
///   put somewhere, not as a thing that is handled.
struct PhoneUploadRowButton: View {

    let state: PhoneUploadButtonState
    let onTap: () -> Void

    /// The 48-point box Android draws (`DeviceUpload.kt:324`), scaled: an
    /// icon-only control sized in fixed points is the thing that stops
    /// growing when everything around it does.
    @ScaledMetric(relativeTo: .body) private var box: CGFloat = 48

    var body: some View {
        Button(action: onTap) {
            face
                .frame(width: box, height: box)
                .contentShape(.rect)
        }
        .buttonStyle(.plain)
        // **Disabled only while checking**, which is Android's rule
        // (`DeviceUpload.kt:338`): a refused button is still tappable,
        // because the tap is how the reason gets said out loud and how the
        // status gets re-read (``PhoneUploadModel/gate()``). Hiding it
        // instead would leave a person with a refusal and nowhere to read it.
        .disabled(isChecking)
        .accessibilityLabel(label)
        .accessibilityValue(value)
    }

    private var isChecking: Bool {
        if case .checking = state { return true }
        return false
    }

    @ViewBuilder private var face: some View {
        switch state {
        case .busy(let percent):
            // The ring, and the only moving thing in the row. Hidden from
            // VoiceOver because the **button** carries the words Android
            // gives the ring (`DeviceUpload.kt:326`): a label on a view
            // inside a hidden-by-combination button is read by nothing, and
            // this file shipped one for an hour.
            //
            // Determinate when the upload reported a number and
            // indeterminate when it did not — a ring pinned at 0 reads as a
            // stall, which is what `ProgressView(value: 0)` would draw for
            // the first chunk of a gigabyte.
            if let percent {
                ProgressView(value: Double(percent) / 100)
                    .progressViewStyle(.circular)
                    .accessibilityHidden(true)
            } else {
                ProgressView()
                    .progressViewStyle(.circular)
                    .accessibilityHidden(true)
            }
        case .ready:
            Image(systemName: "arrow.up.circle")
                .font(.title3)
                .foregroundStyle(Color.brandInk)
        case .checking, .refused:
            // Faint unless ready (`DeviceUpload.kt:344`), and `fgMuted`
            // rather than a dimmed primary: a glyph that carries meaning
            // needs 3:1, and this token is the one that has it.
            Image(systemName: "arrow.up.circle")
                .font(.title3)
                .foregroundStyle(Color.fgMuted)
        }
    }

    private var label: String {
        if case .busy(let percent) = state { return PhoneMediaStrings.uploadingLabel(percent) }
        return PhoneMediaStrings.upload
    }

    /// The reason, read out after the word `upload`, so a refused button says
    /// why without a tap. Android has no equivalent — a `contentDescription`
    /// there is one string — and this is the same sentence the tap toasts.
    private var value: String {
        switch state {
        case .refused(let reason): return reason
        case .checking: return PhoneMediaStrings.stillAskingTheServer
        case .ready, .busy: return ""
        }
    }
}

/// The one card above the list when uploads are off.
///
/// `DeviceUpload.kt:352`'s `UploadRefusedCard`: the reason, then the tail that
/// fits — the admin sentence when **this device** was blocked, the
/// "everything else here works" one when a library is merely read-only.
struct PhoneUploadRefusedCard: View {

    let reason: String
    let blocked: Bool

    var body: some View {
        Text(PhoneMediaStrings.refusedCard(reason: reason, blocked: blocked))
            .font(.footnote)
            // **`Color.primary`, not ``Color/fgMuted``.** `fgMuted` was
            // measured against the WHITE canvas; on
            // `secondarySystemBackground` — which is what this card's fill
            // is — it fails Apple's own contrast audit. The card is already
            // set apart by its fill, so the words do not need to be grey as
            // well.
            .foregroundStyle(Color.primary)
            .fixedSize(horizontal: false, vertical: true)
            .padding(12)
            .frame(maxWidth: .infinity, alignment: .leading)
            .background(
                RoundedRectangle(cornerRadius: 10)
                    .fill(Color(.secondarySystemBackground))
            )
            .accessibilityElement(children: .combine)
    }
}

/// What a row too big to upload says instead of having a control.
///
/// **Unreachable since D43**, and left defined on purpose. There is no
/// ceiling in this app any more — ``PhoneUploadModel/offersUpload(_:)`` is
/// true for every file — so nothing draws this. Its two call sites
/// (`PhoneMediaRowView.swift:98`, `PhoneDeviceVideosView.swift:245`) belong
/// to the player unit's files, which this unit may not edit, and a branch
/// calling a view that no longer exists is a branch that does not compile.
/// So the view stays until that unit takes the call sites out; it is noted in
/// `design-notes/ios/batch-2026-11-03/transfers/DECISIONS.md` under "Needs a
/// shared change".
struct PhoneUploadTooBigNote: View {

    let size: Int64?

    var body: some View {
        Text(PhoneMediaStrings.tooBigToUpload(size ?? 0))
            .font(.caption)
            .foregroundStyle(Color.fgMuted)
            .fixedSize(horizontal: false, vertical: true)
    }
}
