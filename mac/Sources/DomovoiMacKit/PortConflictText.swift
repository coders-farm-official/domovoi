import Foundation

/// What Setup's sheet says about a port another program holds.
///
/// The name is said once. An owner this account can't see (lsof shows a
/// standard user only its own programs) is "Another program", and the
/// sheet says once that it may be another account's (Sitting 3, S3-9: the
/// hint was part of the name, so the sheet said it three times, starting
/// lowercase).
extension PortConflict {
    /// The owner, as the start of a sentence.
    var sentenceOwner: String {
        guard let first = owner.first else { return owner }
        return first.uppercased() + owner.dropFirst()
    }

    var unnamed: Bool { owner == Preflight.unnamedOwner }

    public var sheetTitle: String { "\(sentenceOwner) is using Domovoi's ports" }

    public var sheetText: String {
        let which = ports.count == 1 ? "port" : "ports"
        var t = "\(sentenceOwner) holds \(which) \(Preflight.portList(ports)), which Domovoi needs. "
        if let v = ollamaVersion {
            t += "It is Ollama \(v). "
            if ollamaUsable {
                t += "Domovoi can use it instead of running its own: its AI models then stay in that Ollama's own folder, and uninstalling Domovoi leaves them there."
                if ollamaIsApp { t += " The Ollama app runs only while someone is signed in, so Domovoi's AI answers stop when nobody is." }
                t += " Or quit Ollama and choose Retry, and Domovoi runs its own."
            } else {
                t += "It is too old for Domovoi: quit it (or update it) and choose Retry."
            }
        } else if unnamed {
            t += "Setup can't see which program it is; it may belong to another account on this Mac. Quit it, then choose Retry."
        } else {
            t += "Quit \(owner), then choose Retry."
        }
        return t
    }
}
