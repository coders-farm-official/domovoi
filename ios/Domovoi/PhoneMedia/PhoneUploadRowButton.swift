import DomovoiKit
import SwiftUI

/// The Upload control on one row of "your device", in its four states.
///
/// `ui/screens/local/DeviceUpload.kt:323`'s `UploadRowButton`.
///
/// ### What is not drawn, and why
///
/// * **A row over ``PhoneUploadModel/cap``** gets no button at all — the
///   caller asks ``PhoneUploadModel/offersUpload(_:)`` first and draws
///   ``PhoneUploadTooBigNote`` instead. A button that would fail on every tap
///   is the control the owner's rule forbids ([[no-misleading-controls]]).
/// * **A percentage.** Android streams the body and reports whole percent as
///   it writes (`PhoneUpload.kt:52`); `DomovoiClient.raw` takes `body: Data`
///   and reports nothing until it answers, so the ring is indeterminate and
///   ``PhoneUploadButtonState/busy(percent:)`` is always built with `nil`
///   here. A guessed number would read as progress and move on nothing.
///   ``PhoneMediaStrings/uploadingLabel(_:)`` still takes the percent,
///   because the rule is shared with Android's and
///   `/api/files/upload-stream` is the item that will fill it in.
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
        case .busy:
            // The ring, and the only moving thing in the row. Hidden from
            // VoiceOver because the **button** carries the words Android
            // gives the ring (`DeviceUpload.kt:326`): a label on a view
            // inside a hidden-by-combination button is read by nothing, and
            // this file shipped one for an hour.
            ProgressView()
                .progressViewStyle(.circular)
                .accessibilityHidden(true)
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
/// ``FileSaveModel``'s own pattern the other way round (`FileSave.swift:28`):
/// the ceiling is stated on the row, in bytes, and the dashboard is named as
/// the place where there is no ceiling.
struct PhoneUploadTooBigNote: View {

    let size: Int64?

    var body: some View {
        Text(PhoneMediaStrings.tooBigToUpload(size ?? 0))
            .font(.caption)
            .foregroundStyle(Color.fgMuted)
            .fixedSize(horizontal: false, vertical: true)
    }
}
