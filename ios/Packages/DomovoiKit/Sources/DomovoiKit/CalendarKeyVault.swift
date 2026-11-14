import Foundation

/// The per-phone calendar key each server hands out (V057, main `8769500d`):
/// `PUT /api/calendar/phones/{device_id}` answers `calendar_key` once, on
/// the first snapshot it stores, and every later PUT and DELETE must carry it
/// as `X-Domovoi-Calendar-Key` or the house answers 403 — so only the phone
/// that shares its calendars can change them, whatever device id another
/// client asserts.
///
/// **In the Keychain, per server**, under its own service, with the
/// household token's protections (``SystemKeychain``):
/// `kSecAttrAccessibleAfterFirstUnlockThisDeviceOnly` and not
/// synchronisable, so it never reaches iCloud Keychain or another phone.
/// Never logged, never shown. Forgetting the server forgets it.
public struct CalendarKeyVault: Sendable {

    /// The service the keys are filed under — not the household token's.
    public static let service = "org.codersfarm.domovoi.calendar-key"

    /// The header it travels in (`calendar_feeds.CALENDAR_KEY_HEADER`).
    public static let header = "X-Domovoi-Calendar-Key"

    private let items: any KeychainItems

    public init(items: any KeychainItems = SystemKeychain(service: CalendarKeyVault.service)) {
        self.items = items
    }

    static func account(_ server: String) -> String { ServerCredentials.normalize(server) }

    /// The key held for `server`, or nil.
    public func key(for server: String) -> String? {
        let account = Self.account(server)
        guard !account.isEmpty, let data = try? items.get(account: account),
              let text = String(data: data, encoding: .utf8)
        else { return nil }
        return Self.storable(text)
    }

    /// Keep a key the server just handed out. One a header could not carry
    /// is not kept.
    public func save(_ key: String, for server: String) {
        let account = Self.account(server)
        guard !account.isEmpty, let clean = Self.storable(key), let data = clean.data(using: .utf8) else { return }
        try? items.set(data, account: account)
    }

    public func forget(server: String) {
        let account = Self.account(server)
        guard !account.isEmpty else { return }
        try? items.remove(account: account)
    }

    /// Printable ASCII, no spaces, at most the 200 characters the server
    /// reads (`calendar.py` `_calendar_key`); nil otherwise.
    static func storable(_ key: String) -> String? {
        let t = key.trimmingCharacters(in: .whitespacesAndNewlines)
        guard !t.isEmpty, t.count <= 200,
              t.unicodeScalars.allSatisfy({ (u: Unicode.Scalar) -> Bool in u.value > 0x20 && u.value < 0x7F })
        else { return nil }
        return t
    }
}
