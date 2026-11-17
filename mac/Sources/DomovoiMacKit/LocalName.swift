import Foundation
import dnssd
import SystemConfiguration

/// `domovoi.local` on the home network (mac-installer PLAN 4.2, owner answer
/// Q19: published "only when no other device on the network already answers
/// for it"). The dashboard's kiosk default and the docs use that name;
/// satellites don't need it (they sweep the /24, satellite/discovery.py).
///
/// The helper asks the Mac's own mDNSResponder to answer for the name with
/// the Mac's LAN address, as a *unique* record: mDNSResponder first probes
/// the network, and when another device already answers for the name the
/// registration fails with a name conflict and nothing is published. A
/// device that claims the name later wins the same way (the callback reports
/// the conflict and the helper stops). A Mac whose own name is already
/// "domovoi" answers for it anyway, so nothing is added. The record goes
/// with the helper's process: uninstalling (or the helper stopping) takes it
/// off the network.
public enum LocalNamePolicy {
    public static let name = "domovoi.local."

    public enum Decision: Equatable {
        case publish
        /// This Mac's own Bonjour name is "domovoi": it answers already.
        case thisMac
        /// No home-network address to give.
        case off
    }

    public static func decide(localHostName: String?, lan: LanAddress?) -> Decision {
        if localHostName?.lowercased() == "domovoi" { return .thisMac }
        guard let lan, HostFiles.usable(lan.ipv4), IPv4.isPrivate(lan.ipv4), lan.interface != nil else { return .off }
        return .publish
    }

    /// The Mac's Bonjour name (System Settings > General > Sharing > Local
    /// hostname), without ".local".
    public static func currentLocalHostName() -> String? {
        SCDynamicStoreCopyLocalHostName(nil) as String?
    }

    /// How long to leave the name alone after another device took it.
    public static let retryAfterConflict: Double = 3600
}

/// The registration itself (dnssd). Used from one dispatch queue.
public final class LocalNamePublisher {
    public private(set) var state = "off"
    var conn: DNSServiceRef?
    var record: DNSRecordRef?
    var published: LanAddress?
    var conflictAt: Date?
    let queue: DispatchQueue
    let log: (String) -> Void

    public init(queue: DispatchQueue, log: @escaping (String) -> Void) {
        self.queue = queue
        self.log = log
    }

    /// Publishes, moves or withdraws the record for `lan`.
    public func update(lan: LanAddress?, now: Date = Date()) {
        switch LocalNamePolicy.decide(localHostName: LocalNamePolicy.currentLocalHostName(), lan: lan) {
        case .thisMac:
            stop()
            state = "this-mac"
        case .off:
            stop()
            state = "off"
        case .publish:
            guard let lan else { return }
            if let c = conflictAt, now.timeIntervalSince(c) < LocalNamePolicy.retryAfterConflict { return }
            if published == lan, conn != nil, state == "published" || state == "probing" { return }
            stop()
            register(lan)
        }
    }

    func register(_ lan: LanAddress) {
        var c: DNSServiceRef?
        var err = DNSServiceCreateConnection(&c)
        guard err == DNSServiceErrorType(kDNSServiceErr_NoError), let c else {
            state = "failed: mDNSResponder said \(err)"
            return
        }
        DNSServiceSetDispatchQueue(c, queue)
        var addr = in_addr()
        guard inet_pton(AF_INET, lan.ipv4, &addr) == 1 else { DNSServiceRefDeallocate(c); state = "failed: bad address"; return }
        let ifIndex = lan.interface.map { if_nametoindex($0) } ?? 0
        var rec: DNSRecordRef?
        let ctx = Unmanaged.passUnretained(self).toOpaque()
        let reply: DNSServiceRegisterRecordReply = { _, _, _, errorCode, context in
            guard let context else { return }
            Unmanaged<LocalNamePublisher>.fromOpaque(context).takeUnretainedValue().answered(errorCode)
        }
        err = withUnsafeBytes(of: &addr) { buf in
            DNSServiceRegisterRecord(c, &rec, DNSServiceFlags(kDNSServiceFlagsUnique), ifIndex, LocalNamePolicy.name,
                                     UInt16(kDNSServiceType_A), UInt16(kDNSServiceClass_IN), UInt16(buf.count), buf.baseAddress,
                                     120, reply, ctx)
        }
        guard err == DNSServiceErrorType(kDNSServiceErr_NoError) else {
            DNSServiceRefDeallocate(c)
            state = "failed: mDNSResponder said \(err)"
            return
        }
        conn = c
        record = rec
        published = lan
        state = "probing"
    }

    func answered(_ code: DNSServiceErrorType) {
        if code == DNSServiceErrorType(kDNSServiceErr_NoError) {
            state = "published"
            log("domovoi.local now answers with \(published?.ipv4 ?? "?") on \(published?.interface ?? "?")")
        } else if code == DNSServiceErrorType(kDNSServiceErr_NameConflict) {
            log("another device on the network answers for domovoi.local: Domovoi leaves the name to it")
            stop()
            state = "taken"
            conflictAt = Date()
        } else {
            log("domovoi.local: mDNSResponder said \(code)")
            stop()
            state = "failed: mDNSResponder said \(code)"
        }
    }

    public func stop() {
        // Deallocating the connection removes every record registered on it.
        if let c = conn { DNSServiceRefDeallocate(c) }
        conn = nil
        record = nil
        published = nil
    }

    deinit { stop() }
}
