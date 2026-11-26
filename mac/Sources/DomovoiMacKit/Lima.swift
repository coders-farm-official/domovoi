import Foundation

/// The VM's settings, from the Mac (mac-installer PLAN 4.3, 4.10).
public struct VMSize: Equatable, Codable {
    public var cpus: Int
    public var memoryGiB: Int
    public var diskGiB: Int

    /// 6 GiB on a 16-23 GB Mac, 8 GiB from 24 GB (unified memory is shared
    /// with Ollama's models); half the cores, 2 to 4.
    public static func forMac(memoryBytes: UInt64, cores: Int) -> VMSize {
        let gb = Double(memoryBytes) / 1_000_000_000
        return VMSize(cpus: max(2, min(4, cores / 2)), memoryGiB: gb >= 24 ? 8 : 6, diskGiB: 40)
    }
}

/// provision.sh's model tier, from the Mac's memory, not the VM's.
public enum Tier: String, Codable {
    case floor, standard

    public static func forMac(memoryBytes: UInt64) -> Tier {
        Double(memoryBytes) / 1_000_000_000 >= 24 ? .standard : .floor
    }
}

/// How many requests Domovoi's own Ollama answers at once
/// (OLLAMA_NUM_PARALLEL), so a voice turn needn't wait behind a chat reply.
/// The tiers of `scripts/linux/provision.sh` (`ollama_parallel`), from the
/// Mac's memory as ``Tier`` reads it: under 24 GB 1, from 24 GB 2, from
/// 48 GB 3. Each slot is one more context window of every loaded model
/// (about 1.1 GiB at the default windows), held in the unified memory the
/// VM and the speech models share, so 4, which Linux gives only to GPUs
/// that hold the models plus four windows, is for Macs of 64 GB or more.
public enum OllamaParallel {
    public static func forMac(memoryBytes: UInt64) -> Int {
        let gb = Double(memoryBytes) / 1_000_000_000
        switch gb {
        case 64...: return 4
        case 48...: return 3
        case 24...: return 2
        default: return 1
        }
    }
}

/// A media folder shared into the VM at /srv/media/<name>.
public struct MediaMount: Equatable, Codable {
    public var name: String
    public var hostPath: String

    public init(name: String, hostPath: String) {
        self.name = name
        self.hostPath = hostPath
    }

    public var guestPath: String { "/srv/media/\(name.lowercased())" }

    public static let defaultNames = ["Music", "Pictures", "Videos", "Documents", "Audiobooks"]

    static func validName(_ n: String) -> Bool {
        guard let f = n.unicodeScalars.first, f.isASCII, CharacterSet.letters.contains(f) else { return false }
        return n.count <= 32 && n.unicodeScalars.allSatisfy { CharacterSet.alphanumerics.contains($0) && $0.isASCII || $0 == "-" || $0 == "_" }
    }
}

/// `mac/vm/domovoi.yaml`, filled in.
public enum LimaTemplate {
    /// `kitMount`: an offline kit's folder on the Mac (OfflineKit.swift),
    /// shared into the VM read-only at KitNames.guestMount for provision.sh
    /// --offline-kit. Only in kit mode: a mount whose folder is gone (the
    /// stick taken out) would stop the VM from starting, so the install
    /// takes it out of the VM again once provisioning ends
    /// (Installer.releaseKit).
    public static func render(template: String, size: VMSize, imagePath: String, imageSHA256: String, mounts: [MediaMount], ports: Ports,
                              kitMount: String? = nil) throws -> String {
        for m in mounts {
            guard MediaMount.validName(m.name) else { throw HelperError("media folder name \(m.name) must be letters, digits, - or _") }
            guard m.hostPath.hasPrefix("/") else { throw HelperError("media folder \(m.hostPath) must be an absolute path") }
        }
        if Set(mounts.map { $0.name.lowercased() }).count != mounts.count { throw HelperError("two media folders have the same name") }
        if let k = kitMount, !k.hasPrefix("/") { throw HelperError("the offline kit's folder \(k) must be an absolute path") }
        var entries = mounts.map {
            "- location: \(Template.yamlString($0.hostPath))\n  mountPoint: \(Template.yamlString($0.guestPath))\n  writable: true"
        }
        if let k = kitMount {
            entries.append("- location: \(Template.yamlString(k))\n  mountPoint: \(Template.yamlString(KitNames.guestMount))\n  writable: false")
        }
        let block = entries.isEmpty ? "mounts: []" : "mounts:\n" + entries.joined(separator: "\n")
        return try Template.render(template, values: [
            "CPUS": String(size.cpus),
            "MEMORY": Template.yamlString("\(size.memoryGiB)GiB"),
            "DISK": Template.yamlString("\(size.diskGiB)GiB"),
            "IMAGE_LOCATION": Template.yamlString(imagePath),
            "IMAGE_DIGEST": Template.yamlString("sha256:\(imageSHA256)"),
            "MOUNTS": block,
            "WEB_PORT": String(ports.web),
            "CORE_PORT": String(ports.core),
        ])
    }
}

/// `limactl`, always with our LIMA_HOME and HOME (never the user's ~/.lima).
public final class Lima {
    public let limactl: String
    public let limaHome: String
    public let home: String
    /// The account limactl runs as: the role account in the system install
    /// (Lima refuses root, and the VM must be the role account's), nil in
    /// the user mode (the signed-in user).
    public let runAs: String?
    let runner: CommandRunner

    public init(limactl: String, limaHome: String, home: String, runner: CommandRunner, runAs: String? = nil) {
        self.limactl = limactl
        self.limaHome = limaHome
        self.home = home
        self.runner = runner
        self.runAs = runAs
    }

    var env: [String: String] { ["LIMA_HOME": limaHome, "HOME": home] }

    public static let sudo = "/usr/bin/sudo"
    /// A plain PATH for limactl's own children (ssh): the system install's
    /// helper may have been started with almost no environment.
    public static let jobPath = "/usr/bin:/bin:/usr/sbin:/sbin"

    public func command(_ args: [String], stdin: Data? = nil, timeout: TimeInterval? = 600) -> Command {
        guard let user = runAs else {
            return Command(limactl, args, environment: env, stdin: stdin, timeout: timeout)
        }
        // sudo resets the environment (env_reset), so the variables go
        // through env(1). -n: never a password prompt (root needs none).
        // From "/": the role account can't read every folder.
        let vars = ["LIMA_HOME=\(limaHome)", "HOME=\(home)", "PATH=\(Self.jobPath)"]
        return Command(Self.sudo, ["-n", "-u", user, "/usr/bin/env"] + vars + [limactl] + args, stdin: stdin, timeout: timeout,
                       currentDirectory: "/")
    }

    /// Lima's own rule: its longest socket in the instance folder (ssh's
    /// control socket, "ssh.sock" plus ssh's 16-character suffix) must fit
    /// a Unix socket path, 104 bytes on macOS.
    public static func checkSocketRoom(limaHome: String, instance: String) throws {
        let longest = "\(limaHome)/\(instance)/ssh.sock.1234567890123456"
        if longest.utf8.count > 104 {
            throw HelperError("the Lima folder \(limaHome) is too long for the VM's sockets (\(longest.utf8.count) of 104 bytes): use a shorter prefix")
        }
    }

    /// `Running`, `Stopped`, ... or nil when there is no such instance.
    public func status(_ instance: String) throws -> String? {
        let r = try runner.run(command(["list", "--format", "{{.Name}} {{.Status}}"], timeout: 60))
        guard r.ok else { return nil }
        for line in r.stdout.split(separator: "\n") {
            let f = line.split(separator: " ")
            if f.count >= 2, f[0] == instance { return String(f[1]) }
        }
        return nil
    }

    public func validate(_ yaml: URL) throws {
        try runner.check(command(["validate", yaml.path], timeout: 60), "limactl validate")
    }

    public func create(_ instance: String, from yaml: URL) throws {
        try runner.check(command(["create", "--tty=false", "--name=\(instance)", yaml.path], timeout: 1800), "limactl create")
    }

    /// Runs a command inside the guest, from `/`.
    public func shell(_ instance: String, _ args: [String], stdin: Data? = nil, timeout: TimeInterval? = 600, onLine: ((String) -> Void)? = nil) throws -> CommandResult {
        try runner.run(command(["shell", "--workdir", "/", instance] + args, stdin: stdin, timeout: timeout), onLine: onLine)
    }

    public func stop(_ instance: String, force: Bool = false) throws {
        try runner.check(command(["stop"] + (force ? ["--force"] : []) + [instance], timeout: 300), "limactl stop")
    }

    /// The yq expression `limactl edit --set` takes to put the offline
    /// kit's read-only mount in (at `location`) or, with nil, take it out:
    /// the instance's other mounts stay as they are.
    public static func kitMountExpression(_ location: String?) -> String {
        let keep = "((.mounts // []) | map(select(.mountPoint != \(Template.yamlString(KitNames.guestMount)))))"
        guard let l = location else { return ".mounts = " + keep }
        return ".mounts = \(keep) + [{\"location\": \(Template.yamlString(l)), \"mountPoint\": \(Template.yamlString(KitNames.guestMount)), \"writable\": false}]"
    }

    /// Puts the offline kit's mount into a stopped instance's settings, or
    /// takes it out (nil). Lima refuses to edit a running instance; the
    /// change takes effect when it next starts.
    public func setKitMount(_ instance: String, location: String?) throws {
        try runner.check(command(["edit", "--tty=false", "--set", Self.kitMountExpression(location), instance], timeout: 120), "limactl edit (the offline kit's mount)")
    }

    public func delete(_ instance: String) throws {
        try runner.check(command(["delete", "--force", instance], timeout: 300), "limactl delete")
    }
}
