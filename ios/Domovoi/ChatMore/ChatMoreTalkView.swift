import DomovoiKit
import SwiftUI

/// Hold to talk: the button, the level ring, the hint, and the last exchange
/// underneath it.
///
/// `VoiceView` (`VoiceView.kt:501`). The whole turn goes to the house — the
/// recording is 16 kHz mono PCM16 in a multipart body, the core does the
/// speech-to-text and writes **both** rows itself (`phone_voice.py:632`), and
/// the thread is read back afterwards. There is no `Speech` import and no
/// `NSSpeechRecognitionUsageDescription`, which `project.yml:188` says must
/// never appear.
///
/// ### What is not, and why there is no control for it
///
/// * **No talk button at all without the microphone.** Android asks for
///   `RECORD_AUDIO` when the mic is tapped and refuses with
///   ``ChatMoreStrings/microphoneNeeded`` (`ChatScreen.kt:481`). Here the
///   permission is asked for as this screen opens and, if it is refused, the
///   button is not drawn — a 196-point button that can never record is the
///   control [[no-misleading-controls]] forbids.
/// * **No device actions.** A turn can come back with `play`, `pause`,
///   `next` and `volume` for *this phone's* player (`VoiceTurn.kt:217`); the
///   player is the Music item's model and an app cannot set the iPhone's
///   volume at all, so three of the seven could never work and the set is
///   left out whole. The reply still says what it said.
/// * **No dock gesture.** `dockExpanded`/`dockTakes` (`VoiceView.kt:137`) is a
///   workaround for Compose's stretch overscroll eating a nested scroll. This
///   screen shows the last exchange and a plain way back to the thread.
/// * **The reply is spoken in the server's own voice or not at all.** Android
///   speaks on the phone only when a downloaded Piper voice is this phone's
///   pick (`replyVoiceOnPhone`, `PhoneVoice.kt:148`); this app has no voice
///   catalogue and may not ship sherpa-onnx's prebuilts (GPL), so `speak` is
///   never sent as false and ``ChatMoreVoiceReply/playback`` collapses to
///   server audio or nothing — which is what Android does on a phone with no
///   voice downloaded.
struct ChatMoreTalkView: View {

    let chat: ChatModel
    let more: ChatMoreModel
    let close: () -> Void

    @State private var talker = ChatMoreTalker()
    /// nil until the permission has been asked for, so the button is drawn
    /// neither before the answer nor after a refusal.
    @State private var allowed: Bool?
    @State private var pressing = false
    /// Whether the press that is happening now began while Domovoi was
    /// speaking — ``ChatMoreVoiceRules/recorded(seconds:interrupted:)`` needs
    /// it, because a tap that only silenced a reply needs no advice.
    @State private var interrupted = false

    @ScaledMetric(relativeTo: .largeTitle) private var button: CGFloat = 196

    var body: some View {
        NavigationStack {
            VStack(spacing: 16) {
                Spacer(minLength: 0)
                talkButton
                Text(more.talkHint)
                    .font(.subheadline)
                    .foregroundStyle(Color.fgMuted)
                    .multilineTextAlignment(.center)
                    .fixedSize(horizontal: false, vertical: true)
                if let why = talker.trouble {
                    Text(why)
                        .font(.caption)
                        .foregroundStyle(Color.errInk)
                        .multilineTextAlignment(.center)
                        .fixedSize(horizontal: false, vertical: true)
                }
                Spacer(minLength: 0)
                exchange
            }
            .padding(16)
            .frame(maxWidth: .infinity, maxHeight: .infinity)
            .navigationTitle(ChatMoreStrings.talk)
            .navigationBarTitleDisplayMode(.inline)
            .toolbar {
                ToolbarItem(placement: .cancellationAction) {
                    Button(ChatMoreStrings.backToTheChat) {
                        talker.release()
                        close()
                    }
                }
            }
        }
        .task {
            if allowed == nil { allowed = await talker.mayRecord() }
        }
        // The ring, the clock and the end of the reply, on one timer.
        //
        // **One timer and no delegate**: `AVAudioPlayerDelegate`'s callback
        // arrives `nonisolated` and the hop back would be the only piece of
        // this screen Swift 6 could not check, and the ring needs a tick
        // anyway.
        .task {
            while !Task.isCancelled {
                talker.tick()
                if talker.recording, ChatMoreVoiceRules.atCap(seconds: talker.seconds) {
                    // The core refuses a longer clip with a 413, so a
                    // recorder that ran on would be recording a refusal
                    // (``ChatMoreVoiceRules/atCap(seconds:)``).
                    send()
                }
                if more.talkPhase == .speaking, !talker.speaking {
                    more.finishedSpeaking()
                }
                try? await Task.sleep(for: .milliseconds(100))
            }
        }
        .onChange(of: more.replyAudio) { _, wav in
            guard let wav else { return }
            talker.play(wav)
        }
        .onDisappear { talker.release() }
    }

    // MARK: - the button

    @ViewBuilder
    private var talkButton: some View {
        switch allowed {
        case .some(true):
            ring
        case .some(false):
            // No button, and the sentence that says why (`ChatScreen.kt:481`).
            Text(ChatMoreStrings.microphoneNeeded)
                .font(.subheadline)
                .foregroundStyle(Color.errInk)
                .multilineTextAlignment(.center)
                .fixedSize(horizontal: false, vertical: true)
        case nil:
            ProgressView()
                .frame(width: button, height: button)
                .accessibilityLabel(ChatMoreStrings.talk)
        }
    }

    /// The talk button with its level ring.
    ///
    /// A `DragGesture(minimumDistance: 0)` and not a `Button`: the gesture is
    /// a **hold**, and the three things that can end it are a release on the
    /// button, a release off it and the system taking the touch away —
    /// which is the one Android had a real defect over (`VoiceView.kt:120`:
    /// a cancelled touch read as neither, so the recording ran to its 30 s cap
    /// and was sent). `onEnded` covers the first two and `.onDisappear`
    /// covers the third, because a touch iOS takes away is a screen that is
    /// going.
    private var ring: some View {
        GeometryReader { geometry in
            ZStack {
                Circle()
                    .fill(Color.fgMuted.opacity(0.12))
                Circle()
                    .strokeBorder(Color.brandInk, lineWidth: 3 + 9 * talker.level)
                    .opacity(more.talkPhase == .cancelArmed ? 0.3 : 1)
                Image(systemName: more.talkPhase == .speaking ? "speaker.wave.2" : "mic")
                    .font(.largeTitle)
                    .foregroundStyle(Color.brandInk)
            }
            .contentShape(.circle)
            .gesture(
                DragGesture(minimumDistance: 0)
                    .onChanged { touch in
                        if !pressing { press() }
                        guard pressing else { return }
                        more.slideTalk(
                            off: ChatMoreVoiceRules.slidOff(
                                x: touch.location.x,
                                y: touch.location.y,
                                width: geometry.size.width,
                                height: geometry.size.height,
                                // The margin that forgives a thumb which
                                // wanders while talking (`VoiceView.kt:107`).
                                margin: 24
                            )
                        )
                    }
                    .onEnded { _ in
                        guard pressing else { return }
                        pressing = false
                        if more.talkPhase == .cancelArmed {
                            talker.cancel()
                            more.cancelTalk()
                        } else {
                            send()
                        }
                    }
            )
        }
        .frame(width: button, height: button)
        .accessibilityLabel(ChatMoreStrings.talk)
        .accessibilityHint(more.talkHint)
        .accessibilityAddTraits(.isButton)
    }

    private func press() {
        pressing = true
        interrupted = more.talkPhase == .speaking
        if interrupted { talker.stopPlaying() }
        // ``ChatMoreModel/pressTalk()`` is the decision — a press while a turn
        // is still being answered starts nothing and says so — and the
        // recorder is only started once it has said yes.
        guard more.pressTalk() else {
            pressing = false
            return
        }
        guard talker.start() else {
            more.cancelTalk()
            pressing = false
            return
        }
    }

    private func send() {
        pressing = false
        let pcm = talker.finish()
        more.talk(pcm: pcm, interrupted: interrupted)
        interrupted = false
    }

    // MARK: - the last exchange

    /// The last reply and what it answered, docked under the button
    /// (``ChatMoreVoiceRules/latestExchange(_:isUser:)``, `VoiceView.kt:205`).
    private var exchange: some View {
        let shown = ChatMoreVoiceRules.latestExchange(chat.transcript.messages) {
            $0.role == "user"
        }
        return VStack(alignment: .leading, spacing: 6) {
            ForEach(shown, id: \.ordinal) { message in
                VStack(alignment: .leading, spacing: 2) {
                    Text(message.content)
                        .font(message.role == "user" ? .callout : .body)
                        .foregroundStyle(message.role == "user" ? Color.fgMuted : Color.primary)
                        .fixedSize(horizontal: false, vertical: true)
                        .multilineTextAlignment(.leading)
                    if let id = message.id, let mark = more.speakerMark(for: id) {
                        ChatMoreSpeakerMarkView(mark: mark)
                    }
                }
                .frame(maxWidth: .infinity, alignment: .leading)
            }
        }
        .frame(maxWidth: .infinity, alignment: .leading)
    }
}
