import Foundation

/// `share/build.json`, written by mac/build.sh into the package: what the
/// package was built from, and where the VM's Linux side comes from.
public struct BuildInfo: Codable, Equatable {
    public var schema: Int
    /// The Mac side's version (side.json).
    public var version: String
    /// The full commit the package was built from (side.json build_commit,
    /// and the commit provision.sh installs).
    public var commit: String
    /// provision.sh's --repo-url when no bundle ships in the package.
    public var repoURL: String?
    /// A git bundle in share/ (a test build of a commit nobody pushed).
    public var bundle: String?
    public var ref: String?
    public var trackBranch: String?
    /// More provision.sh options, as is.
    public var provisionArgs: [String]?
    public var builtAt: String?
    /// The release this package was built from (`build.sh --tag`), or nil
    /// for a development build.
    public var tag: String?
    /// Where the Mac side's updates come from: owner/name on GitHub (nil:
    /// MacReleaseNames.defaultRepo). A test build for the update train names
    /// a throwaway fork; the signature check against the pinned key is the
    /// gate either way.
    public var releaseRepo: String?

    enum CodingKeys: String, CodingKey {
        case schema, version, commit, bundle, ref, tag
        case repoURL = "repo_url"
        case trackBranch = "track_branch"
        case provisionArgs = "provision_args"
        case builtAt = "built_at"
        case releaseRepo = "release_repo"
    }

    public static func load(_ share: URL) throws -> BuildInfo {
        let u = share.appendingPathComponent("build.json")
        let b: BuildInfo
        do {
            b = try JSONDecoder().decode(BuildInfo.self, from: Data(contentsOf: u))
        } catch {
            throw HelperError("can't read \(u.path): \(error)")
        }
        guard b.schema == 1 else { throw HelperError("\(u.path): schema \(b.schema) is not 1") }
        guard b.commit.count == 40, b.commit.allSatisfy({ $0.isHexDigit && !$0.isUppercase }) else {
            throw HelperError("\(u.path): commit is not a full SHA-1")
        }
        if b.bundle == nil && b.repoURL == nil { throw HelperError("\(u.path): neither a bundle nor a repo_url") }
        if let n = b.bundle, n.contains("/") { throw HelperError("\(u.path): bundle must be a file name in share/") }
        if let t = b.tag, !MacReleaseNames.isTag(t) || t != "v" + b.version { throw HelperError("\(u.path): tag \(t) is not v + its version") }
        if let r = b.releaseRepo, !MacReleaseNames.isRepo(r) { throw HelperError("\(u.path): release_repo is not owner/name") }
        return b
    }

    /// Where provision.sh and the repository come from, in the package.
    public func provisionSource(share: URL) -> ProvisionSource {
        var p = ProvisionSource(script: share.appendingPathComponent("provision.sh"))
        if let b = bundle { p.repoBundle = share.appendingPathComponent(b) } else { p.repoURL = repoURL }
        p.ref = ref ?? commit
        p.trackBranch = trackBranch
        let signers = share.appendingPathComponent("allowed_signers")
        if FileManager.default.fileExists(atPath: signers.path) { p.allowedSigners = signers }
        p.extraArgs = provisionArgs ?? []
        return p
    }
}

/// What the setup pages hand the helper (`install --answers FILE`).
public struct SetupAnswers: Codable, Equatable {
    public var internet: String
    public var mediaRoot: String
    /// own, or existing (the Ollama already on port 11434).
    public var ollama: String
    /// "Keep this Mac awake while it's plugged in" (the consent page).
    public var keepAwake: Bool?
    /// "Start this Mac after a power failure" (the consent page, desktop
    /// Macs, only while FeatureGates.startAfterPowerFailure is on).
    public var startAfterPowerFailure: Bool?
    /// "Restore from a backup" (the media page, a first install only): the
    /// house identity backup, copied by the app into its own private work
    /// folder (IdentityBackup.read). Never saved in the install record: a
    /// repair doesn't restore again.
    public var restoreIdentity: String?

    enum CodingKeys: String, CodingKey {
        case internet, ollama
        case mediaRoot = "media_root"
        case keepAwake = "keep_awake"
        case startAfterPowerFailure = "start_after_power_failure"
        case restoreIdentity = "restore_identity"
    }

    public init(internet: String, mediaRoot: String, ollama: String, keepAwake: Bool? = nil, startAfterPowerFailure: Bool? = nil,
                restoreIdentity: String? = nil) {
        self.internet = internet
        self.mediaRoot = mediaRoot
        self.ollama = ollama
        self.keepAwake = keepAwake
        self.startAfterPowerFailure = startAfterPowerFailure
        self.restoreIdentity = restoreIdentity
    }
}
