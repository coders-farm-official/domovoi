import AVFoundation
import DomovoiKit
import Foundation
import Observation
import SwiftUI

/// The talk screen's one DEBUG switch: a canned recording in place of the
/// microphone.
///
/// ``AuditStrip`` (`WorkspaceView.swift:238`) is the shape, and this exists
/// for the same reason that one does — a Simulator cannot be held up to
/// somebody's mouth. Without it a UI case for a spoken turn would have to
/// get past iOS's own microphone alert, which an XCUITest can only do through
/// an interruption monitor, and then record a second of Simulator silence and
/// hope the clip-length rules took it.
///
/// With the switch set, ``ChatMoreTalker`` never touches `AVAudioSession` or
/// `AVAudioRecorder` at all: a press makes the stated number of seconds of
/// 16 kHz mono PCM16 **zeroes**, which is exactly what the core takes and
/// what ``ChatMoreVoiceRules/recorded(seconds:interrupted:)`` measures. So
/// the whole turn — the phase machine, the multipart body, the refusals, the
/// re-read — is the real one; only the microphone is not.
enum ChatMoreTestSeam {

    /// Seconds of canned audio a press produces, or nil for the microphone.
    static var talkSeconds: Double? {
        #if DEBUG
        guard let raw = value(after: "-domovoi-chatmore-talk"), let seconds = Double(raw) else {
            return nil
        }
        return seconds
        #else
        return nil
        #endif
    }

    private static func value(after flag: String) -> String? {
        let arguments = ProcessInfo.processInfo.arguments
        guard let at = arguments.firstIndex(of: flag), at + 1 < arguments.count else { return nil }
        return arguments[at + 1]
    }
}

/// `AVSpeechSynthesizer`, boxed on the main actor.
///
/// The class is not `Sendable` and SwiftUI's `@State` would hold it across an
/// actor boundary the compiler cannot check. One `@MainActor` box, three
/// methods, and the view never names the framework type.
@MainActor
final class ChatMoreSpeech {

    private let synthesizer = AVSpeechSynthesizer()

    var isSpeaking: Bool { synthesizer.isSpeaking }

    func speak(_ text: String) {
        synthesizer.speak(AVSpeechUtterance(string: text))
    }

    func stopSpeaking(at boundary: AVSpeechBoundary) {
        synthesizer.stopSpeaking(at: boundary)
    }
}

/// "read aloud", on the draft canvas.
///
/// `DraftCanvas`'s own read-aloud (`DraftUi.kt:421`), and the **one** place in
/// this item where the phone speaks: a draft is this phone's text, written on
/// this phone's screen, and `AVSpeechSynthesizer` is the platform's own
/// text-to-speech with no licence question and no model to download. It is
/// deliberately **not** used for a chat reply — Android speaks a reply only
/// when a downloaded Piper voice is this phone's own pick
/// (`replyVoiceOnPhone`, `PhoneVoice.kt:148`), and a system voice reading an
/// answer the house meant to say in its own would be the silent fallback
/// [[no-misleading-controls]] is about.
///
/// Not drawn at all for an empty draft, which is the same rule.
struct ChatMoreReadAloudButton: View {

    let text: String

    /// One synthesizer per button rather than a shared one: a canvas closed
    /// mid-sentence takes its synthesizer with it, and two canvases cannot
    /// talk over each other because only one is ever up.
    ///
    /// Wrapped in a ``ChatMoreSpeech`` rather than held as an
    /// `AVSpeechSynthesizer` directly: the synthesizer is not `Sendable`, and
    /// a `@MainActor` box around it is how Swift 6 can see that only the main
    /// actor ever touches it.
    @State private var speaker = ChatMoreSpeech()
    @State private var speaking = false

    var body: some View {
        if !text.trimmingCharacters(in: .whitespacesAndNewlines).isEmpty {
            HomeLink(speaking ? ChatMoreStrings.stop : ChatMoreStrings.readAloud) {
                if speaking {
                    speaker.stopSpeaking(at: .immediate)
                    speaking = false
                } else {
                    speaker.speak(text)
                    speaking = true
                }
            }
            // The synthesizer's own `isSpeaking` is the truth, and it stops
            // on its own at the end of the text; polling it is how the label
            // comes back without an `AVSpeechSynthesizerDelegate`, whose
            // callbacks arrive `nonisolated` and would be the only part of
            // this file Swift 6 could not check.
            .task(id: speaking) {
                guard speaking else { return }
                while !Task.isCancelled, speaker.isSpeaking {
                    try? await Task.sleep(for: .milliseconds(400))
                }
                if !Task.isCancelled { speaking = false }
            }
            .onDisappear { speaker.stopSpeaking(at: .immediate) }
        }
    }
}

/// Records one held turn and plays the reply back.
///
/// `HoldToTalkRecorder` (`voice/HoldToTalk.kt:90`) and `ReplyPlayer`
/// (`voice/ReplyPlayer.kt:75`) as one object, because on iOS they are one
/// `AVAudioSession`: the category has to be `.playAndRecord` for both halves,
/// and whichever of them set it last would otherwise win.
///
/// ### Why `AVAudioRecorder` and not `AVAudioEngine`
///
/// Android taps an `AudioRecord` and accumulates PCM16 itself because
/// Android's recorder writes a container this app would have to parse back
/// out. `AVAudioRecorder` **already** writes 16 kHz mono PCM16 when it is
/// asked to (`AVLinearPCMBitDepthKey`), in a WAV — and ``ChatMoreWav/parse(_:)``,
/// which this item ported for the *reply*, reads a WAV. So the capture is one
/// recorder and one call to a tested parser, instead of an `AVAudioEngine`
/// graph with an `AVAudioConverter` hanging off a real-time tap.
///
/// ### What is not here, and why there is no control for it
///
/// * **No per-chunk level.** Android computes the ring's value from the
///   buffer it just read (`level`, `HoldToTalk.kt:173`); `AVAudioRecorder`
///   meters for us, in decibels, so ``ChatMoreVoiceRules/level(_:)`` has no
///   caller on iOS. It is kept, tested and unused — DECISIONS.md records that
///   as the one seam this item leaves without one, rather than deleting a
///   ported function and making the port silently narrower.
/// * **No `AVAudioPlayerDelegate`.** The reply's end is noticed by the same
///   main-actor timer that drives the ring, because a delegate callback
///   arrives `nonisolated` and the hop back would be the only piece of this
///   file Swift 6 could not check.
@MainActor
@Observable
final class ChatMoreTalker {

    /// 0...1, the level ring's value. ``ChatMoreVoiceRules/level(_:)``'s
    /// range, from the recorder's meter rather than from samples.
    private(set) var level: Double = 0

    /// How long the turn being recorded is, so the view can stop itself at
    /// the core's cap (``ChatMoreVoiceRules/atCap(seconds:)``).
    private(set) var seconds: Double = 0

    /// Set when the microphone could not be opened — the one thing a person
    /// has to be told about rather than left guessing at.
    private(set) var trouble: String?

    private var recorder: AVAudioRecorder?
    private var player: AVAudioPlayer?
    private var file: URL?
    private var startedAt: Date?

    /// Whether anything is being recorded right now.
    var recording: Bool { recorder != nil || (ChatMoreTestSeam.talkSeconds != nil && startedAt != nil) }

    /// Whether the reply is still being spoken.
    var speaking: Bool { player?.isPlaying == true }

    /// Whether a reply has been handed to the player at all.
    ///
    /// The talk screen's tick reads `.speaking && !speaking` as "the reply
    /// finished", but the WAV is started from an `onChange` of
    /// ``ChatMoreModel/replyAudio`` — and the model sets `replyAudio` and
    /// `talkPhase` together, so the tick's `Task.sleep` continuation and
    /// SwiftUI's view update are both main-actor work with no ordering
    /// between them. A tick that resumed in that window saw `.speaking` with
    /// nothing playing yet and called ``ChatMoreModel/finishedSpeaking()``,
    /// which puts `replyAudio` back to nil — so the `onChange` never fired
    /// and the house's spoken reply was dropped, about one frame in every
    /// 100 ms. This flag is the tick's proof that there is something to be
    /// finished.
    private(set) var started = false

    // MARK: - permission

    /// Whether this phone may use its microphone, asking once if nobody has
    /// been asked.
    ///
    /// Android checks `RECORD_AUDIO`, asks, and refuses with
    /// ``ChatMoreStrings/microphoneNeeded`` (`ChatScreen.kt:481`). The alert's
    /// own words are `NSMicrophoneUsageDescription` (`project.yml:163`), which
    /// already says "when you hold the talk button" — so this item adds no
    /// usage string.
    func mayRecord() async -> Bool {
        if ChatMoreTestSeam.talkSeconds != nil { return true }
        switch AVAudioApplication.shared.recordPermission {
        case .granted:
            return true
        case .denied:
            return false
        default:
            return await withCheckedContinuation { settled in
                AVAudioApplication.requestRecordPermission { allowed in
                    settled.resume(returning: allowed)
                }
            }
        }
    }

    // MARK: - recording

    /// Start a turn. Answers whether anything is recording.
    @discardableResult
    func start() -> Bool {
        guard recorder == nil else { return true }
        trouble = nil
        level = 0
        seconds = 0
        stopPlaying()
        startedAt = Date()
        // The canned turn: no session, no recorder, no alert.
        if ChatMoreTestSeam.talkSeconds != nil { return true }

        let url = FileManager.default.temporaryDirectory
            .appendingPathComponent("chatmore-turn-\(UUID().uuidString).wav")
        let settings: [String: Any] = [
            AVFormatIDKey: Int(kAudioFormatLinearPCM),
            AVSampleRateKey: Double(ChatMoreVoiceTurn.sampleRate),
            AVNumberOfChannelsKey: 1,
            AVLinearPCMBitDepthKey: 16,
            AVLinearPCMIsBigEndianKey: false,
            AVLinearPCMIsFloatKey: false,
        ]
        do {
            let session = AVAudioSession.sharedInstance()
            // `.playAndRecord` because the reply is played through the same
            // session, and `.duckOthers` because Android takes exclusive
            // transient focus while recording and ducks while speaking
            // (`VoiceFocus`, `ReplyPlayer.kt:155`). `.defaultToSpeaker` so a
            // reply is not whispered into the earpiece, which is what
            // `.playAndRecord` does on its own.
            try session.setCategory(
                .playAndRecord,
                mode: .spokenAudio,
                options: [.duckOthers, .defaultToSpeaker, .allowBluetooth]
            )
            try session.setActive(true)
            let made = try AVAudioRecorder(url: url, settings: settings)
            made.isMeteringEnabled = true
            guard made.record() else {
                trouble = ChatMoreStrings.micDidntOpen("the recorder would not start")
                startedAt = nil
                return false
            }
            recorder = made
            file = url
            return true
        } catch {
            trouble = ChatMoreStrings.micDidntOpen(error.localizedDescription)
            startedAt = nil
            return false
        }
    }

    /// The turn's audio: 16 kHz mono PCM16 with no container, which is what
    /// the core takes (`phone_voice.py:97`).
    ///
    /// The WAV the recorder wrote is read back through ``ChatMoreWav/parse(_:)``
    /// — the same parser the reply goes through — and the file is deleted
    /// whatever happens, because a turn somebody cancelled must not be left
    /// on disk.
    func finish() -> Data {
        defer { tidy() }
        if let canned = ChatMoreTestSeam.talkSeconds {
            let frames = Int(Double(ChatMoreVoiceTurn.sampleRate) * max(0, canned))
            return Data(count: frames * 2)
        }
        recorder?.stop()
        guard let url = file, let bytes = try? Data(contentsOf: url) else { return Data() }
        return ChatMoreWav.parse(bytes)?.data ?? Data()
    }

    /// The turn was thrown away.
    func cancel() {
        recorder?.stop()
        tidy()
    }

    /// Called by the view's timer: the ring's value and the clock.
    func tick() {
        if ChatMoreTestSeam.talkSeconds != nil, let started = startedAt, recorder == nil {
            seconds = Date().timeIntervalSince(started)
            // A canned turn is silent, so the ring is given the one value
            // that is honest about it.
            level = 0
            return
        }
        guard let recorder else { return }
        recorder.updateMeters()
        seconds = recorder.currentTime
        // `averagePower` is decibels full scale: -160 is silence and 0 is as
        // loud as the input goes. Speech near a phone sits around -25, which
        // this maps to about 0.6 — the same part of the ring Android's
        // RMS × 4 puts it in.
        let decibels = Double(recorder.averagePower(forChannel: 0))
        let floor = -50.0
        level = min(1, max(0, (decibels - floor) / -floor))
    }

    private func tidy() {
        recorder = nil
        startedAt = nil
        level = 0
        if let url = file { try? FileManager.default.removeItem(at: url) }
        file = nil
    }

    // MARK: - the spoken reply

    /// Play the WAV the server sent back. Answers whether it started.
    ///
    /// **The server's audio only.** Android speaks on the phone just when a
    /// downloaded Piper voice is this phone's own pick (`replyVoiceOnPhone`,
    /// `PhoneVoice.kt:148`), and this app has no voice catalogue and may not
    /// use sherpa-onnx's prebuilts (GPL, NIGHT-RULES §1) — so
    /// `AVSpeechSynthesizer` is not a fallback here. A phone with no voice
    /// downloaded is exactly what Android does nothing on either.
    @discardableResult
    func play(_ wav: Data) -> Bool {
        stopPlaying()
        do {
            let made = try AVAudioPlayer(data: wav)
            made.prepareToPlay()
            guard made.play() else { return false }
            player = made
            started = true
            return true
        } catch {
            // Said nowhere: a reply whose audio will not decode has its words
            // in the transcript, and a sentence about the audio would be the
            // second thing said about one turn.
            return false
        }
    }

    /// Stop the reply, which is what a press during one means
    /// (``ChatMoreStrings/pressToStop``).
    func stopPlaying() {
        player?.stop()
        player = nil
        started = false
    }

    /// Give the session back when the talk screen closes, so whatever was
    /// playing before this screen opened can come back up
    /// (`.notifyOthersOnDeactivation`).
    func release() {
        cancel()
        stopPlaying()
        guard ChatMoreTestSeam.talkSeconds == nil else { return }
        try? AVAudioSession.sharedInstance().setActive(
            false, options: .notifyOthersOnDeactivation
        )
    }
}
