import DomovoiKit
import SwiftUI

/// Settings → Household → Music mix (Up next 1.3.0; Android
/// `MusicMixPanel.kt`, 8230ccd2; the dashboard's MusicMixPanel,
/// `web/static/music_mix.jsx`): how Up next picks the songs that follow.
///
/// The household's mix and the two internet switches are an admin's
/// (wf/mix-gate) and this app holds no admin session, so they are said in
/// words with "An admin can change this." and a way to the dashboard — never a
/// slider the server would refuse (`PUT /api/music/mix` answers a phone 401).
/// What a phone CAN change is its own person's mix ("this is me"): that card
/// is here too, the same one the person's page shows
/// (``PersonMusicMixSection``), and never on a shared screen.
struct MusicMixPanel: View {

    let model: ShellModel

    @State private var household: HouseholdMixModel

    @Environment(\.openURL) private var openURL

    init(model: ShellModel) {
        self.model = model
        let store = model.container.store
        _household = State(initialValue: HouseholdMixModel(
            client: model.container.client,
            server: { store.serverURL },
            defaults: model.mixDefaults
        ))
    }

    private var server: String { model.container.store.serverURL }

    private var shared: Bool {
        _ = model.ownerRevision
        return model.owners.sharedScreen(for: server) == true
    }

    private var owner: DeviceOwner? {
        _ = model.ownerRevision
        return model.owners.deviceOwner(for: server)
    }

    var body: some View {
        VStack(alignment: .leading, spacing: 12) {
            muted(MusicMixStrings.intro, font: .subheadline)
            content
        }
        .frame(maxWidth: .infinity, alignment: .leading)
        .task(id: TaskKey(server: ServerCredentials.normalize(server), paired: model.isPaired)) {
            guard model.isPaired else { return }
            await household.read()
        }
    }

    private struct TaskKey: Equatable {
        let server: String
        let paired: Bool
    }

    @ViewBuilder
    private var content: some View {
        if !model.isPaired {
            muted(MusicMixStrings.notPaired, font: .subheadline)
        } else if let mix = household.mix {
            mixCard(mix)
            playingOnCard(mix)
            internetCard(mix)
            dashboardButton
            yourMix
        } else {
            muted(household.problem ?? MusicMixStrings.reading, font: .subheadline)
        }
    }

    // MARK: - the cards

    private func mixCard(_ mix: MusicMix) -> some View {
        let rights = MusicMixRules.rights()
        return PanelCard(title: MusicMixStrings.mixTitle, sub: MusicMixStrings.mixSub) {
            VStack(alignment: .leading, spacing: 10) {
                if !rights.household { muted(MusicMixStrings.adminCanChangeThis) }
                MixSaidRow(label: MusicMixStrings.discovery.label, value: MusicMixStrings.discovery.said(mix.discoveryFavorites))
                MixSaidRow(label: MusicMixStrings.wander.label, value: MusicMixStrings.wander.said(mix.closeWander))
            }
        }
    }

    private func playingOnCard(_ mix: MusicMix) -> some View {
        PanelCard(title: MusicMixStrings.playingOnTitle, sub: "") {
            VStack(alignment: .leading, spacing: 10) {
                MixSaidRow(
                    label: MusicMixStrings.autoplayLabel,
                    value: MusicMixStrings.onOff(mix.autoplay),
                    sub: MusicMixStrings.autoplaySub
                )
                muted(MusicMixStrings.phoneAutoplayNote(model.upNextAutoplay?.choice))
                MixSaidRow(
                    label: MusicMixStrings.noRepeatsLabel,
                    value: MusicMixStrings.noRepeatsValue(mix.listRepeatMin),
                    sub: MusicMixStrings.noRepeatsSub
                )
            }
        }
    }

    private func internetCard(_ mix: MusicMix) -> some View {
        let rights = MusicMixRules.rights()
        return PanelCard(title: MusicMixStrings.internetTitle, sub: MusicMixStrings.internetSub) {
            VStack(alignment: .leading, spacing: 10) {
                if !rights.internet { muted(MusicMixStrings.adminCanChangeThese) }
                MixSaidRow(
                    label: MusicMixStrings.similarArtistsLabel,
                    value: MusicMixStrings.onOff(mix.internet.similarArtists),
                    sub: MusicMixStrings.similarArtistsSub
                )
                MixSaidRow(
                    label: MusicMixStrings.popularityLabel,
                    value: MusicMixStrings.onOff(mix.internet.popularity),
                    sub: MusicMixStrings.popularitySub
                )
            }
        }
    }

    /// The way to the dashboard, where an admin changes what is read-only
    /// here. Hidden when no address can be built (no misleading controls).
    @ViewBuilder
    private var dashboardButton: some View {
        let rights = MusicMixRules.rights()
        let url = systemSettingsDashboardURL(serverURL: server, section: "music-mix").flatMap(URL.init(string:))
        if !rights.household || !rights.internet, let url {
            Button {
                openURL(url)
            } label: {
                Label(MusicMixStrings.openOnTheDashboard, systemImage: "arrow.up.forward.square")
            }
            .foregroundStyle(Color.brandInk)
            .frame(minHeight: 44)
        }
    }

    /// The one mix this phone may change: its own person's. Never on a
    /// shared screen, which is nobody's.
    @ViewBuilder
    private var yourMix: some View {
        if !shared, let me = owner {
            let name = me.name.trimmingCharacters(in: .whitespaces).isEmpty ? "you" : me.name
            PanelCard(title: MusicMixStrings.yourMix, sub: MusicMixStrings.yourMixSub(me.name)) {
                PersonMusicMixSection(model: model, personID: me.id, name: name, showHead: false)
            }
        }
    }

    private func muted(_ text: String, font: Font = .footnote) -> some View {
        Text(text)
            .font(font)
            .foregroundStyle(Color.fgMuted)
            .fixedSize(horizontal: false, vertical: true)
    }
}

/// A setting said in words: its name, its value, a quiet line under it. One
/// VoiceOver stop.
struct MixSaidRow: View {

    let label: String
    let value: String
    var sub: String?

    var body: some View {
        VStack(alignment: .leading, spacing: 2) {
            HStack(alignment: .firstTextBaseline, spacing: 8) {
                Text(label)
                    .font(.subheadline.weight(.semibold))
                    .fixedSize(horizontal: false, vertical: true)
                Spacer(minLength: 8)
                Text(value)
                    .font(.subheadline)
                    .multilineTextAlignment(.trailing)
                    .fixedSize(horizontal: false, vertical: true)
            }
            if let sub {
                Text(sub)
                    .font(.footnote)
                    .foregroundStyle(Color.fgMuted)
                    .fixedSize(horizontal: false, vertical: true)
            }
        }
        .accessibilityElement(children: .combine)
    }
}

/// A person's own music mix (Android `PersonMusicMixSection`; the dashboard's
/// PersonMusicMix): "Use the household’s mix", and when off, their two
/// five-step sliders. Changeable only from the phone that is theirs
/// (``MusicMixRules/editable(personID:owner:shared:admin:)``); anywhere else
/// it is said in words with who can change it. A refusal (the phone stopped
/// being theirs meanwhile) is said in the server's words. Nothing on a shared
/// screen or an unpaired phone.
struct PersonMusicMixSection: View {

    let model: ShellModel
    let personID: Int
    let name: String
    var showHead = true

    @State private var mix: PersonMixModel

    init(model: ShellModel, personID: Int, name: String, showHead: Bool = true) {
        self.model = model
        self.personID = personID
        self.name = name
        self.showHead = showHead
        let store = model.container.store
        _mix = State(initialValue: PersonMixModel(
            client: model.container.client,
            server: { store.serverURL },
            deviceID: { [weak model] in model?.deviceID },
            say: { [weak model] text in model?.showToast(text) }
        ))
    }

    private var server: String { model.container.store.serverURL }

    private var shared: Bool {
        _ = model.ownerRevision
        return model.owners.sharedScreen(for: server) == true
    }

    private var editable: Bool {
        _ = model.ownerRevision
        return MusicMixRules.editable(personID: personID, owner: model.owners.deviceOwner(for: server), shared: shared)
    }

    var body: some View {
        if !shared, model.isPaired {
            section
                .task(id: TaskKey(server: ServerCredentials.normalize(server), person: personID)) {
                    mix.show(personID)
                    await mix.read()
                }
        }
    }

    private struct TaskKey: Equatable {
        let server: String
        let person: Int
    }

    private var section: some View {
        VStack(alignment: .leading, spacing: 8) {
            if showHead {
                Text(MusicMixStrings.personHead)
                    .font(.headline)
                    .accessibilityAddTraits(.isHeader)
            }
            if let pm = mix.mix {
                if editable { own(pm) } else { words(pm) }
            } else {
                note(mix.problem ?? MusicMixStrings.reading)
            }
            if let refusal = mix.refusal {
                Text(refusal)
                    .font(.footnote)
                    .foregroundStyle(Color.errInk)
                    .fixedSize(horizontal: false, vertical: true)
            }
        }
        .frame(maxWidth: .infinity, alignment: .leading)
    }

    /// On their own phone: the switch, and their sliders when it is off.
    @ViewBuilder
    private func own(_ pm: PersonMix) -> some View {
        Toggle(isOn: Binding(
            get: { !pm.ownMix },
            set: { on in useHousehold(on) }
        )) {
            Text(MusicMixStrings.useHouseholds)
                .font(.subheadline)
                .fixedSize(horizontal: false, vertical: true)
        }
        .disabled(mix.busy)
        .frame(minHeight: 44)
        .accessibilityLabel(MusicMixStrings.useHouseholdsFor(name))
        if pm.ownMix {
            MixStepSlider(spec: MusicMixStrings.discovery, value: pm.uses.discoveryFavorites, enabled: !mix.busy) { step in
                setStep(discovery: true, step)
            }
            MixStepSlider(spec: MusicMixStrings.wander, value: pm.uses.closeWander, enabled: !mix.busy) { step in
                setStep(discovery: false, step)
            }
            note(MusicMixStrings.usedWhen(name))
        }
    }

    // Every write is the model's, which keys its answer by the server and
    // the person it was for; the view only starts it.
    private func useHousehold(_ on: Bool) {
        let mix = self.mix
        let name = self.name
        Task { @MainActor in await mix.useHousehold(on, name: name) }
    }

    private func setStep(discovery: Bool, _ step: Int) {
        let mix = self.mix
        let name = self.name
        Task { @MainActor in await mix.setStep(discovery: discovery, step: step, name: name) }
    }

    /// Anywhere else: said in words, with who can change it.
    @ViewBuilder
    private func words(_ pm: PersonMix) -> some View {
        Text(pm.ownMix ? MusicMixStrings.hasOwn(name) : MusicMixStrings.usesHouseholds(name))
            .font(.subheadline)
            .fixedSize(horizontal: false, vertical: true)
        if pm.ownMix {
            MixSaidRow(label: MusicMixStrings.discovery.label, value: MusicMixStrings.discovery.said(pm.uses.discoveryFavorites))
            MixSaidRow(label: MusicMixStrings.wander.label, value: MusicMixStrings.wander.said(pm.uses.closeWander))
        }
        note(MusicMixStrings.readOnlyNote(name))
    }

    private func note(_ text: String) -> some View {
        Text(text)
            .font(.footnote)
            .foregroundStyle(Color.fgMuted)
            .fixedSize(horizontal: false, vertical: true)
    }
}

/// A five-step slider with its two ends named and the step said in words.
/// The step goes to the server when the thumb is let go, once.
struct MixStepSlider: View {

    let spec: MixSliderSpec
    let value: Int?
    let enabled: Bool
    let onChange: (Int) -> Void

    @State private var at: Double = Double(MusicMixRules.middle)

    private var saved: Int { value ?? MusicMixRules.middle }
    private var step: Int { min(5, max(1, Int(at.rounded()))) }

    var body: some View {
        VStack(alignment: .leading, spacing: 2) {
            Text(spec.label)
                .font(.caption)
                .foregroundStyle(Color.fgMuted)
            slider
            ends
            Text(spec.said(step))
                .font(.footnote)
                .fixedSize(horizontal: false, vertical: true)
        }
        .onAppear { at = Double(saved) }
        .onChange(of: saved) { _, now in at = Double(now) }
    }

    private var slider: some View {
        Slider(value: $at, in: 1...5, step: 1) { editing in
            if !editing { commit() }
        }
        .disabled(!enabled)
        .accessibilityLabel(MusicMixStrings.sliderName(spec))
        .accessibilityValue(spec.said(step))
        // VoiceOver's swipe up and down move one step and save it, as a
        // drag let go does.
        .accessibilityAdjustableAction { direction in
            switch direction {
            case .increment: at = Double(min(5, step + 1))
            case .decrement: at = Double(max(1, step - 1))
            @unknown default: return
            }
            commit()
        }
    }

    private func commit() {
        if step != saved { onChange(step) }
    }

    private var ends: some View {
        HStack {
            Text(spec.left)
            Spacer(minLength: 8)
            Text(spec.right)
        }
        .font(.caption2)
        .foregroundStyle(Color.fgMuted)
        .accessibilityHidden(true)
    }
}
