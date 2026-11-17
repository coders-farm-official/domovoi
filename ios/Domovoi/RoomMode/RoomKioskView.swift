import Combine
import DomovoiKit
import SwiftUI
import UIKit

/// The room screen (`roommode/RoomModeActivity.kt` and `RoomDisplay.kt`,
/// ROOMMODE-MAP §3.4): dark and plain so the phone reads as a room, not as
/// the app — the room's name, a big clock with the day and date, the voice
/// state, and, when the server tells the room about them, this room's music
/// with its own controls, its timers and next alarm, and a big Stop while one
/// goes off. Below them what the room always had: the status line, the
/// approval code, Mute, Exit (held, then the phone's own unlock), the mic
/// chip and the Guided Access tip. The content drifts a few points every
/// minute against burn-in, dims late at night, and after a minute with
/// nothing to show the screen dims itself.
///
/// Built before any household object (the app's scene switch), and it is
/// what starts and stops the room with the scene: iOS opens a microphone
/// only for the app on screen (§3.1), so the screen stays on while the room
/// runs, on battery too: a locked screen would be a room that stopped
/// listening. Night dimming, the idle dimming and the slow redraw are what
/// save the battery.
struct RoomKioskScreen: View {
    let scene: RoomModeScene
    @StateObject private var model: RoomKioskModel
    @Environment(\.scenePhase) private var scenePhase
    @State private var guidedAccessSheet = false
    #if DEBUG
    @StateObject private var perf = RoomPerfProbe()
    #endif

    init(scene: RoomModeScene) {
        self.scene = scene
        let store = scene.store
        let feed = scene.launch.feed
        _model = StateObject(wrappedValue: RoomKioskModel(
            store: store,
            makeEngine: { RoomModeRuntime.makeEngine(store: store, feed: feed) },
            lock: RoomModeRuntime.lockFacts(fixed: scene.launch.fixedLock),
            screen: RoomScreen(),
            notices: RoomNotices(),
            micGranted: { feed || RoomModeRuntime.micGranted },
            onLeave: { _ in scene.left() }
        ))
    }

    var body: some View {
        RoomKioskView(model: model, onGuidedAccess: { guidedAccessSheet = true }) {
            #if DEBUG
            Text(perf.line)
                .font(.caption2.monospacedDigit())
                .foregroundStyle(RoomInk.dim)
            #else
            EmptyView()
            #endif
        }
        .statusBarHidden()
        .persistentSystemOverlays(.hidden)
        .preferredColorScheme(.dark)
        .onChange(of: scenePhase, initial: true) { _, phase in
            switch phase {
            case .active: model.enteredForeground()
            case .background: model.enteredBackground()
            default: break   // Control Centre, a banner: nothing (§3.1)
            }
        }
        .onAppear {
            model.guidedAccessChanged(UIAccessibility.isGuidedAccessEnabled)
            model.thermalChanged(RoomThermal(ProcessInfo.processInfo.thermalState))
        }
        .onReceive(
            NotificationCenter.default.publisher(for: ProcessInfo.thermalStateDidChangeNotification)
                .receive(on: DispatchQueue.main)
        ) { _ in
            model.thermalChanged(RoomThermal(ProcessInfo.processInfo.thermalState))
        }
        .onReceive(
            NotificationCenter.default.publisher(for: UIAccessibility.guidedAccessStatusDidChangeNotification)
                .receive(on: DispatchQueue.main)
        ) { _ in
            model.guidedAccessChanged(UIAccessibility.isGuidedAccessEnabled)
        }
        .task {
            // Every five seconds: the dimming, the drift, the heat's return
            // and the interruption notice. Nothing on screen changes unless
            // one of them does.
            while !Task.isCancelled {
                try? await Task.sleep(nanoseconds: 5_000_000_000)
                model.tick()
                #if DEBUG
                perf.sample()
                #endif
            }
        }
        .sheet(isPresented: $guidedAccessSheet) { RoomGuidedAccessSheet() }
    }
}

/// Android's room inks (`RoomDisplay.kt`, `DomovoiDarkColors`) on black.
enum RoomInk {
    static let text1 = Color(red: 0xE9 / 255, green: 0xE5 / 255, blue: 0xDE / 255)
    static let text2 = Color(red: 0x8C / 255, green: 0x87 / 255, blue: 0x7F / 255)
    static let dim = Color(red: 0x3A / 255, green: 0x36 / 255, blue: 0x32 / 255)
    static let amber = Color(red: 0.9758, green: 0.6765, blue: 0.1471)
    static let red = Color(red: 1.0, green: 0.3718, blue: 0.3577)
    static let card = Color(red: 0x1B / 255, green: 0x19 / 255, blue: 0x16 / 255)
    static let raised = Color(red: 0x22 / 255, green: 0x1F / 255, blue: 0x1C / 255)
    static let border = Color(red: 0x2F / 255, green: 0x2D / 255, blue: 0x2A / 255)
}

struct RoomKioskView<Footer: View>: View {
    @ObservedObject var model: RoomKioskModel
    let onGuidedAccess: () -> Void
    /// Added to the real time: zero in the app, a pinned clock in the DEBUG
    /// preview (``RoomDisplayPreviewScreen``).
    let clockOffset: TimeInterval
    let footer: Footer

    init(
        model: RoomKioskModel,
        clockOffset: TimeInterval = 0,
        onGuidedAccess: @escaping () -> Void,
        @ViewBuilder footer: () -> Footer
    ) {
        self.model = model
        self.clockOffset = clockOffset
        self.onGuidedAccess = onGuidedAccess
        self.footer = footer()
    }

    /// Something counts down or moves on: a redraw every second, else on the
    /// minute.
    private var ticks: Bool {
        guard let f = model.feed else { return false }
        return f.ringing != nil || !f.timers.isEmpty || f.nowPlaying != nil
    }

    var body: some View {
        GeometryReader { proxy in
            TimelineView(RoomTickSchedule(everySecond: ticks)) { context in
                let locale = Locale.autoupdatingCurrent
                let s = RoomDisplayModel.build(RoomDisplayModel.Input(
                    status: model.status,
                    running: model.running && model.engineUp,
                    feed: model.feed,
                    volume: model.volume,
                    now: context.date.addingTimeInterval(clockOffset),
                    elapsedMS: RoomClock.monotonicMS(),
                    timeZone: .autoupdatingCurrent,
                    locale: locale,
                    use24h: RoomDisplayModel.uses24HourClock(locale),
                    width: proxy.size.width,
                    height: proxy.size.height
                ))
                display(s)
                    .onChange(of: s.voiceLabel) { _, label in
                        RoomAnnouncer.polite(RoomModeStrings.stateDescription(label))
                    }
                    .onChange(of: s.ringing?.fireID, initial: true) { _, fire in
                        if fire != nil, let r = s.ringing { RoomAnnouncer.assertive(r.announcement) }
                    }
            }
        }
        .background(Color.black.ignoresSafeArea())
        .contentShape(Rectangle())
        .simultaneousGesture(TapGesture().onEnded { model.touched() })
        .alert(RoomModeStrings.exitTitle, isPresented: presenting(.confirmNoLock)) {
            Button(RoomModeStrings.exitConfirm) { model.confirmExit() }
            Button(RoomModeStrings.stayARoom, role: .cancel) { model.dismissDialog() }
        } message: {
            Text(RoomStatusText.confirmNoLock)
        }
        .alert(RoomModeStrings.setALockExitTitle, isPresented: presenting(.setALock)) {
            Button(RoomModeStrings.okay, role: .cancel) { model.dismissDialog() }
        } message: {
            Text(RoomStatusText.setALockToExit)
        }
        .alert(RoomModeStrings.leaveTitle, isPresented: presenting(.keepOrForget)) {
            Button(RoomModeStrings.keepThisRoom) { model.leave(forget: false) }
            Button(RoomModeStrings.forgetThisRoom, role: .destructive) { model.leave(forget: true) }
            Button(RoomModeStrings.stayARoom, role: .cancel) { model.dismissDialog() }
        } message: {
            Text(RoomStatusText.keepOrForget)
        }
    }

    private func presenting(_ dialog: RoomKioskModel.Dialog) -> Binding<Bool> {
        Binding(
            get: { model.dialog == dialog },
            set: { if !$0, model.dialog == dialog { model.dismissDialog() } }
        )
    }

    private func display(_ s: RoomDisplayModel.State) -> some View {
        let palette = RoomPalette(night: s.night)
        return Group {
            switch s.layout {
            case .portrait: portrait(s, palette)
            case .landscape: landscape(s, palette)
            }
        }
        .padding(s.compact ? 16 : 24)
        .offset(x: model.drift.x, y: model.drift.y)
    }

    /// One column; the big button and Exit stay at the foot of the screen
    /// whatever scrolls above them, and nothing is cut off at the bar: the
    /// scroll area fades out above it and ends with room to scroll clear.
    private func portrait(_ s: RoomDisplayModel.State, _ c: RoomPalette) -> some View {
        let gap: CGFloat = s.compact ? 12 : 20
        // With cards to show, the clock and the ring step down so the music
        // and the timers fit on a phone too.
        let small = s.compact || s.hasCards
        return VStack(spacing: 0) {
            RoomFadingScroll {
                topRow(s, c)
                if let r = s.ringing {
                    ringingCard(r, s, c).padding(.top, gap)
                }
                RoomClockView(state: s, palette: c, size: small ? 72 : 96).padding(.top, gap)
                voiceState(s, c, ringSize: small ? 56 : 72).padding(.top, gap)
                statusBlock(s, c)
                if s.hasCards {
                    cards(s, c, gap: gap, withRinging: false).padding(.top, gap)
                }
                extras(c, gap: gap)
            }
            actionBar(s, c).padding(.top, gap / 2)
        }
    }

    /// Two columns: the clock's, with the big button and Exit under it, and
    /// the cards', which has the full height and scrolls (fading at its foot,
    /// never cut) when there is more than fits. With no cards the clock's
    /// column has the screen.
    private func landscape(_ s: RoomDisplayModel.State, _ c: RoomPalette) -> some View {
        let gap: CGFloat = s.compact ? 12 : 20
        return VStack(spacing: 0) {
            topRow(s, c)
            HStack(spacing: 32) {
                RoomFadingScroll(centered: true) {
                    RoomClockView(state: s, palette: c, size: s.compact ? 72 : 120)
                    voiceState(s, c, ringSize: s.compact ? 56 : 80).padding(.top, gap)
                    statusBlock(s, c)
                    actionBar(s, c).padding(.top, gap)
                    extras(c, gap: gap)
                }
                if s.hasCards {
                    RoomFadingScroll(centered: true) {
                        cards(s, c, gap: gap, withRinging: true)
                    }
                }
            }
            .padding(.top, gap)
        }
    }

    private func topRow(_ s: RoomDisplayModel.State, _ c: RoomPalette) -> some View {
        RoomTopRow(
            label: model.label, compact: s.compact, palette: c,
            micOpen: model.micOpen, micLive: model.micLive, muted: model.muted
        )
    }

    private func voiceState(_ s: RoomDisplayModel.State, _ c: RoomPalette, ringSize: CGFloat) -> some View {
        RoomVoiceState(
            ring: s.ring, label: s.voiceLabel, animates: model.animates,
            ringSize: ringSize, compact: s.compact, palette: c
        )
    }

    private func ringingCard(_ r: RoomDisplayModel.Ringing, _ s: RoomDisplayModel.State, _ c: RoomPalette) -> some View {
        RoomRingingCard(ringing: r, compact: s.compact, palette: c) {
            model.control(.stopRinging(fireID: r.fireID))
        }
    }

    /// The ringing card (when asked), then the timers and the music: the
    /// timers first while one is nearly done (``RoomDisplayModel/State/timersFirst``).
    private func cards(_ s: RoomDisplayModel.State, _ c: RoomPalette, gap: CGFloat, withRinging: Bool) -> some View {
        let hasTimers = !s.timers.isEmpty || s.nextAlarm != nil
        return VStack(spacing: gap) {
            if withRinging, let r = s.ringing { ringingCard(r, s, c) }
            if s.timersFirst && hasTimers { RoomTimersCard(state: s, palette: c) }
            if let np = s.nowPlaying {
                RoomNowPlayingCard(
                    nowPlaying: np, compact: s.compact, palette: c,
                    loadArt: { path in await model.art(path) },
                    onControl: { action in model.control(action) },
                    onVolume: { direction in model.stepVolume(direction) }
                )
            }
            if !s.timersFirst && hasTimers { RoomTimersCard(state: s, palette: c) }
        }
    }

    /// What the room always said under the ring: the status line and, while
    /// it waits for approval, the code and "check now".
    @ViewBuilder
    private func statusBlock(_ s: RoomDisplayModel.State, _ c: RoomPalette) -> some View {
        if let detail = model.detail {
            Text(detail)
                .font(.system(size: s.compact ? 15 : 17))
                .foregroundStyle(c.text2)
                .multilineTextAlignment(.center)
                .fixedSize(horizontal: false, vertical: true)
                .padding(.horizontal, 8)
                .padding(.top, 12)
                .accessibilityIdentifier("room-detail")
        }
        if let code = model.approvalCode {
            Text(RoomModeStrings.approvalCodeShown(code))
                .font(.system(size: s.compact ? 40 : 48, weight: .semibold).monospacedDigit())
                .foregroundStyle(c.amber)
                .padding(.top, 12)
                .accessibilityLabel(RoomModeStrings.approvalCodeDescription(code))
                .accessibilityIdentifier("room-approval-code")
            RoomBigButton(title: RoomModeStrings.checkNow, fill: c.dim, ink: c.text1, compact: s.compact) {
                model.checkNow()
            }
        }
    }

    /// The Guided Access tip and the footer (DEBUG: the perf line).
    @ViewBuilder
    private func extras(_ c: RoomPalette, gap: CGFloat) -> some View {
        if model.showsGuidedAccessTip {
            HStack(spacing: 12) {
                Button(model.guidedAccessLine, action: onGuidedAccess)
                    .font(.footnote)
                    .foregroundStyle(c.text2)
                    .underline()
                    .accessibilityIdentifier("room-guided-access-tip")
                // A household phone's line stays while Guided Access is off
                // (security audit round 6, IOS-2).
                if model.guidedAccessTipDismissible {
                    Button(RoomModeStrings.guidedAccessDismiss) { model.dismissGuidedAccessTip() }
                        .font(.footnote)
                        .foregroundStyle(c.text2)
                }
            }
            .multilineTextAlignment(.center)
            .padding(.top, gap)
        }
        footer.padding(.top, gap)
    }

    /// The big button and Exit (hold), always on screen.
    private func actionBar(_ s: RoomDisplayModel.State, _ c: RoomPalette) -> some View {
        VStack(spacing: 8) {
            bigButton(s, c)
            RoomExitControl { model.exitHeld() }
        }
    }

    @ViewBuilder
    private func bigButton(_ s: RoomDisplayModel.State, _ c: RoomPalette) -> some View {
        switch model.action {
        case .mute:
            RoomBigButton(title: RoomModeStrings.mute, fill: c.dim, ink: c.text1, compact: s.compact) {
                model.perform(.mute)
            }
        case .unmute:
            // A muted room can stay muted for days: the button says so in
            // amber without lighting a big amber block.
            RoomBigButton(title: RoomModeStrings.unmute, fill: .black, ink: c.amberInk, compact: s.compact, outline: c.amber) {
                model.perform(.unmute)
            }
        case .resume:
            RoomBigButton(title: RoomModeStrings.resume, fill: c.amber, ink: .black, compact: s.compact) {
                model.perform(.resume)
            }
        case .openSettings:
            RoomBigButton(title: RoomModeStrings.openAppSettings, fill: c.amber, ink: .black, compact: s.compact) {
                model.perform(.openSettings)
                if let url = URL(string: UIApplication.openSettingsURLString) { UIApplication.shared.open(url) }
            }
        case .none:
            EmptyView()
        }
    }
}

/// Follows the recorder itself: "mic off" only once no recorder is open,
/// whatever the Mute button says; "mic live" while what it hears goes to
/// the server (`RoomModeActivity.kt:418-446`).
struct RoomMicChip: View {
    let open: Bool
    let live: Bool
    let muted: Bool

    var body: some View {
        let off = !open
        let ink: Color = off ? RoomInk.dim : (live ? RoomInk.red : RoomInk.text2)
        HStack(spacing: 8) {
            Circle()
                .fill(live && !off ? RoomInk.red : ink)
                .frame(width: 10, height: 10)
            Text(off ? RoomModeStrings.micOff : (live ? RoomModeStrings.micLive : RoomModeStrings.micListening))
                .font(.footnote)
                .foregroundStyle(ink)
                .strikethrough(off && muted)
        }
        .padding(.horizontal, 12)
        .padding(.vertical, 6)
        .overlay(Capsule().stroke(ink, lineWidth: 1))
        .accessibilityElement(children: .ignore)
        .accessibilityLabel(RoomModeStrings.micChipDescription(off: off, live: live))
        .accessibilityIdentifier("room-mic-chip")
    }
}

struct RoomBigButton: View {
    let title: String
    let fill: Color
    let ink: Color
    let compact: Bool
    /// An outline instead of a bright fill, for a state that can last days.
    var outline: Color? = nil
    let action: () -> Void

    var body: some View {
        Button(action: action) {
            Text(title)
                .font(.system(size: 24, weight: .medium))
                .foregroundStyle(ink)
                .frame(maxWidth: 420)
                .frame(minHeight: compact ? 56 : 72)
                .background(fill, in: Capsule())
                .overlay {
                    if let outline { Capsule().stroke(outline, lineWidth: 1) }
                }
        }
        .buttonStyle(.plain)
        .padding(.horizontal, 32)
    }
}

/// A tap does nothing (a room is not left by accident); a hold of 0.8 s, or
/// VoiceOver's "exit room mode" action, asks for the phone's unlock.
struct RoomExitControl: View {
    let onExit: () -> Void

    var body: some View {
        Text(RoomModeStrings.exitHold)
            .font(.subheadline)
            .foregroundStyle(RoomInk.text2)
            .padding(.horizontal, 20)
            .padding(.vertical, 12)
            .overlay(RoundedRectangle(cornerRadius: 12).stroke(RoomInk.dim, lineWidth: 1))
            .contentShape(Rectangle())
            .onLongPressGesture(minimumDuration: 0.8, perform: onExit)
            .accessibilityElement(children: .ignore)
            .accessibilityLabel(RoomModeStrings.exitHint)
            .accessibilityAddTraits(.isButton)
            .accessibilityAction(named: RoomModeStrings.exitAction, onExit)
            .accessibilityIdentifier("room-exit")
    }
}

/// How to keep the phone in Domovoi (ROOMMODE-MAP §3.5). Advice, never
/// required.
struct RoomGuidedAccessSheet: View {
    @Environment(\.dismiss) private var dismiss

    var body: some View {
        NavigationStack {
            ScrollView {
                VStack(alignment: .leading, spacing: 14) {
                    Text(RoomModeStrings.guidedAccessIntro)
                        .fixedSize(horizontal: false, vertical: true)
                    ForEach(Array(RoomModeStrings.guidedAccessSteps.enumerated()), id: \.offset) { index, step in
                        HStack(alignment: .firstTextBaseline, spacing: 10) {
                            Text("\(index + 1).")
                                .font(.body.weight(.semibold))
                                .foregroundStyle(Color.brandInk)
                            Text(step)
                                .fixedSize(horizontal: false, vertical: true)
                        }
                    }
                    Text(RoomModeStrings.guidedAccessAfter)
                        .font(.subheadline)
                        .foregroundStyle(Color.fgMuted)
                        .fixedSize(horizontal: false, vertical: true)
                }
                .padding(20)
            }
            .navigationTitle(RoomModeStrings.guidedAccessTitle)
            .navigationBarTitleDisplayMode(.inline)
            .toolbar {
                ToolbarItem(placement: .confirmationAction) {
                    Button(RoomModeStrings.done) { dismiss() }
                }
            }
        }
    }
}
