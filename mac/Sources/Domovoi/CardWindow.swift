import AppKit
import DomovoiMacKit
import SwiftUI
import UniformTypeIdentifiers

// "Prepare an SD card...": the menu-bar icon's card helper, the twin of the
// Windows tray's (windows/tray/CardWizard.cs). Domovoi's Linux system on a
// Mac can't see SD cards, so the dashboard builds the card files as a zip
// and this window puts them on the card, exactly as a server that can see
// the card would (CardWriter).
//
// Steps: build the files in the dashboard; the zip (the newest in Downloads,
// or chosen); the card (only removable cards that look freshly flashed:
// CardDisks); the setup network password (fetched with the zip's one-time
// pass, CardKeys; this step shows only when that can't work); write: a
// plain-English confirmation naming the card and its size, then the write,
// read-back and eject (CardJob); done.
//
// All of it runs here, as the signed-in account, with no administrator
// prompt (owner decision 2026-11-03), as on Windows: the card is that
// account's own mounted volume. A standard account can prepare a card.
// The zip is unpacked into a private folder in this account's temporary
// folder, removed when the window closes; the setup password stays in
// memory.

final class CardWindowController: NSObject, NSWindowDelegate {
    let window: NSWindow
    let model: CardHelperModel
    var onClose: (() -> Void)?

    init(menu: MenuBarController) {
        let m = CardHelperModel(menu: menu)
        model = m
        let host = NSHostingController(rootView: CardHelperView().environmentObject(m).frame(width: 620, height: 480))
        window = NSWindow(contentViewController: host)
        super.init()
        window.title = "Prepare a room's SD card"
        window.styleMask = [.titled, .closable, .miniaturizable]
        window.isReleasedWhenClosed = false
        window.delegate = self
        window.center()
    }

    func show() {
        NSApp.setActivationPolicy(.regular)
        window.makeKeyAndOrderFront(nil)
        NSApp.activate(ignoringOtherApps: true)
    }

    /// Not while the card is being written.
    func windowShouldClose(_ sender: NSWindow) -> Bool { !model.writing }

    func windowWillClose(_ notification: Notification) {
        model.close()
        onClose?()
    }
}

enum CardTone { case plain, ok, warn, error }

final class CardHelperModel: ObservableObject {
    enum Step { case build, zip, card, password, write, done }

    weak var menu: MenuBarController?
    let disks = CardDisks(runner: ProcessRunner(), files: LocalCardFiles())
    let files = LocalCardFiles()
    let keys: CardKeys
    let webURL: String
    let webPort: Int
    let work = DispatchQueue(label: "domovoi.card")

    @Published var step: Step = .build
    @Published var zipPath: String?
    @Published var zipNote = ""
    @Published var zipTone: CardTone = .plain
    @Published var package: CardPackage?
    @Published var scan = CardScan()
    @Published var scanned = false
    @Published var chosenVolume: String?
    @Published var key: KeyAnswer?
    @Published var fetching = false
    @Published var typed = ""
    @Published var adminPassword = ""
    @Published var writing = false
    @Published var written = false
    @Published var pct = 0.0
    @Published var status = ""
    @Published var statusTone: CardTone = .plain
    @Published var done: CardJob.Result?

    /// The private folder of the zip in use: files/ (unpacked).
    var workDir: URL?
    var rescan: Timer?
    var scanning = false

    init(menu: MenuBarController) {
        self.menu = menu
        webURL = menu.webURL
        webPort = menu.probe.ports.web
        keys = HTTPCardKeys(webPort: menu.probe.ports.web)
        pickNewestZip()
    }

    var chosen: CardTarget? { scan.targets.first { $0.volume == chosenVolume } }
    var keyFetched: Bool { key?.ok ?? false }

    func open(_ path: String) {
        if let u = URL(string: webURL + path) { NSWorkspace.shared.open(u) }
    }

    // ─── steps ─────────────────────────────────────────────────────────

    func go(_ s: Step) {
        step = s
        rescan?.invalidate()
        rescan = nil
        if s == .card {
            refreshCards()
            rescan = Timer.scheduledTimer(withTimeInterval: 3, repeats: true) { [weak self] _ in self?.refreshCards() }
        }
        if s == .write && !written { status = ""; statusTone = .plain; pct = 0 }
    }

    var canGoNext: Bool {
        switch step {
        case .build: return true
        case .zip: return package != nil
        case .card: return chosen != nil && !fetching
        case .password: return CardSetupPassword.problem(typed) == nil
        case .write: return written && !writing
        case .done: return false
        }
    }

    var canGoBack: Bool { step != .build && step != .done && !writing && !fetching }

    func next() {
        guard canGoNext else { return }
        switch step {
        case .build: go(.zip)
        case .zip: go(.card)
        case .card: cardChosen()
        case .password: go(.write)
        case .write: go(.done)
        case .done: break
        }
    }

    func back() {
        guard canGoBack else { return }
        switch step {
        case .zip: go(.build)
        case .card: go(.zip)
        case .password: go(.card)
        case .write: go(package?.isPortal == true && !keyFetched ? .password : .card)
        default: break
        }
    }

    func close() {
        rescan?.invalidate()
        adminPassword = ""
        typed = ""
        key = nil
        if let w = workDir { try? FileManager.default.removeItem(at: w) }
        workDir = nil
    }

    // ─── the zip ───────────────────────────────────────────────────────

    func pickNewestZip() {
        guard let downloads = FileManager.default.urls(for: .downloadsDirectory, in: .userDomainMask).first else { return }
        if let z = CardZip.newest(in: downloads) {
            setZip(z)
        } else {
            zipNote = "No card files in your Downloads folder yet (a domovoi-satellite-overlay zip, or the folder Safari unpacked it to). Build them in the dashboard (step 1), or choose where you saved them."
            zipTone = .warn
        }
    }

    func chooseZip() {
        let p = NSOpenPanel()
        p.title = "Choose the card files from the dashboard"
        // The zip, or the folder Safari unpacked it to (Train 1 finding 24).
        p.allowedContentTypes = [.zip, .folder]
        p.allowsMultipleSelection = false
        p.canChooseDirectories = true
        p.directoryURL = FileManager.default.urls(for: .downloadsDirectory, in: .userDomainMask).first
        NSApp.activate(ignoringOtherApps: true)
        if p.runModal() == .OK, let u = p.url { setZip(u) }
    }

    func setZip(_ url: URL) {
        guard let menu else { return }
        zipPath = url.path
        package = nil
        key = nil
        chosenVolume = nil
        zipNote = "Reading the card files..."
        zipTone = .plain
        if let old = workDir { try? FileManager.default.removeItem(at: old) }
        workDir = nil
        let dir: URL
        do { dir = try menu.makeWorkDir() } catch { zipNote = "The card files couldn't be unpacked: \(error)."; zipTone = .error; return }
        workDir = dir
        let port = webPort
        work.async {
            var pkg: CardPackage?
            var note: String
            var tone: CardTone
            do {
                let files = dir.appendingPathComponent(CardJob.filesName, isDirectory: true)
                var isDir: ObjCBool = false
                if FileManager.default.fileExists(atPath: url.path, isDirectory: &isDir), isDir.boolValue {
                    try CardZip.copyFolder(url, into: files, runner: ProcessRunner())
                } else {
                    try CardZip.unpack(url, into: files, runner: ProcessRunner())
                }
                let src = try CardFolderSource(path: dir.appendingPathComponent(CardJob.filesName).path)
                let p = try CardZip.load(src)
                let f = DateFormatter()
                f.dateFormat = "d MMM yyyy, HH:mm"
                let saved = ((try? FileManager.default.attributesOfItem(atPath: url.path))?[.modificationDate] as? Date).map { ", saved " + f.string(from: $0) } ?? ""
                let whereFrom = "\n" + url.lastPathComponent + saved + "."
                switch CardZip.origin(zip: p.serverFingerprint, server: HTTPCardKeys.serverFingerprint(http: URLSessionHTTP(), webPort: port)) {
                case .otherServer:
                    note = "These card files were built by a different Domovoi server, not the one on this Mac, so a room made from them would never pair with it. Build them in this Mac's dashboard (step 1)." + whereFrom
                    tone = .error
                case .unknown:
                    pkg = p
                    note = p.summary() + whereFrom + "\nDomovoi on this Mac isn't answering, so the helper can't check these files came from it. Use them only if you downloaded them from its dashboard."
                    tone = .warn
                case .thisServer:
                    pkg = p
                    note = p.summary() + " Built by the Domovoi on this Mac." + whereFrom
                    tone = .ok
                }
            } catch let e as CardZipError {
                note = e.message
                tone = .error
            } catch {
                note = "Those card files couldn't be read: \(error)."
                tone = .error
            }
            DispatchQueue.main.async {
                guard self.workDir == dir else { return }   // another zip was picked meanwhile
                self.package = pkg
                self.zipNote = note
                self.zipTone = tone
            }
        }
    }

    // ─── the card ──────────────────────────────────────────────────────

    func refreshCards() {
        guard let pkg = package, !scanning, !writing else { return }
        scanning = true
        let marker = pkg.bootMarker
        work.async {
            let s = self.disks.scan(marker: marker)
            DispatchQueue.main.async {
                self.scanning = false
                self.scan = s
                self.scanned = true
                if self.chosen == nil { self.chosenVolume = s.targets.count == 1 ? s.targets[0].volume : nil }
            }
        }
    }

    var cardNote: String {
        if !scanned { return "Looking for cards..." }
        if scan.failed { return "macOS didn't list its disks (diskutil didn't answer). Click Refresh." }
        var t: String
        switch scan.targets.count {
        case 0: t = "No card found yet. Put the card in (the Mac's SD slot or a card reader); it shows up here by itself.\n"
            + "Just used Raspberry Pi Imager? It ejects the card when it finishes: take the card out and put it back in."
        case 1: t = "One card found."
        default: t = "\(scan.targets.count) cards found: pick the one to write."
        }
        if scan.others > 0 {
            t += "\n" + (scan.others == 1 ? "One other removable disk is" : "\(scan.others) other removable disks are") + " left out: not a freshly flashed Raspberry Pi card."
        }
        if scan.accessDenied {
            t += "\nmacOS hasn't let Domovoi look at a card yet. If it asked, choose Allow. Otherwise turn on Removable Volumes for Domovoi in System Settings > Privacy & Security > Files & Folders. (Test builds of Domovoi ask again after each update.)"
        }
        return t
    }

    func cardChosen() {
        guard let t = chosen, let pkg = package else { return }
        if let b = try? files.read(t.volume + "/cmdline.txt", max: 1 << 20), let why = CardChecks.cmdlineProblem(b, tokens: pkg.cmdlineTokens) {
            alert(why)
            return
        }
        if t.prepared {
            let a = NSAlert()
            a.messageText = "This card already has Domovoi's files on it."
            a.informativeText = "Write these card files over them? The card's other files stay as they are."
            a.addButton(withTitle: "Cancel")
            a.addButton(withTitle: "Write over them")
            NSApp.activate(ignoringOtherApps: true)
            guard a.runModal() == .alertSecondButtonReturn else { return }
        }
        chooseKeyStep()
    }

    func alert(_ text: String) {
        let a = NSAlert()
        a.messageText = "Prepare a room's SD card"
        a.informativeText = text
        a.alertStyle = .warning
        NSApp.activate(ignoringOtherApps: true)
        a.runModal()
    }

    // ─── the setup network's password ──────────────────────────────────

    /// A Wi-Fi setup card's password is fetched once for these card files;
    /// the password step appears only when that failed.
    func chooseKeyStep() {
        guard let pkg = package else { return }
        if !pkg.isPortal || keyFetched { go(.write); return }
        fetching = true
        work.async {
            let a = CardKeyFlow.fetch(self.keys, pkg, now: Date())
            DispatchQueue.main.async {
                self.fetching = false
                self.key = a
                self.go(a.ok ? .write : .password)
            }
        }
    }

    func retryFetch() {
        guard let pkg = package else { return }
        fetching = true
        work.async {
            let a = CardKeyFlow.fetch(self.keys, pkg, now: Date())
            DispatchQueue.main.async {
                self.fetching = false
                self.key = a
                if a.ok { self.go(.write) }
            }
        }
    }

    func signInAndFetch() {
        guard let pkg = package, !adminPassword.isEmpty else { return }
        let pw = adminPassword
        adminPassword = ""
        fetching = true
        work.async {
            let a = CardKeyFlow.fetchAsAdmin(self.keys, pkg, adminPassword: pw)
            DispatchQueue.main.async {
                self.fetching = false
                self.key = a
                if a.ok { self.go(.write) }
            }
        }
    }

    var typedNote: (String, CardTone) {
        if typed.isEmpty { return ("", .plain) }
        if let why = CardSetupPassword.problem(typed) { return (why, .error) }
        return ("That looks right.", .ok)
    }

    /// The console login made again with the password (the zip's is only a hash).
    var consoleLine: String {
        if let k = key, let u = k.consoleUser, let p = k.consolePassword, k.userconf != nil {
            return "New console login (it replaces the one shown before): \(u) / \(p)."
        }
        return "The console login couldn't be made again: the card keeps the one shown when it was built."
    }

    // ─── write ─────────────────────────────────────────────────────────

    var writeLead: String {
        guard let pkg = package, let t = chosen else { return "" }
        var s = CardPackage.mib(pkg.copyBytes) + " to copy" + (t.partition.free > 0 ? ", \(CardTarget.bytes(t.partition.free)) free on the card's boot partition." : ".")
            + " Domovoi writes the card, checks it and ejects it. Don't take the card out until this says it's done."
        if pkg.isPortal, keyFetched, let k = key, let p = k.psk {
            s += "\nDomovoi filled in the setup network password (\(p))." + (k.reissued ? " It's new: the dashboard's old one won't work." : "")
                + " You'll need it on your phone." + (k.reissued ? " " + consoleLine : "")
        }
        return s
    }

    func startWrite() {
        guard !writing, !written, let pkg = package, let t = chosen, let menu, let dir = workDir else { return }
        let a = NSAlert()
        a.messageText = t.confirmText()
        a.informativeText = "Nothing is formatted or deleted: config.txt and cmdline.txt get Domovoi's lines added, and a domovoi folder is added."
        a.addButton(withTitle: "Cancel")
        a.addButton(withTitle: "Write the card")
        NSApp.activate(ignoringOtherApps: true)
        guard a.runModal() == .alertSecondButtonReturn else { return }

        // The password stays in memory: handed straight to the writer.
        let psk: String? = pkg.isPortal ? (keyFetched ? key?.psk : CardSetupPassword.normalize(typed)) : nil
        let userconf: String? = keyFetched && key?.reissued == true ? key?.userconf : nil
        let src: CardFolderSource
        do { src = try CardFolderSource(path: dir.appendingPathComponent(CardJob.filesName).path) } catch {
            status = "The card files are gone from their folder. Pick them again (the zip or its unpacked folder)."
            statusTone = .error
            return
        }
        writing = true
        menu.busy.insert(.prepareCard)
        status = "Checking the card..."
        statusTone = .plain
        pct = 0
        menu.log.write("card: writing \(t.partition.id) (\(t.name))")
        let job = CardJob(runner: ProcessRunner())
        let volume = t.volume, partition = t.partition.id
        work.async {
            var result: CardJob.Result?
            var why = ""
            do {
                result = try job.run(pkg, from: src, volume: volume, partition: partition, psk: psk, userconf: userconf) { p, text in
                    DispatchQueue.main.async {
                        self.pct = Double(p)
                        self.status = text + "..."
                    }
                }
            } catch let e as CardWriteError {
                why = e.message
            } catch let e as CardZipError {
                why = e.message
            } catch {
                why = "Writing the card stopped: \(error)."
            }
            DispatchQueue.main.async { self.finished(result, why: why) }
        }
    }

    func finished(_ r: CardJob.Result?, why: String) {
        writing = false
        menu?.busy.remove(.prepareCard)
        if let d = r {
            done = d
            written = true
            pct = 100
            status = "Everything was written and checked (\(d.checked) checks). "
                + (d.ejected ? "The card is ejected: take it out and click Next."
                   : "macOS didn't eject it (\(d.ejectProblem ?? "no reason given")). Eject it in Finder's sidebar, or close any window showing the card, then take it out and click Next.")
            statusTone = .ok
            menu?.log.write("card: written and checked, " + (d.ejected ? "ejected" : "not ejected"))
        } else {
            status = why.hasSuffix(".") || why.hasSuffix(")") ? why : why + "."
            statusTone = .error
            menu?.log.write("card: " + status)
        }
    }

    var doneText: String {
        guard let pkg = package else { return "" }
        var t: String
        if pkg.isPortal {
            let pw: String
            if keyFetched, let k = key, let p = k.psk {
                pw = p + (k.reissued ? " (a new one: the password the dashboard showed before won't work)" : " (the dashboard shows it too)")
            } else {
                pw = "you typed here"
            }
            t = "Put the card in the Pi and switch it on. After a few minutes it raises its own Wi-Fi, \(pkg.apSsid ?? ""): join it from your phone, with the password \(pw), and follow the page that opens. Then approve the new room in Settings > Rooms."
        } else {
            t = "This card is set up over USB, which a Domovoi on a Mac can't do: in the dashboard, prepare the card again with the Wi-Fi setup portal."
        }
        if keyFetched && key?.reissued == true { t += "\n\n" + consoleLine }
        return t + "\n\n" + pkg.jobLine() + "."
    }

    func another() {
        written = false
        done = nil
        chosenVolume = nil
        go(.card)
    }
}

// ─── the window ────────────────────────────────────────────────────────

struct CardHelperView: View {
    @EnvironmentObject var m: CardHelperModel

    var body: some View {
        VStack(alignment: .leading, spacing: 0) {
            VStack(alignment: .leading, spacing: 6) {
                Text(title).font(.title2).bold()
                if !lead.isEmpty { Text(lead).foregroundStyle(.secondary).fixedSize(horizontal: false, vertical: true) }
            }
            .padding(.horizontal, 24).padding(.top, 20).padding(.bottom, 12)
            Divider()
            ScrollView {
                page.frame(maxWidth: .infinity, alignment: .topLeading).padding(24)
            }
            Divider()
            HStack {
                Spacer()
                if m.step != .done {
                    Button("Back") { m.back() }.disabled(!m.canGoBack)
                    Button("Next") { m.next() }.keyboardShortcut(.defaultAction).disabled(!m.canGoNext)
                }
                Button(m.step == .done ? "Close" : "Cancel") { NSApp.keyWindow?.performClose(nil) }.disabled(m.writing)
            }
            .padding(16)
        }
    }

    var title: String {
        switch m.step {
        case .build: return "Prepare a room's SD card"
        case .zip: return "The card files"
        case .card: return "The card"
        case .password: return "The setup network password"
        case .write: return "Write the card"
        case .done: return "The card is ready"
        }
    }

    var lead: String {
        switch m.step {
        case .build: return "Domovoi runs in a Linux system on this Mac, which can't see SD cards, so this helper puts the card files on the card for it."
        case .zip: return "Pick the zip you downloaded from the dashboard, or the folder Safari unpacked it to."
        case .card: return "Put the freshly flashed card in this Mac (its SD slot or a card reader). Only removable cards that look like a new Raspberry Pi card are listed: never a disk inside the Mac."
        case .password: return "This card raises its own Wi-Fi so you can set it up from your phone. The helper couldn't get that network's password from Domovoi by itself."
        case .write: return m.chosen.map { "Domovoi's files go onto \u{201C}\($0.name)\u{201D}. Nothing is formatted or deleted: config.txt and cmdline.txt get Domovoi's lines added, and a domovoi folder is added." } ?? ""
        case .done: return ""
        }
    }

    @ViewBuilder var page: some View {
        switch m.step {
        case .build: CardBuildPage()
        case .zip: CardZipPage()
        case .card: CardPickPage()
        case .password: CardPasswordPage()
        case .write: CardWritePage()
        case .done: CardDonePage()
        }
    }
}

func toneColor(_ t: CardTone) -> Color {
    switch t {
    case .plain: return .primary
    case .ok: return .green
    case .warn: return .orange
    case .error: return .red
    }
}

struct CardBuildPage: View {
    @EnvironmentObject var m: CardHelperModel
    var body: some View {
        VStack(alignment: .leading, spacing: 12) {
            Text("1.  Flash a microSD card with Raspberry Pi OS Lite (64-bit), using Raspberry Pi Imager. When Imager offers to apply OS customisation settings, choose No: Domovoi sets the card up itself.")
            Text("2.  Build the card files in the dashboard: Settings > Rooms > prepare a room's SD card. Pick the board and the microphone, click Prepare, and download the files when the build is done.")
            Text("3.  This helper fills in the card's setup network password by itself, from Domovoi on this Mac. You'll still need that password on your phone: the dashboard shows it.")
            Button("Open Settings > Rooms") { m.open("/#settings/rooms/media") }
            Text("Already have the files? Click Next.").foregroundStyle(.secondary)
        }
        .fixedSize(horizontal: false, vertical: true)
    }
}

struct CardZipPage: View {
    @EnvironmentObject var m: CardHelperModel
    var body: some View {
        VStack(alignment: .leading, spacing: 12) {
            Text("The zip the dashboard built (domovoi-satellite-overlay.zip), or the folder of the same name that Safari unpacks it to. The newest one in your Downloads folder is picked for you.")
            HStack {
                Text(m.zipPath.map { ($0 as NSString).lastPathComponent } ?? "No card files picked").lineLimit(1).truncationMode(.middle)
                    .frame(maxWidth: .infinity, alignment: .leading)
                Button("Choose...") { m.chooseZip() }
            }
            Text(m.zipNote).foregroundStyle(toneColor(m.zipTone)).fixedSize(horizontal: false, vertical: true)
        }
    }
}

struct CardPickPage: View {
    @EnvironmentObject var m: CardHelperModel
    var body: some View {
        VStack(alignment: .leading, spacing: 10) {
            ForEach(m.scan.targets, id: \.volume) { t in
                Button { m.chosenVolume = t.volume } label: {
                    HStack(alignment: .top, spacing: 8) {
                        Image(systemName: m.chosenVolume == t.volume ? "largecircle.fill.circle" : "circle")
                        VStack(alignment: .leading, spacing: 2) {
                            Text(t.title())
                            Text("boot partition \(CardTarget.bytes(t.partition.size)), \(CardTarget.bytes(t.partition.free)) free \u{00B7} "
                                 + (t.prepared ? "already has Domovoi's files" : "freshly flashed"))
                                .font(.caption).foregroundStyle(t.prepared ? Color.orange : Color.secondary)
                        }
                        Spacer()
                    }
                    .contentShape(Rectangle())
                }
                .buttonStyle(.plain)
            }
            HStack(alignment: .top) {
                Text(m.cardNote).foregroundStyle(m.scanned && m.scan.targets.isEmpty ? Color.orange : Color.primary)
                    .fixedSize(horizontal: false, vertical: true)
                Spacer()
                Button("Refresh") { m.refreshCards() }
            }
            if m.scan.accessDenied {
                Button("Open Privacy & Security") {
                    if let u = URL(string: "x-apple.systempreferences:com.apple.preference.security?Privacy_FilesAndFolders") { NSWorkspace.shared.open(u) }
                }
            }
            if m.fetching { ProgressView("Asking Domovoi for the setup network password...").controlSize(.small) }
        }
    }
}

struct CardPasswordPage: View {
    @EnvironmentObject var m: CardHelperModel
    var body: some View {
        VStack(alignment: .leading, spacing: 10) {
            if let k = m.key, !k.ok {
                Text(CardKeyWords.reason(k)).bold().foregroundStyle(.red)
                Text(CardKeyWords.nextStep(k)).fixedSize(horizontal: false, vertical: true)
                HStack {
                    if CardKeyWords.offersSignIn(k) {
                        SecureField("Dashboard admin password", text: $m.adminPassword).frame(width: 220)
                        Button("Sign in and fetch it") { m.signInAndFetch() }.disabled(m.adminPassword.isEmpty || m.fetching)
                    }
                    if CardKeyWords.offersRetry(k) { Button("Try again") { m.retryFetch() }.disabled(m.fetching) }
                }
            }
            if m.fetching { ProgressView().controlSize(.small) }
            HStack {
                Text("Setup network").frame(width: 120, alignment: .leading)
                Text(m.package?.apSsid ?? "").textSelection(.enabled)
            }
            HStack {
                Text("Or type it").frame(width: 120, alignment: .leading)
                TextField("ten characters", text: $m.typed).font(.system(.body, design: .monospaced)).frame(width: 220)
            }
            Text(m.typedNote.0).foregroundStyle(toneColor(m.typedNote.1)).padding(.leading, 128)
            Text("The dashboard showed it when it built these files (Settings > Rooms > prepare a room's SD card > show setup details). It's ten characters: lower-case letters and the digits 2 to 9.")
                .foregroundStyle(.secondary).fixedSize(horizontal: false, vertical: true)
            if let p = m.package { Text(p.jobLine() + ".").font(.caption).foregroundStyle(.secondary) }
        }
    }
}

struct CardWritePage: View {
    @EnvironmentObject var m: CardHelperModel
    var body: some View {
        VStack(alignment: .leading, spacing: 12) {
            Text(m.writeLead).fixedSize(horizontal: false, vertical: true)
            Button(m.statusTone == .error || m.statusTone == .warn ? "Try again" : "Write to the card") { m.startWrite() }
                .disabled(m.writing || m.written)
            ProgressView(value: m.pct, total: 100)
            Text(m.status).foregroundStyle(toneColor(m.statusTone)).fixedSize(horizontal: false, vertical: true)
            if let d = m.done {
                Text("written: " + d.written.joined(separator: ", ")).font(.caption).foregroundStyle(.secondary).fixedSize(horizontal: false, vertical: true)
            }
        }
    }
}

struct CardDonePage: View {
    @EnvironmentObject var m: CardHelperModel
    var body: some View {
        VStack(alignment: .leading, spacing: 12) {
            Text(m.doneText).fixedSize(horizontal: false, vertical: true)
            Button("Open Settings > Rooms") { m.open("/#settings/rooms") }
            Button("Prepare another card") { m.another() }
        }
    }
}
