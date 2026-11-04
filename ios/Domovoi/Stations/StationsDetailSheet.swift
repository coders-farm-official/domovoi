import DomovoiKit
import SwiftUI

/// One favourite in full — `StationDetail.kt:60`.
///
/// What it is, what the sampler last heard, the five fields, and the
/// detections the fingerprinter has matched. Plus the two writes that belong
/// to a station rather than to a list: its sampling interval, and `forget`.
///
/// ### A sheet, where Android draws a card under the row
///
/// MAP §5. Android's compact branch expands the detail **inline** beneath the
/// Favorites card (`StationsScreen.kt:231`); on a phone that is a 600-point
/// block that pushes the rest of the page off screen, and the chevron that
/// opens it already reads as "open this". The sheet holds exactly what the
/// card holds. The two-tap split is Android's and is kept: the row plays, the
/// chevron opens this.
///
/// ### The row this draws is the list's row
///
/// ``StationsModel/selectedStation`` reads out of the **loaded favourites**,
/// so a PATCH, a resolved simulcast or a push changes what this sheet shows
/// without anything here being told — and the sheet closes by itself when
/// the station stops existing, which is what `forget` does from inside it.
struct StationsDetailSheet: View {

    let stations: StationsModel
    let detail: StationsDetailModel
    let row: StationsRow

    @Environment(\.dismiss) private var dismiss

    @State private var confirmingForget = false

    var body: some View {
        NavigationStack {
            ScrollView {
                VStack(alignment: .leading, spacing: 14) {
                    identity
                    if let track = row.nowPlaying { callout(track) }
                    playHere
                    Divider()
                    fields
                    Divider()
                    detections
                    Divider()
                    forget
                }
                .padding(16)
                .frame(maxWidth: .infinity, alignment: .leading)
            }
            // The screen's own name in the bar and the **station's** in the
            // body: a station's name is a person's word, long enough to
            // truncate in a navigation bar, and the body is where it can
            // wrap. `hide details` is the chevron's word for closing this
            // (`StationsScreen.kt:478`), so the button that closes it says
            // the same thing rather than inventing a second verb.
            .navigationTitle(StationsStrings.title)
            .navigationBarTitleDisplayMode(.inline)
            .toolbar {
                ToolbarItem(placement: .topBarTrailing) {
                    Button(StationsStrings.hideDetails) { dismiss() }
                }
            }
            // An `alert` rather than a `confirmationDialog`, for
            // ``MusicQueueView``'s two reasons: it has a body to read as well
            // as a choice to make, and the destructive option must be the one
            // that is harder to hit by accident.
            .alert(StationsStrings.forgetTitle(row.name), isPresented: $confirmingForget) {
                Button(StationsStrings.forget, role: .destructive) {
                    // The request is the model's and outlives this sheet; the
                    // model closes the detail itself when the row it was
                    // about is gone, which is what dismisses this.
                    stations.forget(row)
                }
                Button(StationsStrings.cancel, role: .cancel) {}
            } message: {
                Text(StationsStrings.forgetBody)
            }
        }
    }

    // MARK: - what it is (`:70`)

    private var identity: some View {
        HStack(alignment: .top, spacing: 10) {
            Image(systemName: row.source == "fm"
                  ? "antenna.radiowaves.left.and.right"
                  : "radio")
                .font(.title3)
                .foregroundStyle(Color.fgMuted)
                .accessibilityHidden(true)
            VStack(alignment: .leading, spacing: 2) {
                Text(row.name)
                    .font(.title3)
                    .fixedSize(horizontal: false, vertical: true)
                    .multilineTextAlignment(.leading)
                Text(StationsStrings.identity(row))
                    .font(.caption)
                    .foregroundStyle(Color.fgMuted)
                    .fixedSize(horizontal: false, vertical: true)
                Text(StationsStrings.lastSampled(stations.relative(row.lastSampledAt)))
                    .font(.caption)
                    .foregroundStyle(Color.fgMuted)
                    .fixedSize(horizontal: false, vertical: true)
            }
            Spacer(minLength: 0)
            ToneDot(tone: StationsRules.samplerTone(row))
        }
    }

    /// The now-playing callout (`:104`) — drawn only when the ICY poller has
    /// a track, which is why it is not the three-state line the row draws.
    private func callout(_ track: String) -> some View {
        VStack(alignment: .leading, spacing: 2) {
            Text(track)
                .font(.subheadline)
                .foregroundStyle(Color.brandInk)
                .fixedSize(horizontal: false, vertical: true)
                .multilineTextAlignment(.leading)
            Text(stations.relative(row.nowPlayingUpdatedAt))
                .font(.caption)
                .foregroundStyle(Color.fgMuted)
                .fixedSize(horizontal: false, vertical: true)
        }
        .padding(12)
        .frame(maxWidth: .infinity, alignment: .leading)
        .background(
            // The brand **wash**, not the brand as text: `Color.brand` is
            // 2.05:1 and is a fill. The words on it are `brandInk` and
            // `fgMuted`, both measured against this canvas.
            Color.brand.opacity(0.12),
            in: RoundedRectangle(cornerRadius: 10, style: .continuous)
        )
    }

    /// `play here` — the expanded favourite row's button
    /// (`StationsScreen.kt:604`).
    ///
    /// Drawn **only** when something can actually play this station, which is
    /// the same three-way split ``StationsPlayTap`` makes for a row:
    ///
    /// * No engine in this build (``StationsModel/canPlayAnything``, which is
    ///   what this branch's route slot passes): **nothing is drawn**, and
    ///   ``StationsView`` says so once on the page rather than per station
    ///   ([[no-misleading-controls]]).
    /// * An engine, and a URL nothing can open: the reason, from
    ///   ``StationsModel/whyNotPlayable(_:)``, instead of a dead button.
    /// * Otherwise the control, which is what makes the engine's arrival one
    ///   argument in `RouteScreen`'s `stations` slot and nothing else.
    @ViewBuilder private var playHere: some View {
        if stations.canPlay(row) {
            HomeLink(StationsStrings.playHere) { stations.play(row) }
                .disabled(stations.isBusy(row))
        } else if let why = stations.whyNotPlayable(row) {
            Text(why)
                .font(.caption)
                .foregroundStyle(Color.fgMuted)
                .fixedSize(horizontal: false, vertical: true)
                .multilineTextAlignment(.leading)
        }
    }

    // MARK: - the five fields (`:120`)

    private var fields: some View {
        VStack(alignment: .leading, spacing: 10) {
            StationsStreamEditor(stations: stations, detail: detail, row: row)
            StationsFieldRow(
                label: StationsStrings.fieldCountry,
                value: row.countryCode ?? StationsStrings.dash
            )
            StationsFieldRow(
                label: StationsStrings.fieldLanguage,
                value: row.language ?? StationsStrings.dash
            )
            StationsFieldRow(
                label: StationsStrings.fieldTags,
                value: row.tags.isEmpty ? StationsStrings.dash : row.tags.joined(separator: ", ")
            )
            StationsIntervalEditor(stations: stations, row: row)
        }
    }

    // MARK: - the feed (`:339`)

    private var detections: some View {
        VStack(alignment: .leading, spacing: 8) {
            Text(StationsStrings.recentDetections)
                .font(.headline)
                .accessibilityAddTraits(.isHeader)
                .fixedSize(horizontal: false, vertical: true)
            StationsDetectionFeed(detail: detail)
        }
    }

    // MARK: - forgetting (`:600`)

    private var forget: some View {
        Button(StationsStrings.forget) { confirmingForget = true }
            .font(.subheadline)
            .foregroundStyle(Color.errInk)
            .frame(minHeight: 44)
            .contentShape(.rect)
            .disabled(stations.isBusy(row))
    }
}

/// One `label  value` line of the detail (`StationDetail.kt:134`).
struct StationsFieldRow: View {

    let label: String
    let value: String

    @Environment(\.dynamicTypeSize) private var dynamicTypeSize

    var body: some View {
        // A label and a value on one line become two at an accessibility
        // size, where either of them can be a line of its own (rule §3).
        Group {
            if dynamicTypeSize.isAccessibilitySize {
                VStack(alignment: .leading, spacing: 2) { name; text }
            } else {
                HStack(alignment: .firstTextBaseline, spacing: 10) {
                    name
                    Spacer(minLength: 8)
                    text
                }
            }
        }
        .accessibilityElement(children: .combine)
    }

    private var name: some View {
        Text(label)
            .font(.caption)
            .foregroundStyle(Color.fgMuted)
            .fixedSize(horizontal: false, vertical: true)
    }

    private var text: some View {
        Text(value)
            .font(.subheadline)
            .fixedSize(horizontal: false, vertical: true)
            .multilineTextAlignment(.leading)
    }
}

/// The stream field's three faces — `StreamUrlEditor` (`StationDetail.kt:217`).
///
/// *view*: the URL and a pencil. *edit*: the box, `save` and `cancel`.
/// *empty*: the italic line saying polling cannot reach this station, plus
/// `resolve` for an FM row — the only source a simulcast lookup can be made
/// for — and `paste url` for everything.
///
/// Which face is on is ``StationsDetailModel/streamState(_:)``'s answer and
/// not three booleans here: the three are mutually exclusive and **ordered**,
/// and a view that got the order wrong once would draw a field over a
/// sentence saying there is no field.
struct StationsStreamEditor: View {

    let stations: StationsModel
    let detail: StationsDetailModel
    let row: StationsRow

    var body: some View {
        VStack(alignment: .leading, spacing: 6) {
            Text(StationsStrings.fieldStream)
                .font(.caption)
                .foregroundStyle(Color.fgMuted)
                .fixedSize(horizontal: false, vertical: true)
            switch detail.streamState(row) {
            case .view(let url):
                HStack(alignment: .firstTextBaseline, spacing: 8) {
                    Text(url)
                        .font(.footnote)
                        .fixedSize(horizontal: false, vertical: true)
                        .multilineTextAlignment(.leading)
                        // **Spoken as a named value, not as a bare URL.**
                        // Train S007's audit of this sheet reported "Label
                        // not human-readable" against
                        // `http://stream.example/kestrel04`, and it is a
                        // true finding: the element's whole label was the
                        // URL, so VoiceOver read out a host and a path with
                        // no word for what they were. The caption above
                        // says `stream`, but a caption is a sibling leaf —
                        // it is not part of this element and is not read
                        // with it. Label and value rather than one combined
                        // string, as `FileRowView` does with a file's meta.
                        .accessibilityLabel(StationsStrings.streamURLA11y)
                        .accessibilityValue(url)
                    Spacer(minLength: 4)
                    Button {
                        detail.beginEdit(row)
                    } label: {
                        Image(systemName: "pencil")
                            .font(.footnote)
                            .foregroundStyle(Color.fgMuted)
                            .frame(width: 44, height: 44)
                            .contentShape(.rect)
                    }
                    .buttonStyle(.plain)
                    .accessibilityLabel(StationsStrings.editStreamURL)
                }
            case .edit:
                editor
            case .empty(let canResolve):
                VStack(alignment: .leading, spacing: 6) {
                    Text(StationsStrings.noStreamURL)
                        .font(.footnote)
                        .italic()
                        .foregroundStyle(Color.fgMuted)
                        .fixedSize(horizontal: false, vertical: true)
                    HStack(spacing: 12) {
                        if canResolve {
                            HomeLink(
                                stations.isBusy(row)
                                    ? StationsStrings.resolving
                                    : StationsStrings.resolve
                            ) {
                                stations.resolveSimulcast(row)
                            }
                            .disabled(stations.isBusy(row))
                        }
                        HomeLink(StationsStrings.pasteURL) { detail.beginEdit(row) }
                    }
                }
            }
        }
    }

    /// The box, and the two words under it.
    ///
    /// A **single-line** field, so `submitLabel(.done)` says what Return
    /// does: the multi-line rule — a vertical-axis field's Return makes a
    /// newline, so a labelled key would lie — does not apply, and a URL has
    /// no newlines in it anyway.
    private var editor: some View {
        VStack(alignment: .leading, spacing: 6) {
            TextField(
                StationsStrings.streamPlaceholder,
                text: Binding(get: { detail.draft }, set: { detail.typedDraft($0) })
            )
            .textFieldStyle(.roundedBorder)
            .autocorrectionDisabled()
            .textInputAutocapitalization(.never)
            .keyboardType(.URL)
            .submitLabel(.done)
            .onSubmit { save() }
            .accessibilityLabel(StationsStrings.streamPlaceholder)
            HStack(spacing: 12) {
                HomeLink(StationsStrings.save) { save() }
                    .disabled(stations.isBusy(row))
                HomeLink(StationsStrings.cancel) { detail.cancelEdit(row) }
            }
        }
    }

    /// The field closes **only when something was sent**: a refused draft
    /// that closed the box would throw away the text somebody has to correct
    /// (``StationsModel/saveStreamURL(_:draft:)`` answers whether it was).
    private func save() {
        if stations.saveStreamURL(row, draft: detail.draft) {
            detail.editSaved()
        }
    }
}

/// The sampling interval — `StationsScreen.kt:540`.
///
/// A numeric box with an `s` after it, `save` and `cancel`, and the server's
/// own bounds checked before anything is sent so that a typo is a sentence
/// rather than a 422 (``StationsRules/interval(_:)``).
struct StationsIntervalEditor: View {

    let stations: StationsModel
    let row: StationsRow

    /// The draft is **this view's**, where the stream field's is the detail
    /// model's. Not an inconsistency: the stream editor has three faces and
    /// an order between them that something has to own and a test has to be
    /// able to ask about, where this is a number in a box that is thrown away
    /// if it is not saved. A `@State` that dies with the sheet is the right
    /// home for the second and the wrong one for the first.
    @State private var draft = ""
    @State private var editing = false

    @Environment(\.dynamicTypeSize) private var dynamicTypeSize

    var body: some View {
        VStack(alignment: .leading, spacing: 6) {
            Text(StationsStrings.fieldInterval)
                .font(.caption)
                .foregroundStyle(Color.fgMuted)
                .fixedSize(horizontal: false, vertical: true)
            if editing {
                // **The actions ride on the field's own line.**
                //
                // A `numberPad` has no Return key, so `save` is the only way
                // to commit this box — and on train S004 there was no `save`
                // to tap: the sheet is a `ScrollView`, SwiftUI's keyboard
                // avoidance scrolls the **focused field** clear of the
                // keyboard, and a row of buttons drawn *under* the field is
                // exactly what the keyboard then covers. A control on the
                // field's line is carried up with it.
                //
                // At an accessibility size a 120-point box and two words do
                // not share a line (rule §3), so that branch stacks them and
                // the keyboard toolbar below is what reaches `save` there.
                if dynamicTypeSize.isAccessibilitySize {
                    box
                    actions
                } else {
                    HStack(spacing: 12) {
                        box
                        actions
                    }
                }
            } else {
                HStack(alignment: .firstTextBaseline, spacing: 10) {
                    Text("\(row.sampleIntervalSec.map(String.init) ?? StationsStrings.dash)\(StationsStrings.seconds)")
                        .font(.subheadline)
                        .fixedSize(horizontal: false, vertical: true)
                    Spacer(minLength: 8)
                    HomeLink(StationsStrings.interval) {
                        draft = row.sampleIntervalSec.map(String.init) ?? ""
                        editing = true
                    }
                }
            }
        }
    }

    /// The box, with the unit after it.
    ///
    /// The keyboard toolbar carries the same two actions and no others: a
    /// `numberPad` offers nothing that dismisses itself, so without it an
    /// accessibility size — where the actions stack *under* the box — would
    /// have a keyboard over the only way out of the edit.
    private var box: some View {
        HStack(spacing: 6) {
            TextField(StationsStrings.fieldInterval, text: $draft)
                .textFieldStyle(.roundedBorder)
                .keyboardType(.numberPad)
                .frame(maxWidth: 120)
                .accessibilityLabel(StationsStrings.fieldInterval)
                .toolbar {
                    ToolbarItemGroup(placement: .keyboard) {
                        HomeLink(StationsStrings.save) { save() }
                            .disabled(stations.isBusy(row))
                        HomeLink(StationsStrings.cancel) { editing = false }
                        Spacer()
                    }
                }
            Text(StationsStrings.seconds)
                .font(.subheadline)
                .foregroundStyle(Color.fgMuted)
        }
    }

    private var actions: some View {
        HStack(spacing: 12) {
            HomeLink(StationsStrings.save) { save() }
                .disabled(stations.isBusy(row))
            HomeLink(StationsStrings.cancel) { editing = false }
        }
    }

    /// Closes only when the draft was accepted — the refusal is a toast and
    /// the box keeps the number to fix.
    private func save() {
        if stations.saveInterval(row, draft: draft) {
            editing = false
        }
    }
}

/// The detections the sampler has matched — `DetectionFeed`
/// (`StationDetail.kt:339`).
///
/// One hundred rows and no paging, which is Android's `limit=100` and the
/// route's own default: the cursor the route offers (`before`) is not used
/// there and is not used here.
struct StationsDetectionFeed: View {

    let detail: StationsDetailModel

    var body: some View {
        VStack(alignment: .leading, spacing: 8) {
            if detail.detections.data == nil, detail.detections.answered {
                // A failure is not an empty feed, and saying "no detections
                // yet" about a read that fell over is the lie this screen is
                // most likely to tell (`StationDetail.kt:325` splits them the
                // same way, on `detections == null`).
                Text(StationsStrings.unreachable)
                    .font(.subheadline)
                    .foregroundStyle(Color.fgMuted)
                    .fixedSize(horizontal: false, vertical: true)
                Text(detail.detections.error ?? StationsStrings.requestFailed)
                    .font(.footnote)
                    .foregroundStyle(Color.errInk)
                    .fixedSize(horizontal: false, vertical: true)
                HomeLink(StationsStrings.retry) { detail.detections.refresh() }
            } else if detail.isEmpty {
                VStack(alignment: .leading, spacing: 4) {
                    Text(StationsStrings.noDetections)
                        .font(.subheadline)
                        .fixedSize(horizontal: false, vertical: true)
                    Text(StationsStrings.noDetectionsSub)
                        .font(.footnote)
                        .foregroundStyle(Color.fgMuted)
                        .fixedSize(horizontal: false, vertical: true)
                }
            } else if detail.detections.data == nil {
                ProgressView()
                    .accessibilityLabel(StationsStrings.recentDetections)
            }
            ForEach(detail.rows, id: \.id) { found in
                StationsDetectionRow(detail: detail, detection: found)
            }
        }
    }
}

/// One detection (`StationDetail.kt:360`).
///
/// The title, or `—` when the fingerprinter matched audio it could not name;
/// `artist · 4m ago`; the source pill, whose vocabulary is the **plugin's**
/// and passes through verbatim for anything this app has not heard of
/// (``StationsRules/detectionSource(_:)``); and whether it is in the library.
struct StationsDetectionRow: View {

    let detail: StationsDetailModel
    let detection: StationsDetection

    @Environment(\.dynamicTypeSize) private var dynamicTypeSize

    var body: some View {
        let source = detail.source(detection)
        VStack(alignment: .leading, spacing: 4) {
            VStack(alignment: .leading, spacing: 2) {
                Text(detection.title ?? StationsStrings.dash)
                    .font(.subheadline)
                    .fixedSize(horizontal: false, vertical: true)
                    .multilineTextAlignment(.leading)
                Text(detail.meta(detection))
                    .font(.caption)
                    .foregroundStyle(Color.fgMuted)
                    .fixedSize(horizontal: false, vertical: true)
                    .multilineTextAlignment(.leading)
            }
            .accessibilityElement(children: .combine)
            HStack(spacing: 6) {
                HomeTonePill(label: source.label, tone: source.tone)
                HomeTonePill(
                    label: detail.inLibrary(detection)
                        ? StationsStrings.inLibrary
                        : StationsStrings.newDetection,
                    tone: detail.inLibrary(detection) ? .ok : .idle
                )
            }
        }
        .frame(maxWidth: .infinity, alignment: .leading)
    }
}
