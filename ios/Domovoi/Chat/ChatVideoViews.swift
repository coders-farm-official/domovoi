import DomovoiKit
import PhotosUI
import SwiftUI
import UniformTypeIdentifiers

/// A video in a home-server chat: the paperclip, the clip on the composer,
/// and the job's chip under the person's message.
///
/// Android's `ChatVideo.kt` (a6464354) is the spec for the small details and
/// the dashboard's `chat_videos.jsx` for the shapes; ``ChatVideoModel``
/// decides everything, and these views only draw it.
///
/// ### The paperclip holds video alone, on purpose
///
/// This app's house chat has no paperclip for pictures or documents
/// (``ChatView`` says why: the send body carries neither). So the paperclip
/// drawn here holds one section, Android's own item as its header, and the
/// two doors an iPhone has under it -- Photos and Files are separate system
/// sheets, where Android's one picker covers both. It is drawn **only** when
/// the house said `video.available` (``ChatVideoRules/offered(_:)``): with AI
/// features off, no ffmpeg, or a server from before 1.4.0 there is no
/// paperclip at all ([[no-misleading-controls]]).
///
/// ### What is not, and why there is no control for it
///
/// * **No "Ask Domovoi about this"** under a refusal. Neither app has a
///   per-error help button anywhere today (Android's video work adds none
///   either), so a refusal is said in the server's own sentence and nothing
///   more.
/// * **No summary in "what was kept"**: it is already in the conversation, as
///   the reply the core wrote (Android and the dashboard leave it out too).
struct ChatVideoAttachMenu: View {

    let enabled: Bool
    let photos: () -> Void
    let files: () -> Void

    var body: some View {
        Menu {
            Section(ChatVideoStrings.menuItem) {
                Button(ChatVideoStrings.fromPhotos, action: photos)
                Button(ChatVideoStrings.fromFiles, action: files)
            }
        } label: {
            Image(systemName: "paperclip")
                .font(.body)
                .frame(minWidth: 44, minHeight: 44)
                .contentShape(.rect)
        }
        .foregroundStyle(Color.fgMuted)
        .accessibilityLabel(ChatVideoStrings.attach)
        // One video a message: while one is on the composer the paperclip has
        // nothing it could add, so it says so by being dimmed rather than by
        // a toast after the pick.
        .disabled(!enabled)
    }
}

/// The clip on the composer: going up with its bar and an x to stop it, or
/// ready with its length, size and how long watching takes.
struct ChatVideoStagedChip: View {

    let staged: ChatVideoModel.Staged
    /// Nil: no x (the clip under a message just sent).
    let remove: (() -> Void)?

    var body: some View {
        VStack(alignment: .leading, spacing: 4) {
            nameRow
            detail
        }
        .padding(.horizontal, 10)
        .padding(.vertical, 6)
        .frame(maxWidth: 480, alignment: .leading)
        .background(RoundedRectangle(cornerRadius: 10).fill(Color.fgMuted.opacity(0.08)))
        .accessibilityElement(children: .contain)
    }

    private var nameRow: some View {
        HStack(spacing: 6) {
            ChatVideoFilmIcon()
            ChatVideoName(name: staged.name)
            Spacer(minLength: 0)
            removeButton
        }
    }

    @ViewBuilder
    private var removeButton: some View {
        if let remove {
            Button(action: remove) {
                Image(systemName: "xmark")
                    .font(.caption)
                    .frame(minWidth: 44, minHeight: 44)
                    .contentShape(.rect)
            }
            .buttonStyle(.plain)
            .foregroundStyle(Color.fgMuted)
            .accessibilityLabel(ChatVideoStrings.remove(staged.name))
        }
    }

    @ViewBuilder
    private var detail: some View {
        switch staged {
        case .sending(_, let percent):
            sending(percent)
        case .ready(let upload):
            ChatVideoMono(words: ChatVideoStrings.stagedSummary(upload))
        }
    }

    private func sending(_ percent: Int?) -> some View {
        let clamped: Double = Double(max(0, min(100, percent ?? 0)))
        return VStack(alignment: .leading, spacing: 4) {
            ChatVideoMono(words: ChatVideoStrings.sending(percent: percent))
                .accessibilityAddTraits(.updatesFrequently)
            ProgressView(value: clamped, total: 100)
                .tint(Color.brandInk)
                .accessibilityHidden(true)
        }
    }
}

/// While a Photos clip comes across from the picker's process.
struct ChatVideoReadingLine: View {
    var body: some View {
        HStack(spacing: 8) {
            ProgressView()
            Text(ChatVideoStrings.readingVideo)
                .font(.caption)
                .foregroundStyle(Color.fgMuted)
                .fixedSize(horizontal: false, vertical: true)
        }
    }
}

/// The film glyph at the head of a chip; decoration, so VoiceOver skips it.
struct ChatVideoFilmIcon: View {
    var body: some View {
        Image(systemName: "film")
            .font(.caption)
            .foregroundStyle(Color.fgMuted)
            .accessibilityHidden(true)
    }
}

/// A clip's name, on one line, cut in the middle so the ending shows.
struct ChatVideoName: View {
    let name: String
    var body: some View {
        Text(name)
            .font(.footnote)
            .lineLimit(1)
            .truncationMode(.middle)
    }
}

/// A chip's figures, in the monospaced face.
struct ChatVideoMono: View {
    let words: String
    var body: some View {
        Text(words)
            .font(.caption.monospaced())
            .foregroundStyle(Color.fgMuted)
            .fixedSize(horizontal: false, vertical: true)
    }
}

/// A sent video: the server's line while it is watched (with its bar and
/// stop), then how it ended, and "what was kept".
struct ChatVideoJobChip: View {

    let job: ChatVideoJob
    let video: ChatVideoModel

    private var line: String {
        job.line.isEmpty ? ChatVideoStrings.fallbackLine(job.state) : job.line
    }

    var body: some View {
        VStack(alignment: .leading, spacing: 4) {
            header
            lineText
            bar
            stopButton
            kept
            failure
        }
        .padding(.horizontal, 10)
        .padding(.vertical, 6)
        .frame(maxWidth: 480, alignment: .leading)
        .background(RoundedRectangle(cornerRadius: 10).fill(Color.fgMuted.opacity(0.08)))
        .accessibilityElement(children: .contain)
    }

    private var header: some View {
        HStack(spacing: 6) {
            ChatVideoFilmIcon()
            ChatVideoName(name: job.name)
            ChatVideoMono(words: ChatVideoRules.clock(job.durationS))
        }
    }

    private var lineText: some View {
        Text(line)
            .font(.caption)
            .fixedSize(horizontal: false, vertical: true)
            .multilineTextAlignment(.leading)
            // So VoiceOver hears the progress change rather than having to
            // go looking for it (Android's `liveRegion`).
            .accessibilityAddTraits(.updatesFrequently)
    }

    @ViewBuilder
    private var bar: some View {
        if let share = ChatVideoRules.progress(job) {
            ProgressView(value: share)
                .tint(Color.brandInk)
                .accessibilityLabel(ChatVideoStrings.watched(percent: Int((share * 100).rounded(.down))))
        }
    }

    @ViewBuilder
    private var stopButton: some View {
        if ChatVideoRules.stopOffered(job) {
            ChatVideoStopButton(busy: video.stopping.contains(job.id)) { video.stop(job) }
        }
    }

    @ViewBuilder
    private var kept: some View {
        if ChatVideoRules.keptOffered(job) {
            let open: Bool = video.keptOpen.contains(job.id)
            ChatVideoKeptToggle(open: open) { video.toggleKept(job) }
            if open { ChatVideoKeptView(kept: video.kept[job.id]) }
        }
    }

    @ViewBuilder
    private var failure: some View {
        if job.state == "failed", let error = job.error, !error.isEmpty {
            Text(error)
                .font(.caption)
                .foregroundStyle(Color.errInk)
                .fixedSize(horizontal: false, vertical: true)
        }
    }
}

/// "stop", or "stopping…" while it is asked.
struct ChatVideoStopButton: View {
    let busy: Bool
    let action: () -> Void

    var body: some View {
        Button(action: action) {
            Text(busy ? ChatVideoStrings.stopping : ChatVideoStrings.stop)
                .font(.subheadline)
                .frame(minHeight: 44)
                .contentShape(.rect)
        }
        .buttonStyle(.plain)
        .foregroundStyle(Color.errInk)
        .disabled(busy)
        .accessibilityHint(ChatVideoStrings.stopHint)
    }
}

/// "what was kept", with a chevron saying which way it goes.
struct ChatVideoKeptToggle: View {
    let open: Bool
    let action: () -> Void

    var body: some View {
        Button(action: action) {
            HStack(spacing: 4) {
                Text(ChatVideoStrings.whatWasKept)
                Image(systemName: open ? "chevron.up" : "chevron.down")
                    .font(.caption)
                    .accessibilityHidden(true)
            }
            .font(.subheadline)
            .frame(minHeight: 44)
            .contentShape(.rect)
        }
        .buttonStyle(.plain)
        .foregroundStyle(Color.brandInk)
        .accessibilityValue(open ? "open" : "closed")
    }
}

/// What was kept from a watched video: what was said with its times, and
/// what the pictures **might** show.
struct ChatVideoKeptView: View {

    let kept: ChatVideoModel.Kept?

    var body: some View {
        VStack(alignment: .leading, spacing: 4) {
            switch kept {
            case .loaded(let results)?:
                ChatVideoKeptResults(results: results)
            case .failed?:
                ChatVideoNote(words: ChatVideoStrings.couldNotLoadKept)
            case .loading?, nil:
                ChatVideoNote(words: ChatVideoStrings.loading)
            }
        }
    }
}

/// What was said, what the pictures might show, and the two notes.
struct ChatVideoKeptResults: View {

    let results: ChatVideoResults

    var body: some View {
        VStack(alignment: .leading, spacing: 4) {
            said
            pictures
            ChatVideoNote(words: ChatVideoStrings.videoDeleted)
        }
    }

    @ViewBuilder
    private var said: some View {
        if results.transcript.isEmpty {
            ChatVideoNote(words: ChatVideoStrings.noSpeech)
        } else {
            ChatVideoHead(words: ChatVideoStrings.whatWasSaid)
            ForEach(Array(results.transcript.enumerated()), id: \.offset) { (pair: (offset: Int, element: ChatVideoSpoken)) in
                ChatVideoTimedLine(at: pair.element.startS, words: pair.element.text)
            }
        }
    }

    @ViewBuilder
    private var pictures: some View {
        if !results.frames.isEmpty {
            ChatVideoHead(words: ChatVideoStrings.whatThePicturesMightShow)
            ForEach(Array(results.frames.enumerated()), id: \.offset) { (pair: (offset: Int, element: ChatVideoFrame)) in
                ChatVideoTimedLine(at: pair.element.atS, words: ChatVideoStrings.mightShow(pair.element.mightShow))
            }
            ChatVideoNote(words: ChatVideoStrings.guesses)
        }
    }
}

struct ChatVideoHead: View {
    let words: String
    var body: some View {
        Text(words)
            .font(.caption.weight(.semibold))
            .accessibilityAddTraits(.isHeader)
    }
}

struct ChatVideoTimedLine: View {
    let at: Double
    let words: String
    var body: some View {
        HStack(alignment: .firstTextBaseline, spacing: 8) {
            Text(ChatVideoRules.clock(at))
                .font(.caption.monospaced())
                .foregroundStyle(Color.fgMuted)
            Text(words)
                .font(.caption)
                .fixedSize(horizontal: false, vertical: true)
                .multilineTextAlignment(.leading)
        }
        .accessibilityElement(children: .combine)
    }
}

struct ChatVideoNote: View {
    let words: String
    var body: some View {
        Text(words)
            .font(.caption)
            .foregroundStyle(Color.fgMuted)
            .fixedSize(horizontal: false, vertical: true)
    }
}

/// The chips a row carries: its jobs, and -- for the person's newest row
/// that has no id yet -- the clip it was just sent with.
struct ChatVideoRowChips: View {

    let message: ChatLiveMessage
    let video: ChatVideoModel
    /// This is the newest of the person's rows still waiting for its id.
    let newestUnsent: Bool

    var body: some View {
        let jobs: [ChatVideoJob] = video.jobs(under: message.id)
        if !jobs.isEmpty {
            ForEach(jobs) { (job: ChatVideoJob) in
                ChatVideoJobChip(job: job, video: video)
            }
        } else if newestUnsent, message.id == nil, let sent = video.justSent {
            ChatVideoStagedChip(staged: .ready(sent), remove: nil)
        }
    }
}

// MARK: - a clip from Photos

/// A movie from PHPicker, copied out of the picker's process into this
/// app's own spool (``ChatVideoSpool``) under the name Photos gave it, and
/// deleted after the upload by the file's release.
///
/// A copy, because the file PHPicker hands over is only readable inside this
/// closure, and the upload reads it for minutes.
struct ChatVideoPick: Transferable, Sendable {

    let file: ChatVideoFile

    static var transferRepresentation: some TransferRepresentation {
        FileRepresentation(importedContentType: .movie) { received in
            let source = received.file
            let mime = UTType(filenameExtension: source.pathExtension)?.preferredMIMEType
            return ChatVideoPick(file: try ChatVideoSpool.copy(source, named: source.lastPathComponent, mime: mime))
        }
    }
}

/// A clip from Files: the person's own file, read in place behind its
/// security scope, which the file's release closes once the upload is done.
enum ChatVideoFiles {

    /// The kinds the document picker offers: every movie (MP4, M4V and MOV
    /// conform), and WebM, which has no system type of its own.
    static let types: [UTType] = [.movie] + [UTType(filenameExtension: "webm")].compactMap { $0 }

    static func file(at url: URL) -> ChatVideoFile {
        let scoped = url.startAccessingSecurityScopedResource()
        let mime = UTType(filenameExtension: url.pathExtension)?.preferredMIMEType
        return ChatVideoFile(url: url, name: url.lastPathComponent, mime: mime) {
            if scoped { url.stopAccessingSecurityScopedResource() }
        }
    }
}

// MARK: - the UI tests' clip

/// PHPicker and the document picker are out-of-process UI that XCUITest
/// cannot hold steady (``PhoneMediaFake``'s reason), so a UI case launches
/// with `-domovoi-chat-video-pick "<name>@<seconds>"` and both doors hand
/// over a small spooled file of that name, measured as that long. The real
/// pickers are a hand pass on a phone. DEBUG only.
enum ChatVideoFake {

    struct Pick: Sendable {
        let name: String
        let seconds: Double
    }

    static var active: Pick? {
        #if DEBUG
        let arguments = ProcessInfo.processInfo.arguments
        guard let index = arguments.firstIndex(of: "-domovoi-chat-video-pick"),
              arguments.index(after: index) < arguments.endIndex
        else { return nil }
        let value = arguments[arguments.index(after: index)]
        let parts = value.split(separator: "@", maxSplits: 1).map(String.init)
        guard parts.count == 2, let seconds = Double(parts[1]), !parts[0].isEmpty else { return nil }
        return Pick(name: parts[0], seconds: seconds)
        #else
        return nil
        #endif
    }

    /// The fake's file: 64 KiB of nothing in particular, spooled like a
    /// Photos copy and deleted by its release.
    static func file(_ pick: Pick) -> ChatVideoFile? {
        guard let slot = try? ChatVideoSpool.makeSlot() else { return nil }
        let url = slot.appendingPathComponent(pick.name, isDirectory: false)
        guard (try? Data(repeating: 7, count: 64 * 1024).write(to: url)) != nil else { return nil }
        return ChatVideoFile(url: url, name: pick.name) {
            try? FileManager.default.removeItem(at: slot)
        }
    }

    /// The fake's measure: its own length, and the file's real size.
    static func measure(_ pick: Pick) -> @Sendable (URL) async -> ChatVideoMeasure {
        let seconds = pick.seconds
        return { url in
            let real = await ChatVideoMeasure.of(url)
            return ChatVideoMeasure(seconds: seconds, bytes: real.bytes)
        }
    }
}
