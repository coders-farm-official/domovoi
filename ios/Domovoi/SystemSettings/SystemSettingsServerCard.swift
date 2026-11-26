import DomovoiKit
import SwiftUI

/// The Domovoi server, as admin on this phone: its version, check for
/// updates, Update Domovoi, and the two restarts, each after the
/// dashboard's own confirm (``ServerUpdateWords``). The port of Android's
/// `ServerUpdateCard` (`ServerUpdate.kt`); the rules are
/// ``ServerUpdateModel``'s.
///
/// Drawn only while this phone holds admin and the screen is not a shared
/// one (``serverAdminHeld(_:sharedScreen:)``): ``SystemSettingsAboutPanel``
/// decides. The channel switch, a Windows or Mac install's host step,
/// signed-update badges and the restart-by-hand command stay on the
/// dashboard.
struct SystemSettingsServerCard: View {

    let model: ShellModel

    @State private var server: ServerUpdateModel?
    @State private var asking: ServerAsk?

    var body: some View {
        PanelCard(title: ServerUpdateWords.title, sub: ServerUpdateWords.sub) {
            VStack(alignment: .leading, spacing: 8) {
                if let server, let v = server.version, let mode = server.mode {
                    content(server, v, mode)
                } else {
                    Text(ServerUpdateWords.reading)
                        .font(.footnote)
                        .foregroundStyle(Color.fgMuted)
                }
            }
        }
        .task {
            if server == nil {
                let made = ServerUpdateModel(admin: model.container.admin)
                made.say = { [model] said in model.showToast(said) }
                server = made
            }
            await server?.load()
        }
        .alert(
            Text(verbatim: asking.map { ServerUpdateWords.split($0.text).title } ?? ""),
            isPresented: Binding(get: { asking != nil }, set: { shown in if !shown { asking = nil } }),
            presenting: asking
        ) { ask in
            Button(ask.button) {
                asking = nil
                run(ask)
            }
            Button(ServerUpdateWords.cancel, role: .cancel) { asking = nil }
        } message: { ask in
            Text(verbatim: ServerUpdateWords.split(ask.text).body)
        }
    }

    @ViewBuilder
    private func content(_ server: ServerUpdateModel, _ v: ServerVersion, _ mode: ServerMode) -> some View {
        Text(verbatim: v.version ?? v.sha ?? "—")
            .font(.body.monospaced())
            .textSelection(.enabled)
        if let status = v.lastUpdate?.status {
            Text(ServerUpdateWords.lastUpdate(status))
                .font(.footnote)
                .foregroundStyle(Color.fgMuted)
        }
        if mode != .busy {
            if v.codePending && v.restartRequired {
                warn(ServerUpdateWords.codePending(restartCapable: v.restartCapable))
            }
            if !v.pluginsPending.isEmpty {
                warn(ServerUpdateWords.pluginsPending(v.pluginsPending, restartCapable: v.restartCapable))
            }
        }
        buttons(server, v, mode)
        if mode != .restart && mode != .busy, let words = ServerUpdateWords.checkWords(server.check) {
            Text(words)
                .font(.footnote)
                .foregroundStyle((server.check?.behind ?? 0) > 0 ? Color.warnInk : Color.okInk)
                .fixedSize(horizontal: false, vertical: true)
        }
        if mode == .held, let bad = v.badSha {
            warn(ServerUpdateWords.held(bad))
        }
        if mode == .pull, let host = v.hostSide {
            muted(ServerUpdateWords.hostOnDashboard(host))
        }
        if !v.restartCapable && mode != .busy && v.restartRequired {
            muted(ServerUpdateWords.byHand(v))
        }
        if let problem = server.problem {
            Text(problem)
                .font(.footnote)
                .foregroundStyle(Color.errInk)
                .fixedSize(horizontal: false, vertical: true)
        }
    }

    @ViewBuilder
    private func buttons(_ server: ServerUpdateModel, _ v: ServerVersion, _ mode: ServerMode) -> some View {
        VStack(alignment: .leading, spacing: 8) {
            switch mode {
            case .busy:
                let updating = server.following.map(\.updating) ?? v.updateUnit
                Button(updating ? ServerUpdateWords.updating : ServerUpdateWords.restarting) {}
                    .buttonStyle(BrandFillButtonStyle())
                    .disabled(true)
            case .restart:
                if v.restartCapable {
                    Button(ServerUpdateWords.restartApply) { asking = .apply(v) }
                        .buttonStyle(BrandFillButtonStyle())
                }
            case .pull:
                if v.hostSide == nil {
                    Button(server.pulling ? ServerUpdateWords.pulling : ServerUpdateWords.update) {
                        asking = serverOnePress(v) ? .onePress : .pull
                    }
                    .buttonStyle(BrandFillButtonStyle())
                    .disabled(server.pulling)
                }
            case .check, .held:
                Button(server.checking ? ServerUpdateWords.checking : ServerUpdateWords.check) {
                    Task { @MainActor in await server.checkForUpdates() }
                }
                .buttonStyle(InkOutlineButtonStyle())
                .disabled(server.checking)
            }
            if v.restartCapable && mode != .restart && mode != .busy {
                Button(ServerUpdateWords.restart) { asking = .plain(v) }
                    .buttonStyle(InkOutlineButtonStyle())
                    .disabled(server.pulling)
            }
        }
        .font(.subheadline)
    }

    private func run(_ ask: ServerAsk) {
        guard let server else { return }
        Task { @MainActor in
            switch ask {
            case .pull: await server.update(onePress: false)
            case .onePress: await server.update(onePress: true)
            case .plain: await server.restart(plain: true)
            case .apply: await server.restart(plain: false)
            }
        }
    }

    private func warn(_ text: String) -> some View {
        Text(text)
            .font(.footnote)
            .foregroundStyle(Color.warnInk)
            .fixedSize(horizontal: false, vertical: true)
    }

    private func muted(_ text: String) -> some View {
        Text(text)
            .font(.footnote)
            .foregroundStyle(Color.fgMuted)
            .fixedSize(horizontal: false, vertical: true)
    }
}

/// The confirm up, with the dashboard's words (taken when it was asked).
enum ServerAsk {
    case pull
    case onePress
    case plain(ServerVersion)
    case apply(ServerVersion)

    var text: String {
        switch self {
        case .pull: return ServerUpdateWords.pullConfirm
        case .onePress: return ServerUpdateWords.onePressConfirm
        case .plain(let v): return ServerUpdateWords.restartConfirm(v, plain: true)
        case .apply(let v): return ServerUpdateWords.restartConfirm(v, plain: false)
        }
    }

    var button: String {
        switch self {
        case .pull, .onePress: return ServerUpdateWords.updateButton
        case .plain, .apply: return ServerUpdateWords.restartButton
        }
    }
}
