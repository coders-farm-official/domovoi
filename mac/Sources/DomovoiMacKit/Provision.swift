import Foundation

/// Where the Linux side comes from, and the options for provision.sh
/// (scripts/linux/provision.sh; mac-installer PLAN 5, step 6.4).
public struct ProvisionSource: Equatable {
    /// provision.sh on the Mac: the pinned commit's (Phase 3 takes it from
    /// the package; the user mode is given it).
    public var script: URL
    /// The repository: a URL, or a git bundle on the Mac that is copied into
    /// the VM (the user mode's way to test a branch nobody pushed).
    public var repoURL: String?
    public var repoBundle: URL?
    public var ref: String?
    public var trackBranch: String?
    public var allowedSigners: URL?
    public var noVisionModel: Bool
    public var extraArgs: [String]

    public init(script: URL, repoURL: String? = nil, repoBundle: URL? = nil, ref: String? = nil, trackBranch: String? = nil,
                allowedSigners: URL? = nil, noVisionModel: Bool = false, extraArgs: [String] = []) {
        self.script = script
        self.repoURL = repoURL
        self.repoBundle = repoBundle
        self.ref = ref
        self.trackBranch = trackBranch
        self.allowedSigners = allowedSigners
        self.noVisionModel = noVisionModel
        self.extraArgs = extraArgs
    }

    /// Where the helper puts its inputs inside the VM (root's).
    public static let guestDir = "/var/lib/domovoi-mac-install"
    public static let guestScript = guestDir + "/provision.sh"
    public static let guestBundle = guestDir + "/origin.bundle"
    public static let guestSigners = guestDir + "/allowed_signers"
}

public enum ProvisionArgs {
    /// Lima's user-mode network: the Mac's loopback is host.lima.internal
    /// (192.168.5.2), DNS 192.168.5.3. The VM guard lets the VM reach only
    /// Ollama and DNS there (PLAN 4.2c; Phase 1 check 2).
    public static let limaNet = "192.168.5.0/24"
    public static let limaDNS = "192.168.5.3"
    /// The household's connections arrive on the VM's vzNAT interface (the
    /// Mac's pf redirect, PLAN 4.2a): there only Domovoi's LAN ports take
    /// new connections (provision.sh --vm-guard-lan).
    public static let lanInterface = GuestNet.interface

    /// `sttURL`/`sttModel`: the Mac's speech service (PHASE8A-STT.md), when
    /// one is installed: the core uses it before its own Whisper, and the
    /// VM guard lets the core (not the containers) reach its port. It runs
    /// on the Mac's graphics chip beside Ollama, which the core is told
    /// (`--host-stt-shares-gpu`: WHISPER_REMOTE_SHARES_GPU=true in .env).
    public static func build(source: ProvisionSource, answers: InstallRecord.Answers, ollamaURL: String,
                             sttURL: String? = nil, sttModel: String? = nil) -> [String] {
        var a: [String] = []
        if source.repoBundle != nil {
            a += ["--repo-url", ProvisionSource.guestBundle]
        } else if let u = source.repoURL {
            a += ["--repo-url", u]
        }
        if let r = source.ref { a += ["--ref", r] }
        if let t = source.trackBranch { a += ["--track-branch", t] }
        a += ["--internet", answers.internet, "--tier", answers.tier.rawValue]
        if let tz = answers.timezone { a += ["--timezone", tz] }
        for m in answers.media { a += ["--media", "\(m.name.lowercased())=\(m.guestPath)"] }
        a += ["--ollama-url", ollamaURL, "--vm-guard", limaNet, "--vm-guard-dns", limaDNS, "--vm-guard-lan", lanInterface]
        if let u = sttURL {
            a += ["--host-stt-url", u]
            if let m = sttModel { a += ["--host-stt-model", m] }
            a.append("--host-stt-shares-gpu")
        }
        if source.allowedSigners != nil { a += ["--allowed-signers", ProvisionSource.guestSigners] }
        if source.noVisionModel { a.append("--no-vision-model") }
        return a + source.extraArgs
    }
}

/// One `@@DOMOVOI {json}` progress line (provision.sh's header).
public struct ProvisionProgress: Equatable {
    public var step: String
    public var state: String
    public var percent: Int?
    public var message: String?
    public var final: Bool
    public var result: String?
    public var failedStep: String?
    /// The final line's "phone_app": true when the Android app is on the
    /// server for phones, false when the phone-app step couldn't download
    /// it (the install is still ok), nil when unknown (no app release yet,
    /// a run that stopped before that step, or an older provision.sh).
    public var phoneApp: Bool? = nil

    public static let prefix = "@@DOMOVOI "
    /// What Setup's last page says when `phoneApp` is false: provision.sh's
    /// PHONE_APP_MISSING_MSG, as Windows Setup's last page says it.
    public static let phoneAppMissing = "The phone app couldn't be downloaded: this server needs the internet once, "
        + "at some point, to put it here (or upload it in Settings)."

    public static func parse(_ line: String) -> ProvisionProgress? {
        guard line.hasPrefix(prefix),
              let obj = try? JSONSerialization.jsonObject(with: Data(line.dropFirst(prefix.count).utf8)) as? [String: Any],
              let step = obj["step"] as? String, let state = obj["state"] as? String else { return nil }
        return ProvisionProgress(step: step, state: state, percent: obj["percent"] as? Int, message: obj["message"] as? String,
                                 final: (obj["final"] as? Bool) ?? false, result: obj["result"] as? String,
                                 failedStep: obj["failed_step"] as? String, phoneApp: obj["phone_app"] as? Bool)
    }
}

/// The Mac's time zone name, from /etc/localtime (PLAN 4.14).
public func macTimeZone() -> String? {
    guard let target = try? FileManager.default.destinationOfSymbolicLink(atPath: "/etc/localtime"),
          let r = target.range(of: "zoneinfo/") else { return TimeZone.current.identifier }
    return String(target[r.upperBound...])
}
