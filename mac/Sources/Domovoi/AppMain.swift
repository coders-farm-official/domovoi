import AppKit
import Darwin
import DomovoiMacKit
import SwiftUI

// Domovoi.app: one program, two ways in (mac-installer PLAN 4.9).
// - Opened from Applications (or Finder): the setup pages, in a window;
//   closing it quits.
// - Started by the menu-bar LaunchAgent with --menu-bar, in every account's
//   session: the icon in the menu bar, no Dock icon. Opening Domovoi.app
//   while it runs reaches this same process (macOS reopens the running app),
//   which then shows the setup pages in a window, and brings a hidden icon
//   back; closing the window leaves the icon.
// It never runs as root: what needs an administrator goes through the
// system's prompt to the helper (SetupModel, MenuBarController).

@main
enum DomovoiMain {
    /// NSApplication holds its delegate weakly.
    static var controller: AppController?

    static func main() {
        let app = NSApplication.shared
        let c = AppController(menuBarMode: CommandLine.arguments.contains("--menu-bar"))
        controller = c
        app.delegate = c
        app.run()
    }
}

final class AppController: NSObject, NSApplicationDelegate, NSWindowDelegate {
    let menuBarMode: Bool
    var setupWindow: NSWindow?
    var setupModel = SetupModel()
    var menuBar: MenuBarController?

    init(menuBarMode: Bool) {
        self.menuBarMode = menuBarMode
    }

    func applicationDidFinishLaunching(_ notification: Notification) {
        NSApp.mainMenu = Self.mainMenu(menuBarMode: menuBarMode)
        // Read now, while the app is on disk: an uninstall deletes it, and
        // Setup's last page still draws the header (S2-19).
        AppIcon.preload()
        if menuBarMode {
            // One icon per account: a second start (the agent loaded again
            // by Setup) ends at once, with exit 0 (launchd leaves it ended).
            guard SingleInstance.take() else { exit(0) }
            NSApp.setActivationPolicy(.accessory)
            let m = MenuBarController(app: self)
            menuBar = m
            m.start()
        } else {
            NSApp.setActivationPolicy(.regular)
            showSetup()
        }
    }

    func applicationShouldHandleReopen(_ sender: NSApplication, hasVisibleWindows flag: Bool) -> Bool {
        menuBar?.showIcon()
        showSetup()
        return false
    }

    func applicationShouldTerminateAfterLastWindowClosed(_ sender: NSApplication) -> Bool { !menuBarMode }

    func showSetup() {
        if setupWindow == nil {
            // Resizable, and taller than before (Train 1 finding 4: the
            // consent list was a four-line strip in a fixed 600-point window).
            let host = NSHostingController(rootView: SetupView().environmentObject(setupModel)
                .frame(minWidth: 680, idealWidth: 720, maxWidth: .infinity, minHeight: 600, idealHeight: 760, maxHeight: .infinity))
            host.sizingOptions = [.minSize]
            let w = NSWindow(contentViewController: host)
            w.title = "Domovoi Setup"
            w.styleMask = [.titled, .closable, .miniaturizable, .resizable]
            w.setContentSize(NSSize(width: 720, height: min(760, (NSScreen.main?.visibleFrame.height ?? 800) - 60)))
            w.isReleasedWhenClosed = false
            w.delegate = self
            w.center()
            setupWindow = w
        }
        if menuBarMode { NSApp.setActivationPolicy(.regular) }
        setupWindow?.makeKeyAndOrderFront(nil)
        NSApp.activate(ignoringOtherApps: true)
    }

    func windowWillClose(_ notification: Notification) {
        guard menuBarMode, (notification.object as? NSWindow) === setupWindow else { return }
        setupWindow = nil
        // A fresh check the next time, unless Setup is still working (its
        // page shows how the work goes when the window opens again).
        if !setupModel.busy && setupModel.page != .working { setupModel = SetupModel() }
        // The card helper's window keeps the app in the Dock while it's open.
        if menuBar?.cardWindow == nil { NSApp.setActivationPolicy(.accessory) }
    }

    /// The setup pages' TextField needs Edit's shortcuts (Type DELETE...).
    static func mainMenu(menuBarMode: Bool) -> NSMenu {
        let main = NSMenu()
        let appItem = NSMenuItem()
        let appMenu = NSMenu()
        if menuBarMode {
            appMenu.addItem(withTitle: "Close Window", action: #selector(NSWindow.performClose(_:)), keyEquivalent: "w")
        } else {
            appMenu.addItem(withTitle: "Quit Domovoi Setup", action: #selector(NSApplication.terminate(_:)), keyEquivalent: "q")
        }
        appItem.submenu = appMenu
        main.addItem(appItem)
        let editItem = NSMenuItem()
        let edit = NSMenu(title: "Edit")
        edit.addItem(withTitle: "Cut", action: #selector(NSText.cut(_:)), keyEquivalent: "x")
        edit.addItem(withTitle: "Copy", action: #selector(NSText.copy(_:)), keyEquivalent: "c")
        edit.addItem(withTitle: "Paste", action: #selector(NSText.paste(_:)), keyEquivalent: "v")
        edit.addItem(withTitle: "Select All", action: #selector(NSText.selectAll(_:)), keyEquivalent: "a")
        editItem.submenu = edit
        main.addItem(editItem)
        return main
    }
}

/// The app's icon for Setup's header, read once into memory from the
/// bundle's AppIcon.icns (NSApp.applicationIconImage as the fallback). The
/// pages never go back to the bundle for it: Uninstall deletes the bundle
/// while this process still shows the final page (S2-19).
enum AppIcon {
    static let image: NSImage = {
        if let u = Bundle.main.url(forResource: "AppIcon", withExtension: "icns"), let d = try? Data(contentsOf: u), let i = NSImage(data: d) {
            return i
        }
        return NSApp.applicationIconImage ?? NSImage(size: NSSize(width: 40, height: 40))
    }()

    static func preload() { _ = image }
}

/// One menu-bar icon per account: a lock in that account's own
/// ~/Library/Application Support/Domovoi, held while the icon runs.
enum SingleInstance {
    static var fd: Int32 = -1

    static func take() -> Bool {
        guard let base = FileManager.default.urls(for: .applicationSupportDirectory, in: .userDomainMask).first else { return true }
        let dir = base.appendingPathComponent("Domovoi", isDirectory: true)
        try? FileManager.default.createDirectory(at: dir, withIntermediateDirectories: true, attributes: [.posixPermissions: 0o700])
        let f = open(dir.appendingPathComponent("menubar.lock").path, O_RDWR | O_CREAT | O_NOFOLLOW, 0o600)
        guard f >= 0 else { return true }   // can't tell: better an icon than none
        guard flock(f, LOCK_EX | LOCK_NB) == 0 else { close(f); return false }
        fd = f
        return true
    }
}
