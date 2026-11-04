import DomovoiKit
import SwiftUI

/// The Upload control on one row of "your device", in its four states.
///
/// `ui/screens/local/DeviceUpload.kt:323`'s `UploadRowButton`.
///
/// ### The percentage, which is real now and is on the screen (D43)
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
/// **And the number is drawn.** Two things were wrong when this file was
/// first written (REVIEW-1 finding 3): the "determinate" ring was
/// `ProgressView(value:).progressViewStyle(.circular)`, which on iOS ignores
/// the fraction and draws the indeterminate spinner; and the percentage
/// existed only in the button's accessibility label, so a sighted person saw
/// a spinner and no number — while Android puts whole percent in a
/// notification this app has none of. The ring is
/// ``TransferProgressRing`` now, and ``TransferStrings/percent(_:)`` sits
/// beside it.
///
/// VoiceOver therefore hears the number twice: once in the button's label
/// (Android's own sentence, kept word for word) and once from the text. That
/// is the trade taken on purpose — a number only VoiceOver can reach is
/// worse than a number read twice — and the text is a **separate** element
/// rather than a hidden one so that a UI case can tell the two apart
/// (`testTransfers_ABigPhoneFileGoesThroughTheStreamedDoor` asserts both).
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

    @Environment(\.dynamicTypeSize) private var dynamicTypeSize

    /// The 48-point box Android draws (`DeviceUpload.kt:324`), scaled: an
    /// icon-only control sized in fixed points is the thing that stops
    /// growing when everything around it does.
    @ScaledMetric(relativeTo: .body) private var box: CGFloat = 48

    /// The identifier the UI case finds the drawn number by. A query for the
    /// percentage would otherwise match the button's own label and pass
    /// whether or not anything was on the screen.
    static let percentID = "transfers-upload-percent"

    var body: some View {
        // The number next to the ring while one is known, and nothing but the
        // control otherwise. A row at ordinary sizes and a column at
        // accessibility ones (the standing rule): "100%" at AX5 is wider than
        // the control it belongs to.
        if case .busy(let percent) = state, let percent {
            if dynamicTypeSize.isAccessibilitySize {
                VStack(alignment: .trailing, spacing: 2) {
                    percentText(percent)
                    control
                }
            } else {
                HStack(spacing: 4) {
                    percentText(percent)
                    control
                }
            }
        } else {
            control
        }
    }

    private func percentText(_ percent: Int) -> some View {
        Text(TransferStrings.percent(percent))
            .font(.caption)
            // So "9%" and "100%" do not shuffle the ring sideways on every
            // chunk.
            .monospacedDigit()
            .foregroundStyle(Color.fgMuted)
            // The row's own canvas is the list's, which is what `fgMuted` was
            // measured against; the refused card below uses `primary` because
            // its fill is not.
            .fixedSize()
            .accessibilityIdentifier(Self.percentID)
    }

    private var control: some View {
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
            //
            // The determinate one is ``TransferProgressRing``: see the note
            // on the type for why `ProgressView(value:)` was not one.
            if let fraction = transferRingFraction(percent.map { Double($0) / 100 }) {
                TransferProgressRing(share: fraction, diameter: box * 0.55)
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

// `PhoneUploadTooBigNote` stood here and is **gone**. It was the line a row
// over the old 64 MiB ceiling drew instead of an upload control, and D43 took
// the ceiling out: `PhoneUploadModel.offersUpload(_:)` is `!item.isDir` for
// every file, because the upload streams off disk and never measures it. The
// view's own doc said it stayed only until its two call sites went, and they
// are gone with the branches that drew them (`PhoneMediaRowView.swift`,
// `PhoneDeviceVideosView.swift`, the player unit's files) — so it is deleted
// on the integration, in one change with `PhoneMediaError.tooBig` and
// `PhoneMediaStrings.tooBigToUpload`, because either half alone does not
// compile.
