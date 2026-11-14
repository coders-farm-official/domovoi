import DomovoiKit
import SwiftUI
import UIKit

/// The way into room mode (`roommode/RoomSetupActivity.kt`), over the
/// household app or the no-server shell: the explainer, the microphone, the
/// room code. The fourth screen — the approval code — is the room screen.
/// Dark, like Android's (`ThemeMode.Dark`), so it already looks like the
/// room it is about to become.
struct RoomSetupScreen: View {
    let scene: RoomModeScene
    @StateObject private var model: RoomSetupModel
    @Environment(\.scenePhase) private var scenePhase

    init(scene: RoomModeScene, payload: String?) {
        self.scene = scene
        _model = StateObject(wrappedValue: RoomSetupModel(
            payload: payload,
            lock: RoomModeRuntime.lockFacts(fixed: scene.launch.fixedLock),
            permissions: RoomPermissions(feed: scene.launch.feed),
            micRefusal: { PlayerMicArbiter.refusal(.roomMode) },
            onBegin: { scene.begin($0) }
        ))
    }

    var body: some View {
        RoomSetupView(model: model, onCancel: { scene.setup = nil })
            .preferredColorScheme(.dark)
            .onChange(of: scenePhase) { _, phase in
                // Re-read at every return: setting a passcode in Settings
                // lets the person straight on.
                if phase == .active { model.refreshLock() }
            }
            .onOpenURL { url in
                if url.scheme?.lowercased() == RoomEnrolmentParser.scheme { model.incoming(url.absoluteString) }
            }
    }
}

struct RoomSetupView: View {
    @ObservedObject var model: RoomSetupModel
    let onCancel: () -> Void

    var body: some View {
        Group {
            if model.step == .explain {
                RoomExplainerView(model: model, onCancel: onCancel)
            } else if !model.lockOK {
                needsLock
            } else if model.step == .microphone {
                microphone
            } else {
                RoomCodeView(model: model, onCancel: onCancel)
            }
        }
        .background(Color(.systemBackground).ignoresSafeArea())
    }

    /// A household phone without a passcode (``RoomLockRules``). No button
    /// to the setting: iOS has no link to Face ID & Passcode, so the sentence
    /// says where it is.
    private var needsLock: some View {
        ScrollView {
            VStack(alignment: .leading, spacing: 12) {
                RoomSetupHeading(RoomModeStrings.setALockTitle)
                Text(RoomModeStrings.setALockBody)
                    .font(.body)
                    .fixedSize(horizontal: false, vertical: true)
                Button(RoomModeStrings.notNow, action: onCancel)
                    .buttonStyle(InkOutlineButtonStyle())
                    .padding(.top, 8)
            }
            .padding(20)
            .frame(maxWidth: 560, alignment: .leading)
            .frame(maxWidth: .infinity)
        }
    }

    private var microphone: some View {
        ScrollView {
            VStack(alignment: .leading, spacing: 12) {
                RoomSetupHeading(RoomModeStrings.micTitle)
                Text(RoomModeStrings.micBody)
                    .font(.body)
                    .fixedSize(horizontal: false, vertical: true)
                if model.micDenied {
                    Text(RoomModeStrings.micDenied)
                        .foregroundStyle(Color.errInk)
                        .fixedSize(horizontal: false, vertical: true)
                    Button(RoomModeStrings.openAppSettings) {
                        if let url = URL(string: UIApplication.openSettingsURLString) {
                            UIApplication.shared.open(url)
                        }
                    }
                }
                RoomSetupButtons(
                    cancel: onCancel,
                    title: RoomModeStrings.allow,
                    enabled: true,
                    action: { model.allowMic() }
                )
                .padding(.top, 8)
            }
            .padding(20)
            .frame(maxWidth: 560, alignment: .leading)
            .frame(maxWidth: .infinity)
        }
    }
}

struct RoomSetupHeading: View {
    let text: String
    init(_ text: String) { self.text = text }

    var body: some View {
        Text(text)
            .font(.title2.weight(.semibold))
            .accessibilityAddTraits(.isHeader)
            .fixedSize(horizontal: false, vertical: true)
    }
}

/// "preview" beside the choice (owner decision 2, 2026-10-16;
/// `ui/components/Kit.kt:104-118`, `PreviewLabel`).
struct RoomPreviewLabel: View {
    var body: some View {
        VStack(alignment: .leading, spacing: 2) {
            Text(RoomModeStrings.previewLabel)
                .font(.caption.weight(.semibold))
                .foregroundStyle(Color.brandInk)
            Text(RoomModeStrings.previewMeans)
                .font(.caption)
                .foregroundStyle(Color.fgMuted)
                .fixedSize(horizontal: false, vertical: true)
        }
        .accessibilityElement(children: .combine)
    }
}

/// Not now, and the step's one button.
struct RoomSetupButtons: View {
    let cancel: () -> Void
    let title: String
    let enabled: Bool
    let action: () -> Void

    var body: some View {
        ViewThatFits(in: .horizontal) {
            HStack(spacing: 12) { notNow; primary }
            VStack(alignment: .leading, spacing: 12) { primary; notNow }
        }
    }

    private var notNow: some View {
        Button(RoomModeStrings.notNow, action: cancel)
            .buttonStyle(InkOutlineButtonStyle())
            .frame(minHeight: 44)
    }

    private var primary: some View {
        Button(title, action: action)
            .buttonStyle(BrandFillButtonStyle())
            .frame(minHeight: 44)
            .disabled(!enabled)
    }
}

/// Step 1: the owner's six points, read to the end before **Start room
/// mode** works (`RoomSetupActivity.kt:242-272`). "Read to the end" is the
/// end marker coming into the scroll view's frame, measured, not
/// `onAppear` (which fires for a whole `VStack` at once).
struct RoomExplainerView: View {
    @ObservedObject var model: RoomSetupModel
    let onCancel: () -> Void

    private struct EndKey: PreferenceKey {
        static let defaultValue: CGFloat = .infinity
        static func reduce(value: inout CGFloat, nextValue: () -> CGFloat) { value = min(value, nextValue()) }
    }

    var body: some View {
        VStack(alignment: .leading, spacing: 8) {
            RoomSetupHeading(RoomModeStrings.explainerTitle)
            RoomPreviewLabel()
            GeometryReader { viewport in
                let height = viewport.size.height
                let reader = model
                ScrollView {
                    VStack(alignment: .leading, spacing: 16) {
                        ForEach(Array(RoomModeStrings.explainer.enumerated()), id: \.offset) { index, point in
                            VStack(alignment: .leading, spacing: 4) {
                                Text("\(index + 1). \(point.title)")
                                    .font(.headline)
                                    .foregroundStyle(Color.brandInk)
                                    .accessibilityAddTraits(.isHeader)
                                    .fixedSize(horizontal: false, vertical: true)
                                Text(point.body)
                                    .font(.body)
                                    .fixedSize(horizontal: false, vertical: true)
                            }
                        }
                        Color.clear
                            .frame(height: 1)
                            .background(GeometryReader { marker in
                                Color.clear.preference(
                                    key: EndKey.self,
                                    value: marker.frame(in: .named("explainer")).minY
                                )
                            })
                    }
                    .padding(.vertical, 8)
                }
                .coordinateSpace(name: "explainer")
                .onPreferenceChange(EndKey.self) { endY in
                    let atEnd = endY <= height + 8
                    Task { @MainActor in
                        if atEnd && !reader.readToTheEnd { reader.readToTheEnd = true }
                    }
                }
            }
            if !model.readToTheEnd {
                Text(RoomModeStrings.readToTheEnd)
                    .font(.footnote)
                    .foregroundStyle(Color.fgMuted)
            }
            RoomSetupButtons(
                cancel: onCancel,
                title: RoomModeStrings.startRoomMode,
                enabled: model.readToTheEnd,
                action: { model.start() }
            )
        }
        .padding(20)
        .frame(maxWidth: 560, alignment: .leading)
        .frame(maxWidth: .infinity)
    }
}

/// Step 3: the room code, scanned or pasted, shown back, confirmed
/// (`RoomSetupActivity.kt:314-403`).
struct RoomCodeView: View {
    @ObservedObject var model: RoomSetupModel
    let onCancel: () -> Void

    private var text: Binding<String> {
        Binding(get: { model.text }, set: { model.setText($0) })
    }

    private var name: Binding<String> {
        Binding(
            get: { model.shownName },
            set: { model.editedName = String($0.prefix(RoomEnrolmentParser.maxRoomNameLength + 8)) }
        )
    }

    var body: some View {
        ScrollView {
            VStack(alignment: .leading, spacing: 12) {
                RoomSetupHeading(RoomModeStrings.codeTitle)
                Text(RoomModeStrings.codeBody)
                    .font(.body)
                    .fixedSize(horizontal: false, vertical: true)
                Text(RoomModeStrings.chargerTip)
                    .font(.subheadline)
                    .foregroundStyle(Color.fgMuted)
                    .fixedSize(horizontal: false, vertical: true)
                if model.scanning {
                    RoomScannerView { code in model.scanned(code) }
                        .aspectRatio(1, contentMode: .fit)
                        .frame(maxWidth: .infinity)
                        .clipShape(RoundedRectangle(cornerRadius: 12))
                        .accessibilityLabel(RoomModeStrings.scannerLabel)
                    Button(RoomModeStrings.stopScanning) { model.stopScanning() }
                        .buttonStyle(InkOutlineButtonStyle())
                } else {
                    // On a phone without a camera (a Simulator) the button
                    // says so when pressed instead of opening nothing.
                    Button(RoomModeStrings.scan) { model.scan() }
                        .buttonStyle(BrandFillButtonStyle())
                        .frame(minHeight: 44)
                }
                TextField(RoomModeStrings.pasteLabel, text: text, prompt: ShellHintPrompt.text(RoomModeStrings.pasteLabel))
                    .textInputAutocapitalization(.never)
                    .autocorrectionDisabled()
                    .font(.subheadline)
                    .padding(.horizontal, 8)
                    .padding(.vertical, 10)
                    .frame(minHeight: 44)
                    .background(Color(.secondarySystemBackground), in: RoundedRectangle(cornerRadius: 8))
                    .accessibilityLabel(RoomModeStrings.pasteLabel)
                    .accessibilityIdentifier("room-code-field")
                if let refusal = model.refusal {
                    Text(refusal)
                        .foregroundStyle(Color.errInk)
                        .fixedSize(horizontal: false, vertical: true)
                }
                if let problem = model.problem {
                    Text(problem)
                        .foregroundStyle(Color.errInk)
                        .fixedSize(horizontal: false, vertical: true)
                }
                if let e = model.enrolment {
                    Text(RoomModeStrings.server(e.coreURL))
                        .font(.body)
                        .fixedSize(horizontal: false, vertical: true)
                    Text(RoomModeStrings.serverKey(e.fingerprint))
                        .font(.subheadline)
                        .foregroundStyle(Color.fgMuted)
                        .fixedSize(horizontal: false, vertical: true)
                    Text(RoomModeStrings.compareKey)
                        .font(.footnote)
                        .foregroundStyle(Color.fgMuted)
                        .fixedSize(horizontal: false, vertical: true)
                    TextField(RoomModeStrings.roomName, text: name, prompt: ShellHintPrompt.text(RoomModeStrings.roomName))
                        .font(.body)
                        .padding(.horizontal, 8)
                        .padding(.vertical, 10)
                        .frame(minHeight: 44)
                        .background(Color(.secondarySystemBackground), in: RoundedRectangle(cornerRadius: 8))
                        .accessibilityLabel(RoomModeStrings.roomName)
                        .accessibilityIdentifier("room-name-field")
                    if let nameRefusal = model.nameRefusal {
                        Text(nameRefusal)
                            .foregroundStyle(Color.errInk)
                            .fixedSize(horizontal: false, vertical: true)
                    }
                    RoomSetupButtons(
                        cancel: onCancel,
                        title: RoomModeStrings.becomeThisRoom,
                        enabled: model.canBecome,
                        action: { model.become() }
                    )
                    .padding(.top, 8)
                } else {
                    Button(RoomModeStrings.notNow, action: onCancel)
                        .buttonStyle(InkOutlineButtonStyle())
                        .frame(minHeight: 44)
                        .padding(.top, 8)
                }
            }
            .padding(20)
            .frame(maxWidth: 560, alignment: .leading)
            .frame(maxWidth: .infinity)
        }
        .scrollDismissesKeyboard(.interactively)
    }
}
