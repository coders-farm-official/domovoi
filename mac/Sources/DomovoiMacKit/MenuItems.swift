import Foundation

/// What a menu item does (the app maps each to its handler).
public enum MenuAction: String, Equatable {
    case openDashboard, openManual, openLoginItems, keepAwake, startAfterPowerFailure, saveLogs, restart, update, openSetup, hideIcon, openOnNetwork, copyAddress, prepareCard
}

/// One line of the menu-bar icon's menu, before AppKit sees it: the menu is
/// built here, from the verdict and the facts, so the tests can read it.
public struct MenuEntry: Equatable {
    public enum Kind: Equatable { case header, info, item, separator }
    public var kind: Kind
    public var title: String = ""
    public var action: MenuAction?
    public var enabled = true
    public var checked = false
    public var bold = false
    /// The address an Open on this network / Copy address item uses.
    public var payload: String?

    static let separator = MenuEntry(kind: .separator)
    static func info(_ t: String) -> MenuEntry { MenuEntry(kind: .info, title: t, enabled: false) }
    static func item(_ t: String, _ a: MenuAction, enabled: Bool = true, checked: Bool = false, bold: Bool = false, payload: String? = nil) -> MenuEntry {
        MenuEntry(kind: .item, title: t, action: a, enabled: enabled, checked: checked, bold: bold, payload: payload)
    }
}

/// The Mac-side update's menu item (Phase 6 fills this in: mac-installer
/// PLAN 4.12). With no offer there is no "Update Domovoi..." item at all:
/// a control that can't do what it says is hidden, not shown greyed.
public struct UpdateOffer: Equatable {
    /// "Update Domovoi...", "Finish updating Domovoi...", "Updating Domovoi...".
    public var title: String
    public var enabled: Bool
    public var bold: Bool

    public init(title: String, enabled: Bool = true, bold: Bool = false) {
        self.title = title
        self.enabled = enabled
        self.bold = bold
    }
}

/// The seam Phase 6 implements: the menu asks it, on every poll, what to
/// offer and whether there is something to tell the household; a click on
/// its item calls `start`. The menu-bar app holds an optional one; until
/// Phase 6 sets it, the menu has no update item and posts nothing for it.
public protocol MacSideUpdates: AnyObject {
    /// Called on the icon's poll queue (never the main thread).
    func poll()
    func offer() -> UpdateOffer?
    /// A notification to post once, or nil.
    func takeNotice() -> (title: String, body: String)?
    /// The item was clicked (on the main thread).
    func start()
}

public enum MenuItems {
    public static let manualPath = "/#manual"

    /// `busy`: actions under way (their items read "...ing" and are off).
    /// `powerFailure`: nil (no item) unless its feature gate is on and this
    /// is a desktop Mac (PowerFailureItem.decide).
    /// `cardHelper`: this Mac has what "Prepare an SD card..." needs (the
    /// root helper and the system tools, CardDisks.available); without it
    /// there is no such item.
    public static func build(verdict v: MenuVerdict, attention: [Attention], keepAwake: KeepAwakeItem?, update: UpdateOffer?,
                             lanURLs: [String] = [], busy: Set<MenuAction> = [], powerFailure: PowerFailureItem? = nil,
                             cardHelper: Bool = false) -> [MenuEntry] {
        var m: [MenuEntry] = []
        m.append(MenuEntry(kind: .header, title: v.line, action: v.state == .notInstalled ? .openSetup : .openDashboard, bold: true))
        if let d = v.detail { m.append(.info(d)) }
        if v.state == .notInstalled {
            m.append(.separator)
            m.append(.item("Open Domovoi Setup...", .openSetup, bold: true))
            m.append(.separator)
            m.append(.item("Hide this icon", .hideIcon))
            return m
        }
        for a in attention { m.append(.info(a.text)) }
        if attention.contains(where: \.opensLoginItems) { m.append(.item("Open Login Items Settings...", .openLoginItems)) }
        m.append(.separator)

        m.append(.item("Open Domovoi", .openDashboard, bold: true))
        // The Mac's address on the home network: only once other devices can
        // reach Domovoi through it (the networking phase gives the list).
        for u in lanURLs {
            m.append(.item(lanURLs.count == 1 ? "Open on this network (\(host(u)))" : "Open on this network: \(host(u))", .openOnNetwork, payload: u))
        }
        if let first = lanURLs.first { m.append(.item("Copy address", .copyAddress, payload: first)) }
        m.append(.item("User manual", .openManual))
        m.append(.separator)

        // Where the Windows tray has it: its own group under the manual.
        if cardHelper {
            m.append(.item(busy.contains(.prepareCard) ? "Writing an SD card..." : "Prepare an SD card...", .prepareCard))
            m.append(.separator)
        }

        if let k = keepAwake {
            m.append(.item(busy.contains(.keepAwake) ? "Changing the sleep setting..." : "Keep this Mac awake", .keepAwake,
                           enabled: k.enabled && !busy.contains(.keepAwake), checked: k.checked))
            m.append(.info(k.line))
        }
        if let p = powerFailure {
            let a = MenuAction.startAfterPowerFailure
            m.append(.item(busy.contains(a) ? "Changing the power setting..." : "Start after a power failure", a,
                           enabled: p.enabled && !busy.contains(a), checked: p.checked))
            m.append(.info(p.line))
        }
        if keepAwake != nil || powerFailure != nil { m.append(.separator) }

        m.append(.item(busy.contains(.saveLogs) ? "Saving logs..." : "Save logs for support...", .saveLogs, enabled: !busy.contains(.saveLogs)))
        if let u = update { m.append(.item(u.title, .update, enabled: u.enabled, bold: u.bold)) }
        m.append(.item(busy.contains(.restart) ? "Restarting Domovoi..." : "Restart Domovoi...", .restart, enabled: !busy.contains(.restart)))
        m.append(.item("Repair or Uninstall Domovoi...", .openSetup))
        m.append(.separator)
        m.append(.item("Hide this icon", .hideIcon))
        return m
    }

    static func host(_ url: String) -> String { URL(string: url)?.host ?? url }
}
