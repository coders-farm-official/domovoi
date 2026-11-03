import DomovoiKit
import SwiftUI

// ---------------------------------------------------------------------------
// The three cards the Images screen draws, and the tile they share.
//
// Every string comes from ``ImagesStrings`` or off the job itself — nothing
// in this file holds a literal a person reads, because a literal in a view is
// outside what the package's string tests can see.
//
// The card surface is ``HomeCard``'s (`HomeCardView.swift:76-79`), so Images
// does not introduce a second card look. It is also why the muted text in
// here is worth watching: ``Color/fgMuted`` was measured against the **white**
// canvas, and a card tints what is behind it. That is the finding the audit
// case on this screen is looking for.
// ---------------------------------------------------------------------------

/// The engine's note — `EngineNote`, `ImagesScreen.kt:288-296`: a card with a
/// small title and a muted line under it.
///
/// Three of the five engine states are this, and all three say to go to the
/// web dashboard's Images page. **There is no control**, here or anywhere on
/// this screen, for installing or starting the engine: Android has none
/// either, no route for it exists in Android's client, and a button that
/// cannot do what it says is not drawn.
struct ImagesEngineNote: View {

    let title: String
    let sub: String

    var body: some View {
        // ``HomeCard`` rather than a fourth card shape: this is exactly a
        // heading with content under it, which is what that type is.
        HomeCard(title: title) {
            Text(sub)
                .font(.footnote)
                .foregroundStyle(Color.fgMuted)
                .fixedSize(horizontal: false, vertical: true)
                .frame(maxWidth: .infinity, alignment: .leading)
        }
    }
}

/// One job in the live lane — `JobCard`, `ImagesScreen.kt:327-379`.
///
/// The lane holds `pending`, `running` **and `failed`**
/// (``ImagesJobStatus/inLane``, `:180-183`): a failure stays here rather than
/// moving to history, which is how a dead generation gets noticed at all.
///
/// **This row holds no `Task`.** `onCancel` calls into ``ImagesModel``, which
/// outlives the row — a `LazyVStack` row is torn down as it scrolls out and
/// would cancel the write mid-flight. Android does the same thing for the
/// same control: its cancel runs on `app.scope` (`:349`).
struct ImagesJobCard: View {

    let job: ImagesJob
    let onCancel: () -> Void

    @Environment(\.dynamicTypeSize) private var dynamicTypeSize

    var body: some View {
        VStack(alignment: .leading, spacing: 8) {
            top
            if job.status.isActive {
                // `:357-369`. No spinner beside it: the pill above already
                // says `running`, and a spinner per row is noise in a lane
                // that can hold several.
                Text(job.progressLine)
                    .font(.caption.monospaced())
                    .foregroundStyle(Color.fgMuted)
                    .fixedSize(horizontal: false, vertical: true)
            }
            if let trouble = job.error, !trouble.isEmpty {
                // `:370-377` — the engine's own words. A `failed` job whose
                // message is decoded and not drawn looks exactly like an idle
                // one, which is the failure mode this line exists to prevent.
                //
                // ``Color/errInk`` and never `.red`: 6.08:1 against 3.55:1,
                // and this is text.
                Text(trouble)
                    .font(.caption.monospaced())
                    .foregroundStyle(Color.errInk)
                    .fixedSize(horizontal: false, vertical: true)
            }
        }
        .padding(16)
        .frame(maxWidth: .infinity, alignment: .leading)
        .background(
            .quaternary.opacity(0.4),
            in: RoundedRectangle(cornerRadius: 16, style: .continuous)
        )
    }

    /// The status pill, the one line of text, and the cancel — `:331-355`.
    @ViewBuilder private var top: some View {
        if dynamicTypeSize.isAccessibilitySize {
            // A pill, a prompt and an icon on one line is three things
            // competing for a phone's width at AX5. Rule §3: swap the row for
            // a column.
            VStack(alignment: .leading, spacing: 8) {
                HStack(spacing: 8) {
                    pill
                    Spacer(minLength: 8)
                    cancel
                }
                title
            }
        } else {
            HStack(alignment: .center, spacing: 10) {
                pill
                title
                Spacer(minLength: 8)
                cancel
            }
        }
    }

    /// `Pill(job.status, tone)` — `:333-340`. **The raw word**, which is why
    /// ``ImagesJobStatus/other(_:)`` keeps it: a status this app has never
    /// heard of still renders its own word rather than "other".
    private var pill: some View {
        HomeTonePill(label: job.statusWord, tone: tone)
    }

    /// `:335-338`: `Err` for a failure, `Brand` while it is still happening,
    /// idle for anything else.
    private var tone: HealthTone {
        if job.status.isFailure { return .err }
        return job.status.isActive ? .brand : .idle
    }

    /// `prompt ?: model`, one line, ellipsised (`:341-347`).
    ///
    /// One line here as there, and **no** `fixedSize`: a lane row is a
    /// headline for something in progress, the whole prompt is on the history
    /// card once it finishes, and three lines per row in a lane of five
    /// pushes the history off the screen.
    private var title: some View {
        Text(job.laneTitle)
            .font(.subheadline)
            .lineLimit(1)
            .truncationMode(.tail)
    }

    /// `:348-355`, drawn **only while the job is still happening** — a cancel
    /// offered for a failed job would be a control that cannot do what it
    /// says, and ``ImagesModel/cancel(_:)`` refuses it as well so that the
    /// check is not merely a courtesy of the view.
    @ViewBuilder private var cancel: some View {
        if job.status.isActive {
            Button(action: onCancel) {
                Image(systemName: "xmark.circle")
                    .frame(minWidth: 44, minHeight: 44)
                    .contentShape(.rect)
            }
            .buttonStyle(.plain)
            .foregroundStyle(Color.fgMuted)
            // Android's own `contentDescription` (`:353`). An icon-only
            // control with no label is a button VoiceOver cannot name.
            .accessibilityLabel(ImagesStrings.cancel)
        }
    }
}

/// One past generation — `HistoryCard`, `ImagesScreen.kt:381-436`.
///
/// At most three pictures, the prompt, the settings it was made with, and a
/// delete that asks first.
struct ImagesHistoryCard: View {

    let job: ImagesJob

    /// ``ImagesModel/tiles`` — ``VideoPosterStore`` reused as it is
    /// (DECISIONS). The store outlives every row, which is the point: a lazy
    /// row must not own the fetch.
    let tiles: VideoPosterStore

    let onOpen: (String) -> Void
    let onDelete: () -> Void

    @Environment(\.dynamicTypeSize) private var dynamicTypeSize

    var body: some View {
        content
            .padding(16)
            .frame(maxWidth: .infinity, alignment: .leading)
            .background(
                .quaternary.opacity(0.4),
                in: RoundedRectangle(cornerRadius: 16, style: .continuous)
            )
    }

    /// Android is one `Row`: the tiles, a text column of weight 1.4, the
    /// delete (`:396-433`).
    ///
    /// **At an accessibility size it becomes a column**, and this is the
    /// layout the AX5 case is measuring. Three 84-point squares beside three
    /// lines of a prompt is the arrangement that straddles the viewport edge
    /// once the squares grow with the type size and the text needs six lines
    /// instead of three — the exact shape `[[audit-reports-one-issue]]`
    /// records a finding for, which two tree diffs missed and the screenshot
    /// explained.
    @ViewBuilder private var content: some View {
        if dynamicTypeSize.isAccessibilitySize {
            VStack(alignment: .leading, spacing: 10) {
                thumbnails
                text
                HStack {
                    Spacer(minLength: 8)
                    deleteButton
                }
            }
        } else {
            HStack(alignment: .top, spacing: 12) {
                thumbnails
                text
                Spacer(minLength: 8)
                deleteButton
            }
        }
    }

    /// `names.take(3)` (`:398`) — ``ImagesJob/tileNames``.
    ///
    /// **A fourth picture is not drawn and not counted anywhere on the card.**
    /// Android shows three and says nothing, and a `+1 more` affordance would
    /// open nothing. The delete confirmation still counts every image,
    /// because that is what is being deleted (``ImagesStrings/deleteBody(images:)``).
    /// DECISIONS, "A fourth generated image is not shown, and not counted".
    private var thumbnails: some View {
        HStack(spacing: 8) {
            ForEach(job.tileNames, id: \.self) { name in
                ImagesTile(name: name, tiles: tiles) { onOpen(name) }
            }
        }
    }

    /// The prompt at three lines, then the monospace meta line (`:417-428`).
    private var text: some View {
        VStack(alignment: .leading, spacing: 6) {
            Text(job.prompt ?? "")
                .font(.subheadline)
                // Three lines as there, and it **wraps** to reach them:
                // `minimumScaleFactor` is rejected throughout this app.
                .lineLimit(3)
                .truncationMode(.tail)
                .fixedSize(horizontal: false, vertical: true)
                .frame(maxWidth: .infinity, alignment: .leading)
            // ``ImagesJob/metaLine``: the model, the size **only when both
            // width and height are there** (`:387` — half a size is not a
            // size), the steps, the seed.
            Text(job.metaLine)
                .font(.caption.monospaced())
                .foregroundStyle(Color.fgMuted)
                .fixedSize(horizontal: false, vertical: true)
                .frame(maxWidth: .infinity, alignment: .leading)
        }
    }

    /// `:430-432`, with Android's own `contentDescription`.
    private var deleteButton: some View {
        Button(action: onDelete) {
            Image(systemName: "trash")
                .frame(minWidth: 44, minHeight: 44)
                .contentShape(.rect)
        }
        .buttonStyle(.plain)
        // The ink token, not `.red`: this is a glyph that carries meaning and
        // `.red` is 3.55:1 on the light canvas.
        .foregroundStyle(Color.errInk)
        .accessibilityLabel(ImagesStrings.delete)
    }
}

/// One generated picture, as a square that opens it.
///
/// 84 dp in Android (`ImagesScreen.kt:400`), **`@ScaledMetric` here**: a glyph
/// or an image box given a fixed `frame(width:)` does not grow with the type
/// size, which is rule §3 and a finding this track has already had.
///
/// The bytes come through ``VideoPosterStore`` and never `AsyncImage`:
/// `…/file/{name}` is a device-tier route and `AsyncImage` makes its own
/// unauthenticated request, which 401s every time. The store's own doc has
/// the whole reason.
struct ImagesTile: View {

    let name: String
    let tiles: VideoPosterStore
    let onOpen: () -> Void

    @ScaledMetric(relativeTo: .body) private var side: CGFloat = 84

    private var path: String { ImagesPaths.file(name) }

    var body: some View {
        Button(action: onOpen) {
            Rectangle()
                // Opaque, for ``VideoPosterTile``'s reason: a contrast check
                // cannot read a colour that is whatever happens to be behind
                // it.
                .fill(Color(.secondarySystemBackground))
                .overlay { picture }
                .frame(width: side, height: side)
                .clipShape(.rect(cornerRadius: 8))
                .overlay {
                    RoundedRectangle(cornerRadius: 8)
                        .strokeBorder(Color(.separator), lineWidth: 1)
                }
                .contentShape(.rect)
        }
        .buttonStyle(.plain)
        // Android's `contentDescription = name` (`:407`). The file's own name
        // and not "image": a card can hold three of these and three rows
        // saying "image" name nothing.
        .accessibilityLabel(name)
        // Asked for by the tile, fetched by the store, which is what makes
        // this row own nothing.
        .task(id: path) { tiles.want(path) }
    }

    @ViewBuilder private var picture: some View {
        switch tiles.poster(path) {
        case .image(let bytes):
            if let decoded = UIImage(data: bytes) {
                Image(uiImage: decoded)
                    .resizable()
                    .aspectRatio(contentMode: .fill)
            } else {
                // The server said 200 and the 64-byte floor passed, so this
                // is a format the phone cannot decode rather than an absence.
                // Worth its own branch: `Image(uiImage:)` of nothing draws
                // nothing at all, which is indistinguishable from a tile that
                // never loaded.
                ImagesTileGlyph()
            }
        case .loading, .absent, .failed, nil:
            // One glyph for all four, as ``VideoPosterTile`` does: a row of
            // three spinners on a slow house is noise, and the glyph is where
            // most of these settle anyway.
            ImagesTileGlyph()
        }
    }
}

/// What a tile shows while it has no picture.
struct ImagesTileGlyph: View {

    var body: some View {
        Image(systemName: "photo")
            .font(.system(size: 22))
            // ``Color/fgMuted`` at 7.44:1, not `.secondary` at 60% opacity,
            // which is about 2:1 — the finding Apple's audit opened this app
            // with. A glyph that carries meaning needs 3:1.
            .foregroundStyle(Color.fgMuted)
            .accessibilityHidden(true)
    }
}
