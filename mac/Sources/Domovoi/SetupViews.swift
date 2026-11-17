import AppKit
import DomovoiMacKit
import SwiftUI

// The setup pages (mac-installer PLAN 5): check, what changes, the internet
// question (the Windows text, PLAN 4.2a), the media folder, the work, the
// setup code. And, for an installed Domovoi: open, repair, uninstall.
// AppMain.swift hosts them in a window (and the menu-bar icon beside them).

struct SetupView: View {
    @EnvironmentObject var m: SetupModel

    var body: some View {
        VStack(alignment: .leading, spacing: 0) {
            Header(title: title)
            Divider()
            Group {
                switch m.page {
                case .checking: Centered { ProgressView("Checking this Mac...") }
                case .check: CheckPage()
                case .consent: ConsentPage()
                case .internet: InternetPage()
                case .media: MediaPage()
                case .working: WorkPage()
                case .finished: FinishedPage()
                case .failed: FailedPage()
                case .installed: InstalledPage()
                case .uninstalled: UninstalledPage()
                }
            }
            .padding(24)
            .frame(maxWidth: .infinity, maxHeight: .infinity, alignment: .topLeading)
        }
        .onAppear { if m.report == nil { m.runPreflight() } }
        .alert(m.conflict.map { "\($0.owner) is using Domovoi's ports" } ?? "", isPresented: $m.showConflict, presenting: m.conflict) { c in
            Button("Retry") { m.runPreflight() }
            if c.ollamaUsable { Button("Use this Ollama") { m.useExistingOllama() } }
            Button("Cancel Setup", role: .cancel) { NSApp.terminate(nil) }
        } message: { c in
            Text(conflictText(c))
        }
    }

    var title: String {
        switch m.page {
        case .checking, .check: return "Checking this Mac"
        case .consent: return "What Setup will change"
        case .internet: return "Will this Mac have internet after setup?"
        case .media: return "Your media folder"
        case .working: return m.workTitle
        case .finished: return "Domovoi is ready"
        case .failed: return "Setup didn't finish"
        case .installed: return "Domovoi is installed"
        case .uninstalled: return "Domovoi is removed"
        }
    }

    func conflictText(_ c: PortConflict) -> String {
        var t = "\(c.owner) holds port\(c.ports.count == 1 ? "" : "s") \(Preflight.portList(c.ports)), which Domovoi needs. "
        if c.ollamaVersion != nil {
            t += "It is Ollama \(c.ollamaVersion!). "
            if c.ollamaUsable {
                t += "Domovoi can use it instead of running its own: its AI models then stay in that Ollama's own folder, and uninstalling Domovoi leaves them there."
                if c.ollamaIsApp { t += " The Ollama app runs only while someone is signed in, so Domovoi's AI answers stop when nobody is." }
                t += " Or quit Ollama and choose Retry, and Domovoi runs its own."
            } else {
                t += "It is too old for Domovoi: quit it (or update it) and choose Retry."
            }
        } else {
            t += "Quit \(c.owner), then choose Retry."
        }
        return t
    }
}

struct Header: View {
    let title: String
    var body: some View {
        HStack(spacing: 12) {
            Image(nsImage: AppIcon.image).resizable().frame(width: 40, height: 40)
            Text(title).font(.title2).bold()
            Spacer()
        }
        .padding(.horizontal, 24).padding(.vertical, 14)
    }
}

struct Centered<C: View>: View {
    @ViewBuilder var content: C
    var body: some View { VStack { Spacer(); HStack { Spacer(); content; Spacer() }; Spacer() } }
}

struct Footer<L: View, R: View>: View {
    @ViewBuilder var left: L
    @ViewBuilder var right: R
    var body: some View {
        VStack(spacing: 0) {
            Spacer(minLength: 12)
            HStack { left; Spacer(); right }
        }
    }
}

struct ErrorText: View {
    let text: String?
    var body: some View {
        if let t = text, !t.isEmpty {
            Text(t).foregroundStyle(.red).font(.callout).textSelection(.enabled).fixedSize(horizontal: false, vertical: true)
        }
    }
}

// ─── check ─────────────────────────────────────────────────────────────

struct CheckPage: View {
    @EnvironmentObject var m: SetupModel
    var body: some View {
        VStack(alignment: .leading, spacing: 10) {
            if let e = m.preflightError { ErrorText(text: e) }
            if let r = m.report {
                ForEach(r.checks, id: \.id) { c in
                    HStack(alignment: .firstTextBaseline, spacing: 10) {
                        Image(systemName: icon(c.status)).foregroundStyle(color(c.status)).frame(width: 18)
                        Text(c.label).bold().frame(width: 150, alignment: .leading)
                        Text(c.detail).fixedSize(horizontal: false, vertical: true).textSelection(.enabled)
                    }
                }
                if !r.ok {
                    Text("Setup can't go on until the red items are fixed. Nothing has been changed.").font(.callout).padding(.top, 6)
                }
            }
            Footer {
                Button("Check Again") { m.runPreflight() }
            } right: {
                Button("Continue") { m.page = .consent }.keyboardShortcut(.defaultAction).disabled(!m.canContinue)
            }
        }
    }
    func icon(_ s: String) -> String {
        switch s { case "ok": return "checkmark.circle.fill"; case "fail": return "xmark.octagon.fill"; case "warn": return "exclamationmark.triangle.fill"; default: return "info.circle" }
    }
    func color(_ s: String) -> Color {
        switch s { case "ok": return .green; case "fail": return .red; case "warn": return .orange; default: return .secondary }
    }
}

// ─── what changes ──────────────────────────────────────────────────────

/// Whether this package carries the speech service (PHASE8A-STT.md):
/// mac/build.sh puts whisper-server in only when pins.conf names it, and the
/// install sets it up whenever the program is there.
let packageHasSpeechService = FileManager.default.isExecutableFile(atPath: Layout.system.sttProgram.path)

struct ConsentPage: View {
    @EnvironmentObject var m: SetupModel
    var body: some View {
        let r = m.report
        VStack(alignment: .leading, spacing: 10) {
        // FileVault (the owner's required disclosure, PLAN 12.2) and the
        // laptop note stay above the scrolling list, never below its fold
        // (P3-2: in a 600-point window they were out of sight).
        VStack(alignment: .leading, spacing: 6) {
            HStack(alignment: .firstTextBaseline) {
                Text("FileVault").bold()
                Spacer()
                Button("Open FileVault Settings...") { m.openFileVaultSettings() }
            }
            Text(fileVaultSentence(r?.fileVault ?? "unknown")).fixedSize(horizontal: false, vertical: true)
            if r?.laptop == true {
                Text("This Mac is a laptop").bold().padding(.top, 4)
                Text("A MacBook sleeps when its lid closes, and Domovoi stops while it sleeps. Keep the lid open and the charger in.").fixedSize(horizontal: false, vertical: true)
            }
        }
        .padding(10)
        .background(RoundedRectangle(cornerRadius: 6).fill(Color.orange.opacity(0.10)))
        .overlay(RoundedRectangle(cornerRadius: 6).stroke(Color.orange.opacity(0.5)))
        ScrollView {
            VStack(alignment: .leading, spacing: 10) {
                Text("Nothing has changed yet. When you choose Install, Setup asks for an administrator's password once and then:")
                Bullet("Makes a hidden account, _domovoi, with no password, that can't log in. It runs Domovoi's virtual machine\(m.ollama == "own" ? " and its AI engine" : "").")
                // What System Settings really shows (P3-6, macOS 26.4): each
                // service under the signer of the program it runs, never
                // "Domovoi" in this unsigned build.
                Bullet(ConsentText.services(ownOllama: m.ollama == "own", speech: packageHasSpeechService))
                Bullet("Keeps Domovoi in /Library/Application Support/Domovoi: a Linux virtual machine (it grows to about 15-20 GB)\(m.ollama == "own" ? ", the AI models (5-9 GB)" : "")\(packageHasSpeechService ? ", the speech model (0.5 GB)" : ""), and its downloads, all left out of Time Machine. Logs go to /Library/Logs/Domovoi.")
                Bullet("Time Machine doesn't back up Domovoi's settings and database yet: they live inside its virtual machine, which Time Machine skips. A backup tool for them is planned.")
                Bullet("Lets Domovoi read and write the media folder you choose next (an access entry on the folder). Your files there are never deleted, not even by uninstalling.")
                Bullet(packageHasSpeechService
                       ? "Downloads about 10.5-13.5 GB, which takes 20-40 minutes on a typical connection."
                       : "Downloads about 10-13 GB, which takes 20-40 minutes on a typical connection.")
                Bullet("Opens Domovoi on this Mac at http://localhost:6369.\(m.ollama == "own" ? " Its AI engine listens on this Mac only (port 11434)." : "")")
                // The icon's LaunchAgent: listed under its program, the app
                // (unsigned build: "Domovoi", an unidentified developer; to
                // be confirmed on the Mac, as P3-6 was for the services).
                Bullet("Adds the Domovoi icon to the menu bar for every account on this Mac (com.domovoi.mac.menubar in /Library/LaunchAgents). It says whether Domovoi is running, and has Restart and Save logs. Restart needs no password: one more background job, com.domovoi.mac.restart in /Library/LaunchDaemons, does only that when asked (any account on this Mac may ask; one restart at a time, at most one every two minutes). In System Settings > General > Login Items & Extensions it is listed as \"Domovoi\" (an item from an unidentified developer); switching it off hides only the icon, and Domovoi keeps running. The first time the icon starts in an account, macOS shows a \"Domovoi Notifications\" notice: Domovoi uses notifications only to say it stopped or came back, that one of its services was switched off, or how an update went.")
                Bullet(m.keepAwake
                       ? "Changes one power setting, if you leave Keep this Mac awake on below: this Mac no longer goes to sleep by itself while it is plugged in (the screen still turns off). The old setting is noted, and put back when you turn it off in the menu-bar icon or uninstall Domovoi."
                       : "Doesn't change this Mac's power or sleep settings: with Keep this Mac awake off, Domovoi stops whenever this Mac goes to sleep.")
                if m.offerPowerFailure {
                    Bullet(m.startAfterPowerFailure
                           ? "Changes one more power setting, if you leave Start this Mac after a power failure on below: after a power cut, this Mac starts up by itself when the power comes back, and Domovoi with it. The old setting is noted, and put back when you turn it off in the menu-bar icon or uninstall Domovoi."
                           : "Doesn't change what this Mac does after a power cut: it stays off until someone presses its power button.")
                }
                Bullet("Lets phones, computers and room speakers on your home network reach Domovoi at this Mac's own address: a rule in the Mac's packet filter (pf) passes Domovoi's ports (\(PfRules.portWords())) from your home network only to Domovoi's virtual machine, and the Mac is set to pass those connections on (IP forwarding). Nothing from the internet, and no other port. The Mac passes on nothing else: another rule drops anything a device sends through it toward any other network (a VPN, another network the Mac is on). The rules follow the Mac when it changes networks, and uninstalling takes them all away again.")
                Bullet("Answers for the name domovoi.local on your home network, unless another device there already does.")
                Bullet("Tells Domovoi when files change in the media folder, so new music shows up without a rescan.")
                Bullet("Doesn't change this Mac's firewall setting or /etc/pf.conf.")
                Text("To remove Domovoi later, open this app again and choose Uninstall.").font(.callout).foregroundStyle(.secondary).padding(.top, 6)
            }
            .padding(10)
        }
        // The list takes the free height before the footer's spacer does,
        // shows its scroll bar all the time, and sits in a box, so it reads
        // as a list that goes on (Train 1 finding 4).
        .scrollIndicators(.visible)
        .frame(maxHeight: .infinity)
        .layoutPriority(1)
        .background(RoundedRectangle(cornerRadius: 6).fill(Color.secondary.opacity(0.06)))
        .overlay(RoundedRectangle(cornerRadius: 6).stroke(Color.secondary.opacity(0.3)))
        Text("Scroll to read the whole list. You can make this window bigger.").font(.caption).foregroundStyle(.secondary)
        Toggle(isOn: $m.keepAwake) {
            VStack(alignment: .leading, spacing: 2) {
                Text("Keep this Mac awake while it's plugged in").bold()
                Text(r?.laptop == true
                     ? "Recommended: Domovoi stops while the Mac sleeps. A MacBook still sleeps when its lid is closed."
                     : "Recommended: Domovoi stops while the Mac sleeps. You can change this later in the Domovoi icon's menu.")
                    .font(.callout).foregroundStyle(.secondary).fixedSize(horizontal: false, vertical: true)
            }
        }
        .toggleStyle(.checkbox)
        if m.offerPowerFailure {
            Toggle(isOn: $m.startAfterPowerFailure) {
                VStack(alignment: .leading, spacing: 2) {
                    Text("Start this Mac after a power failure").bold()
                    Text(m.report?.fileVault == "off"
                         ? "Recommended: after a power cut, this Mac and Domovoi start again by themselves. You can change this later in the Domovoi icon's menu."
                         : "After a power cut this Mac starts by itself, but with FileVault on it waits at its unlock screen until someone types a password. You can change this later in the Domovoi icon's menu.")
                        .font(.callout).foregroundStyle(.secondary).fixedSize(horizontal: false, vertical: true)
                }
            }
            .toggleStyle(.checkbox)
        }
        Footer {
            Button("Back") { m.page = .check }
        } right: {
            Button("I Agree, Continue") { m.page = .internet }.keyboardShortcut(.defaultAction)
        }
        }
    }

    func fileVaultSentence(_ fv: String) -> String {
        switch fv {
        case "on": return "FileVault is on, so after a power cut or a restart this Mac waits at its unlock screen and Domovoi stays off until someone types a password there; with FileVault off it would start on its own, but this Mac's disk would not be encrypted. Setup never changes it: the choice is yours."
        case "off": return "FileVault is off, so after a power cut Domovoi starts on its own once the Mac is back on; turning FileVault on encrypts this Mac's disk, but then the Mac waits at its unlock screen after every restart until someone types a password. Setup never changes it: the choice is yours."
        default: return "Setup couldn't tell whether FileVault is on. When it is, this Mac waits at its unlock screen after a power cut, and Domovoi stays off until someone types a password there. Setup never changes it."
        }
    }
}

struct Bullet: View {
    let text: String
    init(_ text: String) { self.text = text }
    var body: some View {
        HStack(alignment: .firstTextBaseline, spacing: 8) {
            Text("•")
            Text(text).fixedSize(horizontal: false, vertical: true)
        }
    }
}

// ─── the internet question (Windows PLAN 4.2a, the same words) ─────────

struct InternetPage: View {
    @EnvironmentObject var m: SetupModel
    var body: some View {
        VStack(alignment: .leading, spacing: 12) {
            Text("Setup needs the internet right now to download Domovoi (about 10-13 GB). After that, Domovoi works with or without it. Your voice and recordings never leave this Mac either way.")
                .fixedSize(horizontal: false, vertical: true)
            Text("Pick one answer to continue.").foregroundStyle(.secondary)
            Choice(value: "always", title: "Yes, always. It's on our home internet.",
                   detail: "Turns on the extras that use the internet: web answers (weather, scores, news), song lyrics, other names for artists, podcast downloads. They pause by themselves if the internet drops.")
            Choice(value: "sometimes", title: "Sometimes. The connection comes and goes, or it's slow or metered.",
                   detail: "Small lookups run whenever the internet is up. Big automatic downloads stay off until you turn them on.")
            Choice(value: "never", title: "No. Keep everything in the house.",
                   detail: "There's no internet here, or I don't want Domovoi to use it. Domovoi won't contact the internet at all. Online extras are switched off; music, timers, intercom, voice and everything else on your network work as normal.")
            HStack(spacing: 4) {
                Text("With the internet on, some extras send short text out, such as a web search you asked for or a song title.")
                Link("What goes out", destination: SetupModel.internetDoc)
            }.font(.callout)
            Text("You can change this later in the dashboard: Settings > Internet.").font(.callout)
            Footer {
                Button("Back") { m.page = .consent }
            } right: {
                Button("Continue") { m.page = .media }.keyboardShortcut(.defaultAction).disabled(m.internet == nil)
            }
        }
    }
}

struct Choice: View {
    @EnvironmentObject var m: SetupModel
    let value: String, title: String, detail: String
    var body: some View {
        Button { m.internet = value } label: {
            HStack(alignment: .firstTextBaseline, spacing: 8) {
                Image(systemName: m.internet == value ? "largecircle.fill.circle" : "circle").foregroundStyle(Color.accentColor)
                VStack(alignment: .leading, spacing: 2) {
                    Text(title).bold()
                    Text(detail).font(.callout).foregroundStyle(.secondary).fixedSize(horizontal: false, vertical: true)
                }
            }
            .contentShape(Rectangle())
        }
        .buttonStyle(.plain)
    }
}

// ─── the media folder ──────────────────────────────────────────────────

struct MediaPage: View {
    @EnvironmentObject var m: SetupModel
    var body: some View {
        VStack(alignment: .leading, spacing: 12) {
            Text("Where your music, pictures and videos live. Setup makes Music, Pictures, Videos, Documents and Audiobooks inside it, and Domovoi finds what you put there.")
                .fixedSize(horizontal: false, vertical: true)
            HStack {
                Text(m.mediaRoot).font(.system(.body, design: .monospaced)).textSelection(.enabled)
                Spacer()
                Button("Choose...") { m.chooseMediaFolder() }
            }
            .padding(10).background(RoundedRectangle(cornerRadius: 6).stroke(Color.secondary.opacity(0.4)))
            if m.mediaRoot == "/Users/Shared/Domovoi" {
                Text("Every account on this Mac sees /Users/Shared in Finder.").font(.callout).foregroundStyle(.secondary)
            }
            if m.mediaRoot.hasPrefix("/Volumes/") {
                Text("This folder is on another disk. Such a disk may not be there until someone signs in to this Mac, and Domovoi can't see the files until it is.")
                    .font(.callout).foregroundStyle(.orange).fixedSize(horizontal: false, vertical: true)
            }
            ErrorText(text: m.failure)
            Footer {
                Button("Back") { m.page = .internet }
            } right: {
                Button("Install") { m.startInstall(repair: false) }.keyboardShortcut(.defaultAction).disabled(m.busy)
            }
        }
    }
}

// ─── the work ──────────────────────────────────────────────────────────

struct WorkPage: View {
    @EnvironmentObject var m: SetupModel
    var body: some View {
        let done = m.steps.filter { $0.state == "done" }.count
        VStack(alignment: .leading, spacing: 8) {
            ProgressView(value: Double(done), total: Double(max(m.steps.count, 1)))
            Text(m.detail).font(.callout).foregroundStyle(.secondary).lineLimit(2)
            ForEach(m.steps) { s in
                HStack(spacing: 8) {
                    switch s.state {
                    case "done": Image(systemName: "checkmark.circle.fill").foregroundStyle(.green)
                    case "fail": Image(systemName: "xmark.octagon.fill").foregroundStyle(.red)
                    case "running": ProgressView().controlSize(.small)
                    default: Image(systemName: "circle").foregroundStyle(.secondary)
                    }
                    Text(s.label).foregroundStyle(s.state == "waiting" ? .secondary : .primary)
                }
                .frame(height: 20)
            }
            Footer {
                Text("You can close this window: the work goes on, and Domovoi is ready when it ends. Opening this app again shows how it went.")
                    .font(.callout).foregroundStyle(.secondary).fixedSize(horizontal: false, vertical: true)
            } right: { EmptyView() }
        }
    }
}

struct FinishedPage: View {
    @EnvironmentObject var m: SetupModel
    var body: some View {
        VStack(alignment: .leading, spacing: 12) {
            Text("Domovoi runs on this Mac now, whether or not anyone is signed in. The Domovoi cat in the menu bar says whether it is running.")
            if let code = m.setupCode {
                Text("Your setup code").bold()
                HStack {
                    Text(code).font(.system(.title3, design: .monospaced)).textSelection(.enabled)
                    Spacer()
                    Button("Copy") { m.copy(code) }
                }
                .padding(10).background(RoundedRectangle(cornerRadius: 6).stroke(Color.secondary.opacity(0.4)))
                Text("The dashboard asks for it once, to make you Domovoi's first admin. Keep it to yourself; it isn't saved in any log.")
                    .font(.callout).foregroundStyle(.secondary).fixedSize(horizontal: false, vertical: true)
            } else if let n = m.setupCodeNote {
                Text("Setup code: \(n)").font(.callout)
            }
            if let n = m.phoneAppNote {
                Text(n).font(.callout).fixedSize(horizontal: false, vertical: true)
            }
            Footer { EmptyView() } right: {
                Button("Open Domovoi") { m.openDashboard() }.keyboardShortcut(.defaultAction)
            }
        }
    }
}

struct FailedPage: View {
    @EnvironmentObject var m: SetupModel
    var body: some View {
        VStack(alignment: .leading, spacing: 12) {
            ErrorText(text: m.failure)
            Text("What Setup did so far is recorded. Running Setup again picks up where it stopped (Repair); Uninstall removes what it made. The details are in /Library/Logs/Domovoi.")
                .fixedSize(horizontal: false, vertical: true)
            Footer {
                Button("Uninstall...") { m.page = .installed }
            } right: {
                Button("Try Again") { m.startInstall(repair: true) }.keyboardShortcut(.defaultAction)
            }
        }
    }
}

// ─── an installed Domovoi ──────────────────────────────────────────────

struct InstalledPage: View {
    @EnvironmentObject var m: SetupModel
    @State var removeEverything = false
    @State var typed = ""
    var body: some View {
        VStack(alignment: .leading, spacing: 12) {
            Text("Domovoi is installed on this Mac. Its dashboard is at http://localhost:6369.")
            HStack {
                Button("Open Domovoi") { m.openDashboard() }
                Button("Show Setup Code...") { m.showSetupCode() }.disabled(m.busy)
                Button("Repair") { m.startInstall(repair: true) }.disabled(m.busy)
            }
            if let code = m.setupCode {
                HStack {
                    Text(code).font(.system(.body, design: .monospaced)).textSelection(.enabled)
                    Button("Copy") { m.copy(code) }
                }
            } else if let n = m.setupCodeNote { Text(n).font(.callout) }
            Text("Repair runs every step of Setup again with the answers you gave, and fixes what it finds.").font(.callout).foregroundStyle(.secondary)
            Divider().padding(.vertical, 6)
            Text("Uninstall").bold()
            Button { removeEverything = false } label: { radio(!removeEverything, "Keep my data", "Removes Domovoi's programs and its background services. Keeps the virtual machine, the AI models, the logs, your answers and the hidden account that owns them, so installing again picks up where it left off.") }.buttonStyle(.plain)
            Button { removeEverything = true } label: { radio(removeEverything, "Remove everything", "Also deletes the virtual machine (Domovoi's settings, rooms and users), the AI models, the logs and Domovoi's hidden account. Time Machine has no copy of Domovoi's settings, so they can't be brought back.") }.buttonStyle(.plain)
            if removeEverything {
                HStack {
                    Text("Type DELETE to confirm:")
                    TextField("", text: $typed).frame(width: 120)
                }
            }
            Text("Your media folder's files are never deleted.").font(.callout).foregroundStyle(.secondary)
            ErrorText(text: m.failure)
            Footer { EmptyView() } right: {
                Button("Uninstall...") { m.uninstall(removeEverything: removeEverything) }
                    .disabled(m.busy || (removeEverything && typed != "DELETE"))
            }
        }
    }
    func radio(_ on: Bool, _ title: String, _ detail: String) -> some View {
        HStack(alignment: .firstTextBaseline, spacing: 8) {
            Image(systemName: on ? "largecircle.fill.circle" : "circle").foregroundStyle(Color.accentColor)
            VStack(alignment: .leading, spacing: 2) {
                Text(title).bold()
                Text(detail).font(.callout).foregroundStyle(.secondary).fixedSize(horizontal: false, vertical: true)
            }
        }.contentShape(Rectangle())
    }
}

struct UninstalledPage: View {
    @EnvironmentObject var m: SetupModel
    var body: some View {
        VStack(alignment: .leading, spacing: 12) {
            Text(m.uninstallMessage.isEmpty ? "Domovoi is removed." : m.uninstallMessage).textSelection(.enabled).fixedSize(horizontal: false, vertical: true)
            Text("Your media folder and its files are where they were.").foregroundStyle(.secondary)
            Footer { EmptyView() } right: { Button("Quit") { NSApp.terminate(nil) }.keyboardShortcut(.defaultAction) }
        }
    }
}
