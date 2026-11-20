import Foundation

/// Whether one of Domovoi's ports is taken, for Setup's "Checking this Mac".
///
/// A plain bind on every address is refused for a while after the port was
/// last used, even with nothing listening: a connection closed moments ago
/// lingers in TIME_WAIT (about 30 seconds on macOS). Sitting 3 met it
/// straight after an uninstall, with the dashboard still open in a browser
/// tab: the ports were free by the time anyone looked, but Setup had shown
/// "another program holds port 6369" (S3-9). So a refusal that SO_REUSEADDR
/// gets past, with nothing answering on 127.0.0.1, is looked at again for
/// up to `settle` before it counts.
///
/// That same pattern is also what a program listening on one address only
/// (not every address, and not 127.0.0.1) looks like, so it is never
/// waved through: after `settle` it counts as taken, as before.
public enum PortCheck {
    public struct Probe {
        /// A plain bind on every address is refused (EADDRINUSE).
        public var bindRefused: (Int) -> Bool
        /// The same bind with SO_REUSEADDR is refused too.
        public var reuseBindRefused: (Int) -> Bool
        /// Something answers a connect to 127.0.0.1.
        public var answersLocally: (Int) -> Bool

        public init(bindRefused: @escaping (Int) -> Bool, reuseBindRefused: @escaping (Int) -> Bool, answersLocally: @escaping (Int) -> Bool) {
            self.bindRefused = bindRefused
            self.reuseBindRefused = reuseBindRefused
            self.answersLocally = answersLocally
        }

        public static let real = Probe(bindRefused: { Preflight.bindRefused($0, reuse: false) },
                                       reuseBindRefused: { Preflight.bindRefused($0, reuse: true) },
                                       answersLocally: { Preflight.answersLocally($0) })
    }

    /// How long a refusal that may be a lingering connection is looked at
    /// again (TIME_WAIT is 2 x net.inet.tcp.msl, 30 s by default), and how
    /// often.
    public static let settle: TimeInterval = 40
    public static let every: TimeInterval = 5

    public static func inUse(_ port: Int, probe: Probe, wait: (TimeInterval) -> Void,
                             settle: TimeInterval = PortCheck.settle, every: TimeInterval = PortCheck.every) -> Bool {
        if probe.answersLocally(port) { return true }
        if !probe.bindRefused(port) { return false }
        if probe.reuseBindRefused(port) { return true }
        var waited: TimeInterval = 0
        while waited < settle {
            wait(every)
            waited += every
            if probe.answersLocally(port) { return true }
            if !probe.bindRefused(port) { return false }
        }
        return true
    }
}
