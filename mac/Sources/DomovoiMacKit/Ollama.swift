import Foundation

/// An Ollama version as `GET /api/version` reports it. Read as the core
/// reads it (domovoi/clients/ollama.py `_parse_ollama_version`): major,
/// minor and an optional patch; a suffix such as `-rc1` is ignored; `0.0.0`
/// (a build from source) says nothing, so it is no version.
public struct OllamaVersion: Comparable, Equatable, CustomStringConvertible {
    public let major: Int, minor: Int, patch: Int

    public init?(_ text: String) {
        var s = Substring(text.trimmingCharacters(in: .whitespaces))
        if s.hasPrefix("v") { s = s.dropFirst() }
        var parts: [Int] = []
        var digits = ""
        for c in s {
            if c.isASCII, c.isNumber { digits.append(c); continue }
            if c == ".", !digits.isEmpty, parts.count < 2 { parts.append(Int(digits)!); digits = ""; continue }
            break
        }
        if !digits.isEmpty { parts.append(Int(digits)!) }
        guard parts.count >= 2 else { return nil }
        major = parts[0]; minor = parts[1]; patch = parts.count > 2 ? parts[2] : 0
        if major == 0 && minor == 0 && patch == 0 { return nil }
    }

    public var description: String { "\(major).\(minor).\(patch)" }

    public static func < (a: OllamaVersion, b: OllamaVersion) -> Bool {
        (a.major, a.minor, a.patch) < (b.major, b.minor, b.patch)
    }
}

/// Which Ollama the core uses (mac-installer PLAN 12.2): Domovoi's own,
/// run by its own launchd job with models in Domovoi's folder, or the one
/// the household already runs on this Mac, whose models stay theirs.
public enum OllamaChoice: Equatable, Codable {
    case own
    case existing(port: Int)

    public var label: String {
        switch self {
        case .own: return "own"
        case .existing(let p): return "existing:\(p)"
        }
    }

    public static func parse(_ s: String) throws -> OllamaChoice {
        if s == "own" { return .own }
        if s == "existing" { return .existing(port: 11434) }
        if s.hasPrefix("existing:"), let p = Int(s.dropFirst("existing:".count)), (1...65535).contains(p) { return .existing(port: p) }
        throw HelperError("--ollama is own, existing or existing:PORT, not \(s)")
    }
}

/// What answered on an Ollama port.
public struct OllamaFound: Equatable {
    public var answered: Bool
    public var versionText: String?
    public var version: OllamaVersion?
    /// The listening program, when the system says (`lsof`), e.g. a path
    /// inside `Ollama.app`.
    public var program: String?

    public static let nothing = OllamaFound(answered: false, versionText: nil, version: nil, program: nil)

    /// Ollama.app is a login item: it runs only while someone is signed in.
    public var isTheApp: Bool { program?.contains(".app/") == true }
}

public final class OllamaProbe {
    let http: HTTPClient
    let runner: CommandRunner

    public init(http: HTTPClient, runner: CommandRunner) {
        self.http = http
        self.runner = runner
    }

    public func look(port: Int, timeout: TimeInterval = 3) -> OllamaFound {
        guard let r = http.get("http://127.0.0.1:\(port)/api/version", timeout: timeout) else { return .nothing }
        var f = OllamaFound(answered: true, versionText: nil, version: nil, program: listener(port: port))
        if r.status == 200,
           let obj = try? JSONSerialization.jsonObject(with: r.body) as? [String: Any],
           let v = obj["version"] as? String {
            f.versionText = v
            f.version = OllamaVersion(v)
        }
        return f
    }

    /// The path of the program listening on `port`, or nil. `-a` makes
    /// lsof's selectors AND: without it, it lists every process of the user
    /// (the 2026-10-29 incident).
    func listener(port: Int) -> String? {
        guard let r = try? runner.run(Command("/usr/sbin/lsof", ["-nP", "-a", "-iTCP:\(port)", "-sTCP:LISTEN", "-Fp"], timeout: 10)), r.ok,
              let line = r.stdout.split(separator: "\n").first(where: { $0.hasPrefix("p") }),
              let pid = Int32(line.dropFirst()) else { return nil }
        guard let ps = try? runner.run(Command("/bin/ps", ["-o", "comm=", "-p", String(pid)], timeout: 10)), ps.ok else { return nil }
        let p = ps.stdout.trimmingCharacters(in: .whitespacesAndNewlines)
        return p.isEmpty ? nil : p
    }

    /// The household's own Ollama, checked for "use the Ollama already on
    /// this Mac". Returns the warnings to show; throws when it can't be used.
    public static func checkExisting(_ f: OllamaFound, port: Int, minimum: OllamaVersion) throws -> [String] {
        guard f.answered else {
            throw HelperError("no Ollama answers on 127.0.0.1:\(port): start it, or let Domovoi run its own (--ollama own)")
        }
        guard let text = f.versionText else {
            throw HelperError("what answers on 127.0.0.1:\(port) doesn't answer Ollama's /api/version: is it Ollama?")
        }
        var warnings: [String] = []
        if let v = f.version {
            guard v >= minimum else {
                throw HelperError("the Ollama on port \(port) is \(v); Domovoi needs \(minimum) or newer: update it, or let Domovoi run its own")
            }
        } else if !text.trimmingCharacters(in: .whitespaces).hasPrefix("0.0.0") {
            throw HelperError("the Ollama on port \(port) reports version \(text), which isn't a version: Domovoi needs \(minimum) or newer")
        } else {
            // provision.sh's rule: 0.0.0 (a build from source) is a warning.
            warnings.append("The Ollama on port \(port) reports version \(text), which doesn't say what it can do; Domovoi needs \(minimum) or newer.")
        }
        if f.isTheApp {
            warnings.append("The Ollama app runs only while someone is signed in to this Mac, so Domovoi's AI answers stop when nobody is. Domovoi's own Ollama runs before anyone signs in.")
        }
        warnings.append("Domovoi's models will be in that Ollama's own folder; uninstalling Domovoi leaves them there.")
        return warnings
    }
}
