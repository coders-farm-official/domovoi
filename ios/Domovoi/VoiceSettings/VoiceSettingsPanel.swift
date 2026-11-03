import DomovoiKit
import SwiftUI

/// Settings → Voices: what voice the house speaks with, the three voices it
/// can speak with, how it says names, and one sample.
///
/// A port of `android/.../ui/screens/settings/VoicePanel.kt` — **and it is a
/// different screen from Android's, not a smaller one.** Android's `VoiceCard`
/// (`:117-259`) is about a Piper voice downloaded onto the *phone*, so the
/// phone can read replies out loud on its own: a picker, a download with
/// progress, a delete, a mobile-data switch, a local clip. This phone speaks
/// with nothing — there is no phonemizer
/// (`ios/Packages/DomovoiPhonemizer/` is empty on purpose, and its README
/// calls the port its own milestone), no ONNX Runtime, no model store and
/// nothing anywhere in `ios/` that produces speech — so the subject here is
/// the **server's** voice settings, read and said plainly, with one sample the
/// server renders.
///
/// The map is `design-notes/ios/overnight/voicesettings/MAP.md`; the wording
/// changes are in `IOS-ONLY-STRINGS.md` with reasons, and the calls are in
/// `DECISIONS.md`.
///
/// ### What is here
///
/// `GET /api/voice` (`web/backend/api/voice.py:135-140`, device tier) and
/// `GET /api/voice/samples` (`:147-154`), as four cards: the house's voice,
/// the three voices, the "say it like" list, and one paragraph saying this
/// phone does not speak yet — so the section is not *silently* thinner than
/// Android's.
///
/// ## What is not, and why there is no control for it
///
/// The voice **picker** (`VoicePanel.kt:161`): a pick only means something if
/// something speaks, and it would set a value nothing reads.
/// **download / pause download** (`:204-207`): there is nothing to download
/// into. **delete**, `delete <Name>` and its confirm dialog (`:213`,
/// `:236-242`, `:250-259`): nothing is installed. **allow mobile data**
/// (`:217-228`): it gates a download that cannot happen. The
/// **ready / NN% / not downloaded pill** (`:172-179`) and the
/// **"Also on this phone" list** (`:229-243`): all of them describe a local
/// file. The **download size** (`:181`): `size_bytes` is the size of a
/// download this phone will not make, and a megabyte figure beside a voice
/// reads as an offer — the sample rate and "Piper" stay, as a fact about the
/// server's voice. The **`POST_NOTIFICATIONS` prompt** (`:137-151`): Android
/// only, and only for the download's notification. The **"say it like"
/// preview** (`voice.py:278`): the list is read-only here, so there is no
/// unsaved entry to preview. And **a sample of a voice the server cannot
/// speak**: see ``VoiceSettingsSamples/isPlayable(_:)``.
///
/// Every write behind this screen is `require_admin_mutation`
/// (`voice.py:195`, `:218`, `:239`, `:264`) and a phone holds no admin
/// session, so there is nothing on this screen that changes anything — which
/// is exactly what Android's three sibling sections say out loud through
/// ``VoiceSettingsDashboardPanel``.
struct VoiceSettingsPanel: View {

    let model: ShellModel

    /// Built in `.task` and not in `init`, for ``FilesView``'s reason: the
    /// model needs the container and a `@State` initialiser runs before the
    /// view has one. The player is built with it, because it is the model's
    /// sink and has no life of its own.
    @State private var voice: VoiceSettingsModel?

    var body: some View {
        VStack(alignment: .leading, spacing: 16) {
            header
            if let voice {
                if let info = voice.info.data {
                    // A read that answered and then failed on a later refresh
                    // keeps its data and says so (``ApiRead/isStale``): the
                    // numbers on screen are still true, they are just not
                    // fresh, and blanking them would lose more than it says.
                    if let trouble = voice.info.error {
                        Text(VoiceSettingsStrings.voiceReadFailed(trouble))
                            .font(.footnote)
                            .foregroundStyle(Color.warnInk)
                            .fixedSize(horizontal: false, vertical: true)
                            .accessibilityIdentifier("voicesettings-read-stale")
                    }
                    houseVoiceCard(info)
                    voicesCard(voice, info)
                    namesCard(info)
                } else if let trouble = voice.info.error {
                    // Said, not drawn as an empty card. An empty card is the
                    // shape that let four of Home's sections go unaudited for
                    // a week: a read that never decoded looked like a house
                    // with nothing in it.
                    Text(VoiceSettingsStrings.voiceReadFailed(trouble))
                        .font(.footnote)
                        .foregroundStyle(Color.errInk)
                        .fixedSize(horizontal: false, vertical: true)
                        .accessibilityIdentifier("voicesettings-read-failed")
                } else {
                    Text(VoiceSettingsStrings.checking)
                        .font(.footnote)
                        .foregroundStyle(Color.fgMuted)
                }
            }
            notOnThisPhoneCard
        }
        .frame(maxWidth: .infinity, alignment: .leading)
        .task {
            if voice == nil {
                voice = VoiceSettingsModel(
                    in: model.container,
                    sink: VoiceSettingsSamplePlayer()
                )
            }
            voice?.refresh()
        }
        .onDisappear {
            // ``VoiceSettingsModel/stopSample()`` and not `stop()`: a sample
            // can be a 120-second request (`voice.py:185`) and noise that
            // outlives its screen is the worst of the two, so both go. The
            // two reads stay joined to the server key, because `.task`
            // re-runs on a re-appear and a read that was taken off the
            // registry would come back unkeyed — ``ApiRead/refresh()``'s own
            // note about `@State` surviving a disappearance.
            voice?.stopSample()
        }
    }

    /// Android's `PanelCard("Voice", …)` (`VoicePanel.kt:154-158`) wraps the
    /// whole thing over there; here it is the header above the cards, because
    /// what it wraps is four cards rather than one.
    ///
    /// The sub's last sentence is Android's word for word, and it is the one
    /// a person actually needs: a room's voice is changed by saying so in the
    /// room, not on this screen.
    private var header: some View {
        VStack(alignment: .leading, spacing: 6) {
            Text(VoiceSettingsStrings.voiceTitle)
                .font(.headline)
                .fixedSize(horizontal: false, vertical: true)
            Text(VoiceSettingsStrings.voiceSub)
                .font(.footnote)
                .foregroundStyle(Color.fgMuted)
                .fixedSize(horizontal: false, vertical: true)
        }
    }

    // MARK: - the house's voice

    /// What the house speaks with — **by name when this app lists it, and by
    /// the raw id when it does not.**
    ///
    /// `server_voice` is non-optional on the route but is *not* necessarily
    /// one of the three: when the registry's default is not a shipped voice,
    /// `server_voice()` returns `settings.tts_piper_voice`, which can be any
    /// voice id at all (`domovoi/voice_settings.py:143-152`). Android
    /// substitutes its own default here (`VoicePanel.kt:91`) because the
    /// *phone* has to speak with something; this phone speaks with nothing,
    /// so naming what the server actually said is the only honest answer and
    /// "Kristin" for a house that speaks with something else would be a false
    /// reading rather than a fallback.
    private func houseVoiceCard(_ info: VoiceSettingsInfo) -> some View {
        PanelCard(
            title: VoiceSettingsStrings.houseVoiceTitle,
            sub: VoiceSettingsStrings.houseVoiceSub
        ) {
            VStack(alignment: .leading, spacing: 4) {
                if let listed = info.houseVoice {
                    Text(VoiceSettingsNaming.label(for: listed))
                        .font(.body)
                        .fixedSize(horizontal: false, vertical: true)
                        .accessibilityIdentifier("voicesettings-house-voice")
                    Text(VoiceSettingsStrings.engineAndRate(sampleRate: listed.sampleRate))
                        .font(.caption)
                        .foregroundStyle(Color.fgMuted)
                        .fixedSize(horizontal: false, vertical: true)
                } else {
                    // `warnInk`, not `errInk`: this is a true answer about an
                    // unusual house, not a failure. And never `.orange`,
                    // which measures 2.20:1 on white.
                    Text(VoiceSettingsStrings.notOneOfTheThree(info.serverVoice))
                        .font(.footnote)
                        .foregroundStyle(Color.warnInk)
                        .fixedSize(horizontal: false, vertical: true)
                        .accessibilityIdentifier("voicesettings-house-voice")
                }
                if let changed = parseInstant(info.updatedAt) {
                    Text(
                        "\(VoiceSettingsStrings.lastChanged) "
                            + changed.formatted(date: .abbreviated, time: .shortened)
                    )
                    .font(.caption)
                    .foregroundStyle(Color.fgMuted)
                    .fixedSize(horizontal: false, vertical: true)
                }
            }
        }
    }

    // MARK: - the three voices

    private func voicesCard(
        _ voice: VoiceSettingsModel,
        _ info: VoiceSettingsInfo
    ) -> some View {
        PanelCard(
            title: VoiceSettingsStrings.voicesTitle,
            sub: VoiceSettingsStrings.voicesSub
        ) {
            VStack(alignment: .leading, spacing: 10) {
                // Not a `LazyVStack`: three rows, and a lazy container cost
                // Home six `Dynamic Type font sizes are partially
                // unsupported` findings for nothing
                // (``HomeEverythingView``'s note). A lazy row must not hold
                // the sample's `Task` either, and here it cannot — the model
                // owns it.
                ForEach(info.voices) { listed in
                    VoiceSettingsVoiceRow(
                        listed: listed,
                        canPlay: voice.canPlay(listed.id),
                        isBusy: voice.playingVoiceID == listed.id
                            || voice.fetchingSampleID == listed.id,
                        whyNot: voice.whyNoSample(listed.id),
                        onTap: { voice.tappedSample(listed.id) }
                    )
                }
                if let trouble = voice.sampleError {
                    Text(trouble)
                        .font(.footnote)
                        .foregroundStyle(Color.errInk)
                        .fixedSize(horizontal: false, vertical: true)
                        .accessibilityIdentifier("voicesettings-sample-failed")
                }
            }
        }
    }

    // MARK: - how names are said

    /// The server's **effective** list. An empty one is a real answer — the
    /// owner emptied it — and says so, rather than reading as a read that
    /// failed. Android cannot reach that wording: an empty list there falls
    /// into `pronunciationNote`'s `else` branch and claims the built-in list
    /// is in force (`VoicePanel.kt:100-107`), which is true of the phone's
    /// own converter and not of the server.
    private func namesCard(_ info: VoiceSettingsInfo) -> some View {
        let entries = info.entries
        return PanelCard(
            title: VoiceSettingsStrings.namesTitle,
            sub: entries.isEmpty
                ? VoiceSettingsStrings.noNamesList
                : VoiceSettingsStrings.namesSaidByTheServer(count: entries.count)
        ) {
            VStack(alignment: .leading, spacing: 4) {
                ForEach(entries) { entry in
                    Text(VoiceSettingsStrings.saidAs(word: entry.word, like: entry.sayLike))
                        .font(.footnote)
                        .fixedSize(horizontal: false, vertical: true)
                        .accessibilityIdentifier("voicesettings-said-as-\(entry.word)")
                }
            }
        }
    }

    // MARK: - what this phone does not do

    /// The card that exists so the difference from Android is **told, not
    /// discovered**. Entirely iOS-only, and the whole of it is in
    /// `IOS-ONLY-STRINGS.md`.
    private var notOnThisPhoneCard: some View {
        PanelCard(
            title: VoiceSettingsStrings.notOnThisPhoneTitle,
            sub: VoiceSettingsStrings.notOnThisPhoneBody
        ) {
            EmptyView()
        }
    }
}

/// One voice: its name and sex, the engine line, and a play button **only
/// where the server says it can really speak that voice**.
///
/// ``VoiceSettingsModel/canPlay(_:)`` is the whole of the rule and this row
/// does not second-guess it: the samples read must have answered, the server
/// must have said `"voice"` for this id, and the info read must list it.
/// Where it says no there is a quiet line instead of a disabled button,
/// because a disabled control still says the app could do the thing.
private struct VoiceSettingsVoiceRow: View {

    let listed: VoiceSettingsVoice
    let canPlay: Bool
    /// Fetching or playing — the button says `stop` for both, as Android's
    /// one button does (`VoicePanel.kt:210`).
    let isBusy: Bool
    let whyNot: String?
    let onTap: () -> Void

    @Environment(\.dynamicTypeSize) private var typeSize

    var body: some View {
        // A row at the ordinary sizes, a column at the accessibility ones:
        // the name, the engine line and a button do not fit side by side at
        // AX5, and `minimumScaleFactor` — the other answer — is rejected
        // throughout this app, because it makes text smaller for the person
        // who asked for bigger.
        if typeSize.isAccessibilitySize {
            VStack(alignment: .leading, spacing: 6) {
                facts
                control
            }
            .frame(maxWidth: .infinity, alignment: .leading)
        } else {
            HStack(alignment: .firstTextBaseline, spacing: 10) {
                facts
                Spacer(minLength: 8)
                control
            }
        }
    }

    @ViewBuilder private var facts: some View {
        VStack(alignment: .leading, spacing: 2) {
            Text(VoiceSettingsNaming.label(for: listed))
                .font(.body)
                .fixedSize(horizontal: false, vertical: true)
                .accessibilityIdentifier("voicesettings-voice-\(listed.id)")
            Text(VoiceSettingsStrings.engineAndRate(sampleRate: listed.sampleRate))
                .font(.caption)
                .foregroundStyle(Color.fgMuted)
                .fixedSize(horizontal: false, vertical: true)
        }
    }

    @ViewBuilder private var control: some View {
        if canPlay {
            Button(isBusy ? VoiceSettingsStrings.stop : VoiceSettingsStrings.playASample) {
                onTap()
            }
            .font(.footnote)
            .fixedSize(horizontal: false, vertical: true)
            // Apple's 44 points. The label is short at every type size, so
            // the height is what needs stating; `contentShape` makes the
            // whole of it tappable rather than just the glyphs.
            .frame(minHeight: 44)
            .contentShape(.rect)
            // **The label stays the ported words and the identifier carries
            // the voice.** Three rows could each have a button reading `play
            // a sample`, and a query on that label alone takes whichever one
            // the tree happens to hand back first — the `firstMatch` trap
            // this track has paid for twice. A test that must prove the
            // *right* row's button was tapped asks for it by id.
            .accessibilityIdentifier("voicesettings-play-\(listed.id)")
        } else if let whyNot {
            Text(whyNot)
                .font(.caption)
                .foregroundStyle(Color.fgMuted)
                .fixedSize(horizontal: false, vertical: true)
                .accessibilityIdentifier("voicesettings-no-sample-\(listed.id)")
        }
        // The samples read not having answered yet draws neither: there is
        // nothing true to say about a sample before the server has been
        // asked, and a button that appeared a moment later is better than one
        // that was wrong for a moment.
    }
}
