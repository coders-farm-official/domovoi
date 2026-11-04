import AVFoundation
import DomovoiKit
import Foundation

/// Why a sample that arrived still could not be heard.
///
/// `AVAudioPlayer(data:)` throws for bytes it cannot decode, and that throw
/// carries an `NSError` whose description is not a sentence anybody wants to
/// read; `prepareToPlay()` and `play()` do not throw at all, they return
/// `false`. So both are turned into this, and
/// ``VoiceSettingsModel/sampleArrived(_:for:from:)`` words it with
/// ``VoiceSettingsStrings/sampleFailed(_:)``.
///
/// It is a struct with a `description` rather than an enum because the model
/// reads it as `"\(error)"` — a sentence is what is wanted at that call site,
/// and an enum case name is not one.
struct VoiceSettingsSampleTrouble: Error, CustomStringConvertible {
    let description: String
}

/// The thing that actually makes the noise: ``VoiceSettingsSampleSink`` over
/// `AVAudioPlayer`.
///
/// Everything that is a *decision* — which voice may be played at all, which
/// id the bytes belong to, what a server switch mid-fetch does — is in
/// ``VoiceSettingsModel`` and tested there. What is left here is the player
/// and the `AVAudioSession` category, which no test can ask a question of.
///
/// ### The session dance, and why it is repeated rather than shared
///
/// `Videos/FeedEngine.swift:92-122` does the same two things — set
/// `.playback` and activate before playing, deactivate when finished — and
/// this does not call into it. `FeedEngine` is a video player holding a
/// `AVPlayer`, a ticker, an idle-timer hold and a position writer; the two
/// lines in common are two lines, and a shared "audio session" object that
/// both of them owned would have to decide which of them wins when a sample
/// is tapped while a video is playing. It does not come up (Videos is a
/// different screen and a sample is 0.2 seconds), and a wrong answer to it
/// would be a video silently losing its audio — so the duplication is the
/// smaller risk, and this comment is the record of the choice.
///
/// `.playback` rather than `.ambient` on purpose: a person tapping *play a
/// sample* asked to hear it, so the ring switch should not silence it, which
/// is the same reasoning `FeedEngine` applies to a video they pressed play on.
///
/// ## What is not here, and why there is no control for it
///
/// This plays bytes the **server** rendered. It does not speak: there is no
/// text-to-speech path on iOS at all — no phonemizer
/// (`ios/Packages/DomovoiPhonemizer/` is empty on purpose), no ONNX Runtime,
/// no model store — so there is no "say this out loud", no voice to pick, and
/// nothing to download or delete. Android's `PhoneVoice.playSample()`
/// (`voice/PhoneVoice.kt:372-406`) falls back to a clip bundled in the APK
/// when the voice is not on the phone; the equivalent clip here arrives from
/// the server as **Ogg Opus** (`CLIP_MEDIA_TYPE`, `domovoi/voice_sample.py:22`),
/// which `AVAudioPlayer` does not decode, so those voices are never asked for
/// — ``VoiceSettingsSamples/isPlayable(_:)`` is the gate, and the button is
/// simply not drawn. A button that fetched a clip and played silence is the
/// failure `PhoneVoice.kt:371` records Android shipping once.
@MainActor
final class VoiceSettingsSamplePlayer: NSObject, VoiceSettingsSampleSink, AVAudioPlayerDelegate {

    /// Held so it is not deallocated mid-play — an `AVAudioPlayer` nobody
    /// retains stops making noise, silently, which looks exactly like a
    /// sample that did not arrive.
    private var player: AVAudioPlayer?

    /// What the model wants run when a sample reaches its own end. Cleared by
    /// ``stop()``, because a stop is not an ending and must not report one
    /// (the protocol says so: a `stop` that called `onFinish` would clear the
    /// state a *newer* play had just set).
    private var whenDone: (@MainActor () -> Void)?

    func play(_ data: Data, onFinish: @escaping @MainActor () -> Void) throws {
        stop()
        let made: AVAudioPlayer
        do {
            made = try AVAudioPlayer(data: data)
        } catch {
            // The server answered and the bytes will not decode: either it
            // sent something other than the WAV the route promises
            // (`voice.py:177-184`) or they were truncated. Either way the
            // person gets a sentence, not silence.
            throw VoiceSettingsSampleTrouble(description: VoiceSettingsStrings.samplePhoneCouldNotRead)
        }
        made.delegate = self
        // Before `play()`, not after: activating a session while a player is
        // already running is where a first sample comes out clipped.
        do {
            try AVAudioSession.sharedInstance().setCategory(.playback, mode: .default)
            try AVAudioSession.sharedInstance().setActive(true)
        } catch {
            // Not fatal and not reported, for `FeedEngine`'s reason: the
            // sample usually still plays, and "audio unavailable" over audio
            // that is about to be heard is worse than saying nothing. If it
            // really cannot play, `play()` below returns false and that *is*
            // reported.
        }
        guard made.prepareToPlay(), made.play() else {
            throw VoiceSettingsSampleTrouble(description: VoiceSettingsStrings.samplePhoneWouldNotPlay)
        }
        player = made
        whenDone = onFinish
    }

    func stop() {
        player?.stop()
        player = nil
        whenDone = nil
        // Let go of the session, so a sample does not keep the app owning
        // audio after it has finished with it.
        try? AVAudioSession.sharedInstance().setActive(false)
    }

    // MARK: - AVAudioPlayerDelegate

    /// **`nonisolated`, with a hop.** `AVAudioPlayerDelegate` is not
    /// `@MainActor`, and under Swift 6 a `@MainActor` method cannot witness a
    /// non-isolated protocol requirement. The callback carries nothing across
    /// the hop — `flag` is a `Bool` and the player is deliberately not
    /// captured — so there is nothing non-`Sendable` in flight.
    ///
    /// `flag` is ignored: a sample that stopped early and a sample that
    /// finished both end with nothing playing, and the button has one thing
    /// to say about that.
    nonisolated func audioPlayerDidFinishPlaying(_ player: AVAudioPlayer, successfully flag: Bool) {
        Task { @MainActor [weak self] in self?.finished() }
    }

    private func finished() {
        let done = whenDone
        player = nil
        whenDone = nil
        try? AVAudioSession.sharedInstance().setActive(false)
        // The model's closure checks that it is still this voice before it
        // clears anything (``VoiceSettingsModel/sampleArrived(_:for:from:)``),
        // so a finish that arrives after a newer play cannot undo it.
        done?()
    }
}
