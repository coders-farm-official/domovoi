import Foundation
import Observation

/// Whatever actually makes a noise. The model fetches the bytes; this plays
/// them.
///
/// A protocol rather than an `AVAudioPlayer` here for the reason every model
/// in this package is a model: the decisions — which voice may be played at
/// all, which id the bytes that came back belong to, what happens when the
/// server is switched mid-fetch — are decisions, and a test cannot ask
/// `AVAudioPlayer` whether the model got them right. The real one is
/// `ios/Domovoi/Shell/VoiceSettingsSamplePlayer.swift`, which also owns the
/// `AVAudioSession` category dance (`Videos/FeedEngine.swift:92-122` is the
/// same dance, and the reason it is not repeated in two places unexamined).
@MainActor
public protocol VoiceSettingsSampleSink: AnyObject {

    /// Play `data`. Throws when the bytes will not decode, which the view
    /// turns into ``VoiceSettingsStrings/sampleFailed(_:)`` rather than
    /// silence — a sample that plays nothing and says nothing is the failure
    /// `PhoneVoice.kt:371` records on Android.
    ///
    /// - Parameter onFinish: run when it reaches the end on its own. The
    ///   model clears ``VoiceSettingsModel/playingVoiceID`` there, so the
    ///   button goes back to saying "play a sample" without the view
    ///   watching anything.
    func play(_ data: Data, onFinish: @escaping @MainActor () -> Void) throws

    /// Stop whatever is playing. Must not call `onFinish`.
    func stop()
}

/// The Voices section's model: two reads of the server, and the one sample it
/// can honestly play.
///
/// Android's counterpart is `PhoneVoice` (`voice/PhoneVoice.kt`, 482 lines) —
/// but only about a fifth of it has a subject here. `PhoneVoice` is mostly a
/// **model store**: `wantedKey`, `open`, `openOnPhone`, `download`,
/// `pauseDownload`, `delete`, `setAllowMobileData`, `onWork` and the per-server
/// body cache all exist so the phone can speak with a voice on its own disk.
/// This phone speaks with nothing, so what is left is: read `GET /api/voice`,
/// read `GET /api/voice/samples`, and fetch one sample from
/// `GET /api/voice/sample/{id}` for a voice the server says it can really
/// speak.
///
/// ### The two reads are two reads
///
/// Not one `VoiceSettingsState` that goes loading/loaded/failed. ``ApiRead``'s
/// own doc argues this for Home and it holds here for the same reason: the
/// samples read failing must not blank the voice list, because the list is
/// the honest part and the samples map only decides whether a *button* is
/// drawn. A samples read that has not answered draws no buttons, which is the
/// safe side of the no-misleading-controls rule and not a loss of the screen.
///
/// Neither read subscribes to a push: the server emits no voice event
/// (grepped `web/backend/api/voice.py` — every mutation reloads the core and
/// publishes nothing). Android has the same two triggers and no more:
/// `LaunchedEffect(voice)` once and `LaunchedEffect(serverUrl)` on a switch
/// (`VoicePanel.kt:122-129`), which is ``ApiRead``'s own server key.
///
/// ## What is not here, and why there is no control for it
///
/// No `setChoice`, no `download`, no `pauseDownload`, no `delete`, no
/// `setAllowMobileData`, no `prepare`, no local sample. There is no
/// phonemizer, no ONNX Runtime, no model store and nothing on iOS that
/// speaks, so each of those would set a value nothing reads or start work
/// nothing can finish. The list, with reasons, is in
/// `design-notes/ios/overnight/voicesettings/MAP.md` §5.
@MainActor
@Observable
public final class VoiceSettingsModel {

    /// `GET /api/voice` — `VOICE_PATH` (`PhoneVoice.kt:87`).
    public static let infoPath = "/api/voice"

    /// `GET /api/voice/samples` (`voice.py:147`).
    public static let samplesPath = "/api/voice/samples"

    /// `GET /api/voice/sample/{voice_id}` (`voice.py:169`). The id is the
    /// `model_ref`, percent-encoded because it is a path segment the server
    /// matches literally.
    public static func samplePath(_ voiceID: String) -> String {
        let escaped = voiceID.addingPercentEncoding(
            withAllowedCharacters: .alphanumerics.union(CharacterSet(charactersIn: "-._~"))
        ) ?? voiceID
        return "/api/voice/sample/\(escaped)"
    }

    /// The server may spend up to 120 s synthesizing a sample
    /// (`voice.py:185`, `post_admin_bytes(… timeout=120.0)`). The default
    /// client timeout is shorter, so a sample asked for on a cold core would
    /// fail on this side while the server was still working.
    public static let sampleTimeout: TimeInterval = 120

    public let info: ApiRead<VoiceSettingsInfo>
    public let samples: ApiRead<VoiceSettingsSamples>

    /// The voice whose sample is being fetched, if any. One at a time: a
    /// second tap on another row replaces this one, and the first one's bytes
    /// are dropped when they land (see ``playSample(_:)``).
    public private(set) var fetchingSampleID: String?

    /// The voice that is playing, if any. The play/stop button reads this,
    /// and nothing else writes it.
    public private(set) var playingVoiceID: String?

    /// The last sample failure, as the server worded it. Cleared by the next
    /// attempt.
    public private(set) var sampleError: String?

    /// How many sample requests this model has actually made, for a test that
    /// has to say "and nothing else was asked".
    public private(set) var sampleFetches = 0

    /// The last path a sample was asked at, for the same kind of test.
    public private(set) var lastSamplePath: String?

    private let container: AppContainer
    private let sink: any VoiceSettingsSampleSink
    private var inFlight: Task<Void, Never>?
    private var serverSink: Int?

    public init(in container: AppContainer, sink: any VoiceSettingsSampleSink) {
        self.container = container
        self.sink = sink
        let client = container.client
        self.info = ApiRead(in: container) {
            try await client.get(Self.infoPath, as: VoiceSettingsInfo.self)
        }
        self.samples = ApiRead(in: container) {
            try await client.get(Self.samplesPath, as: VoiceSettingsSamples.self)
        }
        // The two reads re-key themselves on a server switch; the **noise**
        // does not, and a sample of the house the person has just left going
        // on playing under the new house's voice list is the audible form of
        // the same defect. Weakly, so a model whose section has gone is not
        // kept alive by the container — the leak a review found in the
        // network sinks.
        //
        // It stops the **playback** and deliberately does not cancel a fetch
        // in flight: that one is dropped by ``claim(_:from:)`` when it lands,
        // which is the rule being relied on, and a sink that cancelled it
        // instead would quietly replace the guard with a race and leave the
        // guard untested.
        serverSink = container.onServerChange { [weak self] in
            Task { @MainActor in self?.stopPlayback() }
        }
    }

    /// Read both. `VoicePanel.kt:123`'s `voice.refresh()` plus
    /// `:128`'s `fetchFromServer()`, which are one thing here because
    /// nothing is cached per server.
    public func refresh() {
        info.refresh()
        samples.refresh()
    }

    /// The section is going away: stop the reads, stop the noise.
    ///
    /// **The model owns the task, not a row.** A lazy row that held this
    /// `Task` would keep a 120-second request alive after it scrolled out of
    /// the list, and a `@State` player inside a row would keep playing.
    public func stop() {
        info.stop()
        samples.stop()
        stopSample()
        if let serverSink {
            container.removeServerChangeSink(serverSink)
            self.serverSink = nil
        }
    }

    // MARK: - the one control

    /// Whether a play button may be drawn for `id`.
    ///
    /// Three things must all be true, and the view asks this one question
    /// rather than assembling them itself: the samples read has answered, the
    /// server said `"voice"` for this id, and the voice is one the info read
    /// listed. The last is not pedantry — the sample route 404s for anything
    /// outside `PIPER_PINS` (`voice.py:171`), so a button for a voice this
    /// screen is not drawing could only fail.
    public func canPlay(_ id: String) -> Bool {
        guard let answered = samples.data, answered.isPlayable(id) else { return false }
        return info.data?.voices.contains { $0.id == id } ?? false
    }

    /// Why `id` has no button, for the quiet line that replaces it; `nil`
    /// when it has one or when the samples read has not answered yet (then
    /// there is nothing to say either way).
    public func whyNoSample(_ id: String) -> String? {
        guard samples.data != nil, !canPlay(id) else { return nil }
        return VoiceSettingsStrings.noSampleToPlay
    }

    /// Tap the play/stop button on `id`'s row.
    ///
    /// Tapping the row that is playing stops it, which is what the single
    /// button means over there too (`VoicePanel.kt:210`: one `OutlinedButton`
    /// whose label is `"stop"` while `ui.sampling`).
    public func tappedSample(_ id: String) {
        if playingVoiceID == id || fetchingSampleID == id {
            stopSample()
            return
        }
        playSample(id)
    }

    /// Fetch `id`'s sample and play it.
    ///
    /// ### The read-after-await rule, which is this model's one real hazard
    ///
    /// Two things can move while the request is in flight — and one of them
    /// can take two minutes — so **both are captured before the `await` and
    /// checked after it**:
    ///
    /// * **the server.** `container.store.serverURL` is read first and
    ///   compared afterwards. Without it, a tap, a switch to another house
    ///   and the first house's answer would play *that* house's voice under
    ///   this one's name. This is the top defect class on this track (a reply
    ///   for server A landing under server B), and Android guards the same
    ///   await in the same way — `fetchFromServer()` re-reads
    ///   `prefs.serverUrl` after its await before it caches
    ///   (`PhoneVoice.kt:264`).
    /// * **the row.** `fetchingSampleID` is the id this model is still
    ///   waiting for. A tap on Norman, then a tap on Kristin, then Norman's
    ///   bytes arriving must not play under Kristin's button.
    ///
    /// Failing either check **drops the bytes**. It does not play them
    /// quietly and it does not report an error: nobody asked for this, and a
    /// failure line about a house the person has left would be a second
    /// wrong answer.
    public func playSample(_ id: String) {
        guard canPlay(id) else { return }
        inFlight?.cancel()
        sink.stop()
        playingVoiceID = nil
        sampleError = nil
        fetchingSampleID = id
        let client = container.client
        let path = Self.samplePath(id)
        lastSamplePath = path
        sampleFetches += 1
        // Captured BEFORE the await. Read off the store rather than off any
        // snapshot of it, because the store is what the client aims the
        // request at.
        let asked = container.store.serverURL
        inFlight = Task { @MainActor [weak self] in
            // A `Result` rather than two `catch` arms: `raw` is typed
            // `throws(DomovoiError)`, so one `catch` is the whole story, and
            // the cancellation check belongs after the await in both arms —
            // a cancelled request's failure is not a failure to report.
            let outcome: Result<Data, DomovoiError>
            do {
                outcome = .success(
                    try await client.raw(method: "GET", path: path, timeout: Self.sampleTimeout)
                )
            } catch {
                outcome = .failure(error)
            }
            guard !Task.isCancelled else { return }
            switch outcome {
            case .success(let bytes):
                self?.sampleArrived(bytes, for: id, from: asked)
            case .failure(let trouble):
                self?.sampleFailed(trouble.message, for: id, from: asked)
            }
        }
    }

    /// Stop the noise and forget the request. Called by a second tap, by
    /// ``stop()``, and by nothing else.
    public func stopSample() {
        inFlight?.cancel()
        inFlight = nil
        fetchingSampleID = nil
        stopPlayback()
    }

    /// Silence, without touching the request in flight. The server-change
    /// sink's half of ``stopSample()``; see the initialiser for why the two
    /// halves are separate.
    public func stopPlayback() {
        playingVoiceID = nil
        sink.stop()
    }

    // MARK: - the two sides of the round trip

    /// The bytes landed. See ``playSample(_:)`` for why both guards are here.
    private func sampleArrived(_ bytes: Data, for id: String, from asked: String) {
        switch claim(id, from: asked) {
        case .superseded: return
        case .otherHouse: fetchingSampleID = nil; return
        case .ours: break
        }
        fetchingSampleID = nil
        do {
            try sink.play(bytes) { [weak self] in
                // Only if it is still this voice: a stop followed by another
                // play must not have its new state cleared by the old
                // player's last word.
                guard self?.playingVoiceID == id else { return }
                self?.playingVoiceID = nil
            }
            playingVoiceID = id
        } catch {
            playingVoiceID = nil
            sampleError = VoiceSettingsStrings.sampleFailed(
                (error as? DomovoiError)?.message ?? "\(error)"
            )
        }
    }

    private func sampleFailed(_ reason: String, for id: String, from asked: String) {
        switch claim(id, from: asked) {
        case .superseded: return
        // Not reported: a failure line about a house the person has left is a
        // second wrong answer, not a correction of the first.
        case .otherHouse: fetchingSampleID = nil; return
        case .ours: break
        }
        fetchingSampleID = nil
        playingVoiceID = nil
        sampleError = VoiceSettingsStrings.sampleFailed(reason)
    }

    /// Whose answer this is.
    private enum SampleClaim {
        /// The row still waiting, on the house it was asked of.
        case ours
        /// Another row is waiting now: a second tap replaced this request, so
        /// this answer owns none of the state and must touch none of it.
        case superseded
        /// The row still waiting, but the phone has moved to another house.
        /// The bytes are dropped; the waiting flag is cleared, because the
        /// request it was waiting for is over and nothing else is coming.
        case otherHouse
    }

    /// Is this answer still the answer to the question that was asked?
    ///
    /// Two comparisons, in this order: the **id** against the row still
    /// waiting, then the **server** against the address the request was sent
    /// to. The id first because a superseded request must not clear a newer
    /// one's state even when the house is also wrong.
    private func claim(_ id: String, from asked: String) -> SampleClaim {
        guard fetchingSampleID == id else { return .superseded }
        guard asked == container.store.serverURL else { return .otherHouse }
        return .ours
    }
}
