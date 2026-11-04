import Foundation
import Synchronization

/// Where the shell's saved state rests between launches: the app's is
/// `UserDefaults`, a test's is a box.
///
/// One blob rather than a key per field, for the reason Android's `Prefs`
/// keeps its JSON strings: the fields are written together and have to be
/// read back together — a half-applied write is a phone that trusts a server
/// it has no row for. One `set` is one write.
///
/// `@unchecked Sendable` with the reason, as the house rule asks, is on the
/// `UserDefaults` conformer below.
public protocol SavedState: Sendable {
    func read() -> Data?
    func write(_ blob: Data)
}

/// `UserDefaults`, which is the app's.
///
/// In this module rather than beside `DomovoiApp` so it is compiled and
/// tested by `ios/scripts/pkgtest.sh`, exactly as
/// ``DeviceIdentity/UserDefaultsStore`` is and for the same reason.
public struct UserDefaultsSavedState: SavedState, @unchecked Sendable {

    /// The shell's key. Named after the app so a shared suite could never
    /// collide, as ``DeviceIdentity/UserDefaultsStore/key`` is.
    public static let key = "domovoi.shell"

    /// Chat's pins, which are **this phone's** and not the household's
    /// (``ChatPinBook``).
    ///
    /// A second key under the same prefix rather than a field in the shell's
    /// blob: the shell's blob is one write of the server, the trust and the
    /// pins, and a chat pin has nothing to do with any of them — a tap on a
    /// pin rewriting the trust book is how a half-applied write comes to trust
    /// a server there is no row for.
    public static let chatPinsKey = "domovoi.chat.pins"

    private let defaults: UserDefaults
    private let key: String

    /// - Parameter key: which saved blob this is. Defaults to the shell's, so
    ///   every existing call site reads and writes exactly what it did before.
    public init(defaults: UserDefaults = .standard, key: String = UserDefaultsSavedState.key) {
        self.defaults = defaults
        self.key = key
    }

    public func read() -> Data? { defaults.data(forKey: key) }
    public func write(_ blob: Data) { defaults.set(blob, forKey: key) }
}

/// A box. For the tests, and for nothing else — the twin of
/// ``InMemoryKeychain``, so a test can hand one store's state to a second
/// store and watch it come back.
public final class SavedStateBox: SavedState {

    private let blob: Mutex<Data?>

    public init(_ blob: Data? = nil) {
        self.blob = Mutex(blob)
    }

    /// What is in there, for a test that wants to assert about the **saved
    /// bytes** rather than about a store's answers — "the household token is
    /// not in the plist" is a claim about these bytes and can be made no
    /// other way.
    public var saved: Data? { blob.withLock { $0 } }

    public func read() -> Data? { blob.withLock { $0 } }
    public func write(_ newValue: Data) { blob.withLock { $0 = newValue } }
}

/// The ``ShellStore`` that remembers: the server, the trust, the known-server
/// rows, the pins, the refusals, the phone bar and the "this phone" choice
/// survive a launch, and the household tokens rest in the Keychain.
///
/// ### What this replaces, and why it is a defect fix and not a feature
///
/// ``InMemoryShellStore`` was the only store there was, and
/// `DomovoiApp` built one on every launch — so the shipping app forgot its
/// server, its trust and its pins every time it was force-quit, and
/// ``TokenVault`` had **no caller at all**: `AppContainer` held one and
/// nothing ever read or wrote it. Kamron found the first half of that on his
/// own phone within a minute of the first install. Part 1 of I1-C said the
/// persisted store belonged to "the batch that has something to write"; parts
/// 2 and 3 wrote the picker, the trust dialogs and the pairing screens and
/// never came back for it.
///
/// ### The two places, and the line between them
///
/// **The Keychain holds the tokens and nothing else.** A household token is a
/// credential; ``TokenVault`` says at length what protection class it gets
/// and why, and this store is what finally puts it there.
///
/// **`UserDefaults` holds everything else**, as one JSON blob — and
/// ``TrustBook``'s own `CodingKeys` are what guarantee a token cannot reach
/// it, because `tokens` is not among them. The property is still on the book
/// in memory, because the forgetting cascade is the book's and has to be
/// ported whole; what this store does is make the Keychain agree with the
/// book after every write that touches one.
///
/// ### The order of the two writes, which depends on the direction
///
/// A token **arriving** goes to the plist first (see
/// ``persistAndToken(for:)``). A token **going** is deleted from the Keychain
/// first. The reason is in that method and it is not symmetric.
///
/// ### What it does not do
///
///  - It never enumerates the Keychain, because ``KeychainItems`` cannot: a
///    token for an address that is neither active nor a known row is not read
///    at launch and not deleted. That can only come from a crash between this
///    store's two writes, or from a reinstall over a Keychain that survived
///    one, and in both cases it is a value nothing reads. Naming it rather
///    than claiming it cannot happen.
///  - It holds no `themeMode`, no shared-screen writer and no owner writer,
///    for the reason the protocol gives: nothing reads them yet.
public final class SavedShellStore: ShellStore {

    /// The saved shape. **Every field is optional on the way in** (see
    /// ``init(from:)``): a blob written by an older build that is missing a
    /// key must leave the person's server saved, not throw and lose
    /// everything.
    struct Saved: Codable, Sendable {
        var book = TrustBook()
        var preferLocal = false
        var phoneBar: String?
        var localNetworkHasAnswered = false

        enum CodingKeys: String, CodingKey {
            case book
            case preferLocal = "prefer_local"
            case phoneBar = "phone_bar"
            case localNetworkHasAnswered = "local_network_has_answered"
        }

        init() {}

        init(from decoder: any Decoder) throws {
            let values = try decoder.container(keyedBy: CodingKeys.self)
            book = try values.decodeIfPresent(TrustBook.self, forKey: .book) ?? TrustBook()
            preferLocal = try values.decodeIfPresent(Bool.self, forKey: .preferLocal) ?? false
            phoneBar = try values.decodeIfPresent(String.self, forKey: .phoneBar)
            localNetworkHasAnswered =
                try values.decodeIfPresent(Bool.self, forKey: .localNetworkHasAnswered) ?? false
        }
    }

    private struct State {
        var saved = Saved()
        var onSelectionChange: (@Sendable () -> Void)?
    }

    private let state: Mutex<State>
    private let place: any SavedState
    private let vault: TokenVault

    /// Reads what was saved, and the tokens for the addresses that blob knows
    /// about, right here in `init` — so the first thing the shell asks is
    /// answered from disk and there is no window in which a saved install
    /// looks like a new one.
    public init(
        place: any SavedState = UserDefaultsSavedState(),
        vault: TokenVault = TokenVault()
    ) {
        self.place = place
        self.vault = vault

        var loaded = Saved()
        if let blob = place.read() {
            do {
                loaded = try JSONDecoder().decode(Saved.self, from: blob)
            } catch {
                // A blob this build cannot read at all. Starting blank is the
                // only thing left, and it is the state the app already has a
                // screen for; what it must not do is crash on launch, for
                // ever, on a phone whose owner cannot clear it.
                loaded = Saved()
            }
        }
        // The tokens are not in that blob and never were. Two sources are
        // asked, both of them written by this store: the rows, and the active
        // address — which is also a row for every install the app can reach,
        // and is asked separately because a restored-from-backup install is
        // the one that might not be.
        var addresses = Set(loaded.book.known.map(\.url))
        addresses.insert(loaded.book.serverURL)
        for url in addresses where !ServerCredentials.isBlank(url) {
            if let token = vault.token(for: url) { loaded.book.tokens[url] = token }
        }

        self.state = Mutex(State(saved: loaded))
    }

    // MARK: - saving

    /// Everything but the tokens, to the plist.
    ///
    /// An encode failure is a programming mistake — every field is a
    /// `Codable` standard type — so it trips an assertion in a debug build
    /// and is survived in a shipping one: the in-memory copy is still right,
    /// so the app keeps working and only forgets at the next launch.
    private func persist() {
        let saved = state.withLock { $0.saved }
        do {
            place.write(try JSONEncoder().encode(saved))
        } catch {
            assertionFailure("the shell's saved state would not encode: \(error)")
        }
    }

    /// The plist and the Keychain, both made to agree with the book, **in the
    /// order that is safe for the direction this address's token is going**.
    ///
    /// A token **arriving**: the plist first. A crash between the two then
    /// leaves a Keychain item for a server the plist still has a row for, and
    /// the next launch reads it straight back — nothing is lost and nothing
    /// is stranded.
    ///
    /// A token **going** (unpaired, a pin replaced, a server forgotten): the
    /// Keychain first. The other order would leave, for however long the
    /// process lives, a household credential for a server whose row is
    /// already gone — which nothing would ever read and nothing would ever
    /// delete, because this store cannot enumerate the Keychain. "Forget" has
    /// to mean it.
    ///
    /// **A token the vault refuses is dropped from memory too.** The vault is
    /// the authority on what can be kept (``TokenVault/save(token:for:)``
    /// refuses a value a header could not carry); a store that kept one in
    /// memory would work until the next launch and then be unpaired for
    /// reasons nobody could see.
    private func persistAndToken(for url: String) {
        guard !ServerCredentials.isBlank(url) else {
            persist()
            return
        }
        let wanted = state.withLock { $0.saved.book.token(for: url) }
        if wanted == nil {
            try? vault.forget(server: url)
            persist()
            return
        }
        persist()
        do {
            try vault.save(token: wanted, for: url)
        } catch {
            state.withLock { $0.saved.book.setToken(nil, for: url) }
            persist()
        }
    }

    /// **Outside the lock, always**, for the reason
    /// ``InMemoryShellStore`` gives: the shell's sink re-enters the store and
    /// `Mutex` is not recursive.
    private func selectionChanged() {
        let sink = state.withLock { $0.onSelectionChange }
        sink?()
    }

    // MARK: - reading

    public var serverURL: String { state.withLock { $0.saved.book.serverURL } }
    public var preferLocal: Bool { state.withLock { $0.saved.preferLocal } }
    public var phoneBar: String? { state.withLock { $0.saved.phoneBar } }
    public var trustedServers: Set<String> { state.withLock { $0.saved.book.trusted } }
    public var knownServers: [KnownServer] { state.withLock { $0.saved.book.known } }
    public var localNetworkHasAnswered: Bool {
        state.withLock { $0.saved.localNetworkHasAnswered }
    }

    public func isTrusted(_ url: String) -> Bool { state.withLock { $0.saved.book.isTrusted(url) } }

    /// The in-memory mirror, not the Keychain: this is asked on every request
    /// the app builds, and a `SecItem` round trip per request is not what the
    /// Keychain is for. The mirror is loaded in `init` and is written by
    /// nothing but the methods below.
    public func token(for url: String) -> String? {
        state.withLock { $0.saved.book.token(for: url) }
    }

    public func pinFor(_ key: String) -> ServerIdentity.Pin? {
        state.withLock { $0.saved.book.pins[key] }
    }

    /// False for every key, which is ``InMemoryShellStore``'s answer and the
    /// decision behind it: no iPhone install predates the trust dialog, so
    /// there is no upgraded install for Android's exception to be about.
    public func mayPinOnFirstProof(_ key: String) -> Bool { false }

    public func isTokenRejected(_ url: String) -> Bool {
        state.withLock { $0.saved.book.rejected.contains(ServerCredentials.normalize(url)) }
    }

    // MARK: - writing

    public func notifySelectionChanges(to sink: @escaping @Sendable () -> Void) {
        state.withLock { $0.onSelectionChange = sink }
    }

    public func setPreferLocal(_ value: Bool) {
        state.withLock { $0.saved.preferLocal = value }
        persist()
        selectionChanged()
    }

    public func setPhoneBar(_ raw: String?) {
        state.withLock { $0.saved.phoneBar = raw }
        persist()
    }

    public func setToken(_ token: String?, for url: String) {
        state.withLock { $0.saved.book.setToken(token, for: url) }
        persistAndToken(for: url)
    }

    public func pin(_ key: String, _ pin: ServerIdentity.Pin) {
        state.withLock { $0.saved.book.pins[key] = pin }
        persist()
    }

    public func markTokenRejected(_ url: String) {
        state.withLock { _ = $0.saved.book.rejected.insert(ServerCredentials.normalize(url)) }
        persist()
    }

    public func clear(_ url: String) {
        state.withLock { _ = $0.saved.book.rejected.remove(ServerCredentials.normalize(url)) }
        persist()
    }

    public func trustServer(_ url: String, identity: ServerIdentity.Pin? = nil) {
        state.withLock { $0.saved.book.trust(url, identity: identity) }
        persist()
    }

    public func setServerURL(_ url: String) -> Bool {
        // A refusal wrote nothing, so there is nothing to save and nobody to
        // tell — the same rule ``InMemoryShellStore/setServerURL(_:)`` keeps.
        let written = state.withLock { $0.saved.book.setServerURL(url) }
        if written {
            persist()
            selectionChanged()
        }
        return written
    }

    public func upsertKnownServer(url: String, name: String? = nil) {
        state.withLock { $0.saved.book.upsertKnown(url: url, name: name) }
        persist()
    }

    public func replacePin(url: String, pin newPin: ServerIdentity.Pin) {
        // The book drops the household token first (``TrustBook/replacePin(url:pin:)``
        // and the property finding S-T1 behind it), so the Keychain item goes
        // with it — which is this call, and is the half Android gets for free
        // by keeping both in one file.
        state.withLock { $0.saved.book.replacePin(url: url, pin: newPin) }
        persistAndToken(for: url)
    }

    @discardableResult
    public func removeKnownServer(_ url: String) -> TrustBook.ForgetReach {
        let reach = state.withLock { $0.saved.book.forget(url) }
        persistAndToken(for: url)
        return reach
    }

    @discardableResult
    public func forgetActiveServer() -> String? {
        let gone = state.withLock { $0.saved.book.forgetActiveServer()?.url }
        guard let gone else { return nil }
        persistAndToken(for: gone)
        selectionChanged()
        return gone
    }

    /// Set once and never cleared: something on the local network answered
    /// this install, so the Local Network permission is not what is wrong
    /// with the next empty sweep. See ``ShellStore/localNetworkHasAnswered``.
    public func noteLocalNetworkAnswered() {
        let changed = state.withLock { current -> Bool in
            guard !current.saved.localNetworkHasAnswered else { return false }
            current.saved.localNetworkHasAnswered = true
            return true
        }
        if changed { persist() }
    }
}
