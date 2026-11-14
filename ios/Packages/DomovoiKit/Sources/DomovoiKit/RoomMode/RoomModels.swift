import CryptoKit
import Foundation

/// The room's listening models, from its own server, never from the app —
/// `roommode/RoomModels.kt`:
///
/// - openWakeWord's melspectrogram and embedding models, the Silero voice
///   detector and the stock wake-word model come from `/v1/wake-base` — the
///   core's satellite media cache, the files a prepared Pi card carries. Each
///   is taken only when the signed list names it (our server's key) AND its
///   digest and size are the pins below, the same pins the core holds
///   (domovoi/satellite_media/fetchers.py OWW_MODEL_FILES; `RoomModelsTests`
///   checks the two agree).
/// - a wake word the dashboard trained and pushed (`set_wake_word`) comes
///   from `/v1/wake-models`, signed, checked against the list's digest.
///
/// Nothing is bundled in the app: the pretrained wake-word models are not
/// under a licence the app may ship. Files live under the room's own folder
/// (Application Support/room/models, excluded from backups), written
/// atomically, re-hashed before every load.
public final class RoomModels: @unchecked Sendable {

    public enum Outcome: Equatable, Sendable {
        /// Every file the room needs is here and checked.
        case ready(RoomModelFiles)
        /// The server does not have these (its satellite media cache was
        /// never filled, or the pushed model is gone).
        case missing([String])
        /// Could not ask (offline, a refused signature): why.
        case failed(String)
    }

    /// The three networks every room runs.
    public static let base = ["melspectrogram.onnx", "embedding_model.onnx", "silero_vad.onnx"]

    /// domovoi/satellite_media/fetchers.py OWW_MODEL_FILES, the .onnx rows:
    /// sha256 and size.
    public static let pins: [String: (sha: String, size: Int)] = [
        "alexa_v0.1.onnx": ("6ff566a01d12670e8d9e3c59da32651db1575d17272a601b7f8a39283dfbae3e", 854_246),
        "embedding_model.onnx": ("70d164290c1d095d1d4ee149bc5e00543250a7316b59f31d056cff7bd3075c1f", 1_326_578),
        "hey_jarvis_v0.1.onnx": ("94a13cfe60075b132f6a472e7e462e8123ee70861bc3fb58434a73712ee0d2cb", 1_271_370),
        "hey_mycroft_v0.1.onnx": ("c2a311e8fa1338de89c31b3b46dc4dffd4af2f9a8d6ddead48893c2d301b1f18", 857_691),
        "hey_rhasspy_v0.1.onnx": ("5a9b3ed3be2910e35780e097905aa9f35a9c10038df47914cf2b3ec4d670f6ea", 204_081),
        "melspectrogram.onnx": ("ba2b0e0f8b7b875369a2c89cb13360ff53bac436f2895cced9f479fa65eb176f", 1_087_958),
        "silero_vad.onnx": ("a35ebf52fd3ce5f1469b2a36158dba761bc47b973ea3382b3186ca15b1f5af28", 1_807_522),
        "timer_v0.1.onnx": ("371e44535470a29248b3b8f1bbbbaf2525c86417fd8f75c67fcf02ae0b9626df", 1_742_475),
        "weather_v0.1.onnx": ("8441da8e746899e8d969528d5bad5651cdd563079c05962788f77753041f60e7", 1_149_158),
    ]

    /// No model is anywhere near this; a body over it is refused.
    public static let maxModelBytes = 32 * 1024 * 1024

    private let dir: URL
    private let book: any RoomModelBook

    public init(directory: URL, book: any RoomModelBook) {
        self.dir = directory
        self.book = book
    }

    /// The stock model for a wake word openWakeWord ships ("hey_jarvis" ->
    /// hey_jarvis_v0.1.onnx), or nil for a trained one.
    public static func stockFile(_ wakeWord: String) -> String? {
        let name = "\(wakeWord)_v0.1.onnx"
        return pins[name] == nil ? nil : name
    }

    /// The file a wake word loads: the stock model, or `<slug>.onnx`.
    public static func wakeFileName(_ wakeWord: String) -> String {
        stockFile(wakeWord) ?? "\(wakeWord).onnx"
    }

    /// A slug the core may push: the model file stem it serves
    /// (`^[a-z0-9][a-z0-9_]{0,63}$`).
    public static func isSlug(_ value: String?) -> Bool {
        guard let value, (1...64).contains(value.unicodeScalars.count) else { return false }
        for (i, s) in value.unicodeScalars.enumerated() {
            let lowerOrDigit = ("a"..."z").contains(s) || ("0"..."9").contains(s)
            if i == 0 ? !lowerOrDigit : !(lowerOrDigit || s == "_") { return false }
        }
        return true
    }

    /// What the room loads for `wakeWord`, if it is all here and intact.
    public func local(_ wakeWord: String) -> RoomModelFiles? {
        let base = Self.base.map { dir.appendingPathComponent($0) }
        guard base.allSatisfy(verifiedBase) else { return nil }
        let wakeFile = dir.appendingPathComponent(Self.wakeFileName(wakeWord))
        let wakeOK = Self.stockFile(wakeWord) != nil ? verifiedBase(wakeFile) : trainedModelIntact(wakeFile.lastPathComponent)
        guard wakeOK else { return nil }
        return RoomModelFiles(melspectrogram: base[0], embedding: base[1], vad: base[2], wake: wakeFile, wakeKey: wakeWord)
    }

    /// A trained model is loaded only when it still hashes to what its
    /// signed list said when it was taken (re-hashed at every load).
    public func trainedModelIntact(_ name: String) -> Bool {
        guard let want = book.trainedSHA(name) else { return false }
        return Self.sha256(file: dir.appendingPathComponent(name)) == want
    }

    /// Fetch what is missing or changed for `wakeWord` from the core at
    /// `coreURL` (pinned `fingerprint`) over `http`. Blocking.
    public func sync(http: any RoomHTTP, coreURL: String, fingerprint: String, wakeWord: String) -> Outcome {
        do {
            try Self.makeFolder(dir)
            let baseList = try signedList(http, "\(coreURL)/v1/wake-base/manifest.sig", RoomIdentity.wakeBaseChannel, fingerprint)
            let wanted = Self.base + [Self.stockFile(wakeWord)].compactMap { $0 }
            let missing = wanted.filter { baseList[$0] == nil }
            for name in wanted where !missing.contains(name) {
                guard let pin = Self.pins[name] else { continue }
                if baseList[name] != pin.sha {
                    throw RoomNetworkFailure("the server lists \(name) with a digest the app does not pin")
                }
                let target = dir.appendingPathComponent(name)
                if verifiedBase(target) { continue }
                try download(http, "\(coreURL)/v1/wake-base/\(name)", target, sha: pin.sha, size: pin.size)
            }
            if Self.stockFile(wakeWord) == nil {
                let name = Self.wakeFileName(wakeWord)
                let list = try signedList(http, "\(coreURL)/v1/wake-models/manifest.sig", RoomIdentity.wakeModelsChannel, fingerprint)
                guard let sha = list[name] else { return .missing(missing + [name]) }
                let target = dir.appendingPathComponent(name)
                if Self.sha256(file: target) != sha {
                    try download(http, "\(coreURL)/v1/wake-models/\(name)", target, sha: sha, size: nil)
                }
                book.setTrainedSHA(name, sha)
            }
            if !missing.isEmpty { return .missing(missing) }
            guard let files = local(wakeWord) else { return .failed("the models did not check out after the download") }
            return .ready(files)
        } catch {
            return .failed(RoomIdentity.describe(error))
        }
    }

    private func signedList(_ http: any RoomHTTP, _ url: String, _ channel: String, _ fingerprint: String) throws -> [String: String] {
        let body = try http.get(url)
        let accepted = try RoomIdentity.verifyManifestEnvelope(RoomJSON.parse(body), channel: channel, expected: fingerprint)
        try RoomIdentity.checkFreshness(accepted, last: book.seen(channel))
        book.remember(channel, accepted)
        return accepted.manifest
    }

    private func download(_ http: any RoomHTTP, _ url: String, _ target: URL, sha: String, size: Int?) throws {
        let bytes = try http.get(url, limit: Self.maxModelBytes)
        if let size, bytes.count != size {
            throw RoomNetworkFailure("\(target.lastPathComponent) is \(bytes.count) bytes, not \(size)")
        }
        guard Self.sha256(bytes) == sha else {
            throw RoomNetworkFailure("\(target.lastPathComponent) is not the file the signed list names")
        }
        do {
            try bytes.write(to: target, options: .atomic)
        } catch {
            throw RoomNetworkFailure("could not save \(target.lastPathComponent)")
        }
    }

    private func verifiedBase(_ file: URL) -> Bool {
        guard let pin = Self.pins[file.lastPathComponent] else { return false }
        guard Self.size(of: file) == pin.size else { return false }
        return Self.sha256(file: file) == pin.sha
    }

    // MARK: - files

    /// Makes `folder` (and its parents) and keeps it out of backups: models
    /// and clips come back from the server.
    static func makeFolder(_ folder: URL) throws {
        try FileManager.default.createDirectory(at: folder, withIntermediateDirectories: true)
        var values = URLResourceValues()
        values.isExcludedFromBackup = true
        var marked = folder
        try? marked.setResourceValues(values)
    }

    static func size(of file: URL) -> Int? {
        guard let attributes = try? FileManager.default.attributesOfItem(atPath: file.path),
              (attributes[.type] as? FileAttributeType) == .typeRegular,
              let n = attributes[.size] as? NSNumber
        else { return nil }
        return n.intValue
    }

    /// The file's sha256 in hex, or nil when it is not a readable file.
    public static func sha256(file: URL) -> String? {
        guard size(of: file) != nil, let handle = try? FileHandle(forReadingFrom: file) else { return nil }
        defer { try? handle.close() }
        var hasher = SHA256()
        while true {
            let chunk = handle.readData(ofLength: 1 << 16)
            if chunk.isEmpty { break }
            hasher.update(data: chunk)
        }
        return RoomIdentity.hex(Data(hasher.finalize()))
    }

    public static func sha256(_ bytes: Data) -> String {
        RoomIdentity.hex(Data(SHA256.hash(data: bytes)))
    }
}
