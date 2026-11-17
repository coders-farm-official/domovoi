import Foundation

/// One pinned download.
public struct Pin: Equatable, Codable {
    public var url: String
    public var sha256: String

    public init(url: String, sha256: String) {
        self.url = url
        self.sha256 = sha256.lowercased()
    }

    public var fileName: String { (URL(string: url)?.lastPathComponent).flatMap { $0.isEmpty ? nil : $0 } ?? "download" }
}

/// The Mac's own speech recognition (mac-installer PHASE8A-STT.md): the
/// whisper.cpp tag `mac/build.sh` builds whisper-server from (the binary
/// ships in the package), and the model the install downloads.
public struct SttPins: Equatable {
    public var whisperCppVersion: String
    public var whisperCppCommit: String
    /// The core's label for it (`WHISPER_REMOTE_MODEL`), e.g. small.en.
    public var modelName: String
    public var model: Pin
    public var modelBytes: Int64
}

/// `mac/installer/pins.conf`.
public struct Pins: Equatable {
    public var limaVersion: String
    public var lima: Pin
    public var ollamaVersion: String
    public var ollama: Pin
    public var ollamaMinimum: OllamaVersion
    public var ubuntuImage: Pin
    /// nil when pins.conf has no WHISPERCPP_VERSION: this package recognises
    /// speech on the VM's processor only (and installs no speech service).
    public var stt: SttPins? = nil

    public static func parse(_ text: String) throws -> Pins {
        var kv: [String: String] = [:]
        for (i, raw) in text.split(separator: "\n", omittingEmptySubsequences: false).enumerated() {
            let line = raw.trimmingCharacters(in: .whitespaces)
            if line.isEmpty || line.hasPrefix("#") { continue }
            guard let eq = line.firstIndex(of: "=") else { throw HelperError("pins.conf line \(i + 1): no '='") }
            let k = String(line[..<eq]), v = String(line[line.index(after: eq)...])
            guard kv[k] == nil else { throw HelperError("pins.conf: \(k) given twice") }
            kv[k] = v
        }
        func need(_ k: String) throws -> String {
            guard let v = kv[k], !v.isEmpty else { throw HelperError("pins.conf: no \(k)") }
            return v
        }
        func sha(_ k: String) throws -> String {
            let v = try need(k)
            guard v.count == 64, v.allSatisfy({ $0.isHexDigit && !$0.isUppercase }) else { throw HelperError("pins.conf: \(k) is not a lowercase SHA-256") }
            return v
        }
        func https(_ k: String) throws -> String {
            let v = try need(k)
            guard v.hasPrefix("https://") else { throw HelperError("pins.conf: \(k) is not https") }
            return v
        }
        guard let minimum = OllamaVersion(try need("OLLAMA_MIN_VERSION")) else { throw HelperError("pins.conf: OLLAMA_MIN_VERSION is not a version") }
        let p = Pins(
            limaVersion: try need("LIMA_VERSION"),
            lima: Pin(url: try https("LIMA_URL"), sha256: try sha("LIMA_SHA256")),
            ollamaVersion: try need("OLLAMA_VERSION"),
            ollama: Pin(url: try https("OLLAMA_URL"), sha256: try sha("OLLAMA_SHA256")),
            ollamaMinimum: minimum,
            ubuntuImage: Pin(url: try https("UBUNTU_IMAGE_URL"), sha256: try sha("UBUNTU_IMAGE_SHA256")))
        guard p.lima.url.contains("/v\(p.limaVersion)/") else { throw HelperError("pins.conf: LIMA_URL is not version \(p.limaVersion)") }
        guard p.ollama.url.contains("/v\(p.ollamaVersion)/") else { throw HelperError("pins.conf: OLLAMA_URL is not version \(p.ollamaVersion)") }
        guard let own = OllamaVersion(p.ollamaVersion), own >= minimum else { throw HelperError("pins.conf: the pinned Ollama is older than OLLAMA_MIN_VERSION") }
        var out = p
        if kv["WHISPERCPP_VERSION"] != nil {
            let commit = try need("WHISPERCPP_COMMIT")
            guard commit.count == 40, commit.allSatisfy({ $0.isHexDigit && !$0.isUppercase }) else { throw HelperError("pins.conf: WHISPERCPP_COMMIT is not a full lowercase commit") }
            let name = try need("STT_MODEL_NAME")
            guard name.allSatisfy({ $0.isLetter || $0.isNumber || $0 == "." || $0 == "-" || $0 == "_" }) else { throw HelperError("pins.conf: STT_MODEL_NAME is not a model name") }
            guard let bytes = Int64(try need("STT_MODEL_BYTES")), bytes > 0 else { throw HelperError("pins.conf: STT_MODEL_BYTES is not a size") }
            let model = Pin(url: try https("STT_MODEL_URL"), sha256: try sha("STT_MODEL_SHA256"))
            // A model by commit, never by branch: the file under a name can change.
            guard model.url.range(of: "/resolve/[0-9a-f]{40}/", options: .regularExpression) != nil else {
                throw HelperError("pins.conf: STT_MODEL_URL must name a commit (/resolve/<40 hex>/)")
            }
            out.stt = SttPins(whisperCppVersion: try need("WHISPERCPP_VERSION"), whisperCppCommit: commit, modelName: name,
                              model: model, modelBytes: bytes)
        }
        return out
    }

    public static func load(_ url: URL) throws -> Pins {
        do {
            return try parse(String(contentsOf: url, encoding: .utf8))
        } catch let e as HelperError {
            throw e
        } catch {
            throw HelperError("can't read \(url.path): \(error.localizedDescription)")
        }
    }
}
