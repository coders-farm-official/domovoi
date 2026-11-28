#if canImport(DomovoiTorFFI)
import DomovoiTorFFI
import Foundation

/// Away chat's Tor on the iPhone: Arti, from the shared Rust crate
/// native/domovoi-tor (the same code as Android's libdomovoi_tor.so), linked
/// in as ios/Vendor/domovoi-tor/DomovoiTor.xcframework. Its three C calls
/// (`domovoi_tor.h`) as an ``AwayTorEngine``; ``EngineAwayTransport`` runs
/// them off the main actor and owns the streams' fds.
///
/// One Tor client per chat-screen session: ``start(stateDir:cacheDir:authDir:)``
/// bootstraps it (blocking, up to two minutes; a warm start, with the state
/// and cache kept from last time, is much quicker), ``open(onion:port:)``
/// hands back one end of a Unix socket pair the crate pipes to a Tor stream,
/// and ``stop()`` drops the client. Results are the crate's: `0` or a file
/// descriptor, else a negative `DTOR_ERR_*` (``DomovoiTorEngine/Failure``).
public struct DomovoiTorEngine: AwayTorEngine {
    /// The crate's negative results (`src/lib.rs`, `ERR_*`).
    public enum Failure: Int32, Sendable {
        case failed = -1
        case notStarted = -2
        case notFound = -3
        case clientAuth = -4
        case timeout = -5
        case panic = -6
    }

    /// Where Arti stops checking folder permissions. It checks that nobody
    /// else can write its folders, all the way up the path; on the iPhone
    /// everything below the app's own container is the app's alone, and the
    /// system's folders above it are not the app's to vouch for, so the check
    /// stops at the container (everything under it is still checked), as
    /// Android's stops at its files folder. Not "trust everyone".
    private let trustedPrefix: String?

    public init(trustedPrefix: String? = NSHomeDirectory()) {
        self.trustedPrefix = trustedPrefix
    }

    public func start(stateDir: String, cacheDir: String, authDir: String) -> Int32 {
        if let trustedPrefix {
            return trustedPrefix.withCString { prefix in dtor_start(stateDir, cacheDir, authDir, prefix) }
        }
        return dtor_start(stateDir, cacheDir, authDir, nil)
    }

    public func open(onion: String, port: Int32) -> Int32 {
        guard let port = UInt16(exactly: port) else { return Failure.failed.rawValue }
        return dtor_open(onion, port)
    }

    public func stop() {
        dtor_stop()
    }
}
#endif
