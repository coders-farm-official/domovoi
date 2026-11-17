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
            HStack(spacing: 6) {
                Image(systemName: "film")
                    .font(.caption)
                    .foregroundStyle(Color.fgMuted)
                    .accessibilityHidden(true)
                Text(staged.name)
                    .font(.footnote)
                    .lineLimit(1)
                    .truncationMode(.middle)
                Spacer(minLength: 0)
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
            switch staged {
            case .sending(_, let percent):
                Text(ChatVideoStrings.sending(percent: percent))
                    .font(.caption.monospaced())
                    .foregroundStyle(Color.fgMuted)
                    .accessibilityAddTraits(.updatesFrequently)
                ProgressView(value: Double(max(0, min(100, percent ?? 0))), total: 100)
                    .tint(Color.brandInk)
                    .accessibilityHidden(true)
            case .ready(let upload):
                Text(ChatVideoStrings.stagedSummary(upload))
                    .font(.caption.monospaced())
                    .foregroundStyle(Color.fgMuted)
                    .fixedSize(horizontal: false, vertical: true)
            }
        }
        .padding(.horizontal, 10)
        .padding(.vertical, 6)
        .frame(maxWidth: 480, alignment: .leading)
        .background(RoundedRectangle(cornerRadius: 10).fill(Color.fgMuted.opacity(0.08)))
        .accessibilityElement(children: .contain)
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
            HStack(spacing: 6) {
                Image(systemName: "film")
                    .font(.caption)
                    .foregroundStyle(Color.fgMuted)
                    .accessibilityHidden(true)
                Text(job.name)
                    .font(.footnote)
                    .lineLimit(1)
                    .truncationMode(.middle)
                Text(ChatVideoRules.clock(job.durationS))
                    .font(.caption.monospaced())
                    .foregroundStyle(Color.fgMuted)
            }
            Text(line)
                .font(.caption)
                .fixedSize(horizontal: false, vertical: true)
                .multilineTextAlignment(.leading)
                // So VoiceOver hears the progress change rather than having
                // to go looking for it (Android's `liveRegion`).
                .accessibilityAddTraits(.updatesFrequently)
            if let share = ChatVideoRules.progress(job) {
                ProgressView(value: share)
                    .tint(Color.brandInk)
                    .accessibilityLabel(ChatVideoStrings.watched(percent: Int((share * 100).rounded(.down))))
            }
            if ChatVideoRules.stopOffered(job) {
                let busy = video.stopping.contains(job.id)
                Button {
                    video.stop(job)
                } label: {
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
            if ChatVideoRules.keptOffered(job) {
                let open = video.keptOpen.contains(job.id)
                Button {
                    video.toggleKept(job)
                } label: {
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
                if open { ChatVideoKeptView(kept: video.kept[job.id]) }
            }
            if job.state == "failed", let error = job.error, !error.isEmpty {
                Text(error)
                    .font(.caption)
                    .foregroundStyle(Color.errInk)
                    .fixedSize(horizontal: false, vertical: true)
            }
        }
        .padding(.horizontal, 10)
        .padding(.vertical, 6)
        .frame(maxWidth: 480, alignment: .leading)
        .background(RoundedRectangle(cornerRadius: 10).fill(Color.fgMuted.opacity(0.08)))
        .accessibilityElement(children: .contain)
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
                if results.transcript.isEmpty {
                    note(ChatVideoStrings.noSpeech)
                } else {
                    head(ChatVideoStrings.whatWasSaid)
                    ForEach(Array(results.transcript.enumerated()), id: \.offset) { _, piece in
                        timed(piece.startS, piece.text)
                    }
                }
                if !results.frames.isEmpty {
                    head(ChatVideoStrings.whatThePicturesMightShow)
                    ForEach(Array(results.frames.enumerated()), id: \.offset) { _, frame in
                        timed(frame.atS, ChatVideoStrings.mightShow(frame.mightShow))
                    }
                    note(ChatVideoStrings.guesses)
                }
                note(ChatVideoStrings.videoDeleted)
            case .failed?:
                note(ChatVideoStrings.couldNotLoadKept)
            case .loading?, nil:
                note(ChatVideoStrings.loading)
            }
        }
    }

    private func head(_ words: String) -> some View {
        Text(words)
            .font(.caption.weight(.semibold))
            .accessibilityAddTraits(.isHeader)
    }

    private func timed(_ at: Double, _ words: String) -> some View {
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

    private func note(_ words: String) -> some View {
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
        let jobs = video.jobs(under: message.id)
        if !jobs.isEmpty {
            ForEach(jobs) { job in
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
