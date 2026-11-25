import Foundation

/// A house identity backup (1.5.0, S3-4; domovoi/server_identity.py): the
/// server's private key, which the dashboard downloads (Settings > About &
/// updates, an admin with the password asked again). Setup's "Restore from
/// a backup" makes it the new install's identity, so the rooms set up
/// before still know this house.
///
/// The Mac side only carries the file: the setup page checks its shape so a
/// wrong file is caught before a long install, the helper reads it once as
/// root (safely: see `read`), and provision.sh's `--restore-identity` hands
/// it to the core's own python, which does every real check (the key pair,
/// a signature that verifies) and never replaces a different identity that
/// is already there. The key is never logged; nothing here prints it.
public enum IdentityBackup {
    /// server_identity.BACKUP_MAX_BYTES (provision.sh refuses past it).
    public static let maxBytes = 16 * 1024
    public static let kind = "domovoi-server-identity-backup"
    /// Where the step puts it in the VM, root's, 0600, for the one
    /// provision.sh run; deleted after it.
    public static let guestFile = ProvisionSource.guestDir + "/house-identity.json"

    /// Remove everything's warning (Setup's uninstall page and
    /// domovoi-uninstall's usage): the identity goes with the VM.
    public static let removeEverythingWarning =
        "Your rooms will need setting up again: this also deletes the house identity they know Domovoi by. " +
        "To keep it, first download a house identity backup in the dashboard (Settings > About & updates > " +
        "Download house identity backup; an admin, with the password asked again), then choose Restore from a backup " +
        "when you install Domovoi again."

    /// The media page's note when the data was kept from an earlier
    /// install: the kept VM holds the identity, and nothing replaces it.
    public static let keptNote =
        "This Mac kept Domovoi's data from before, house identity included, so the rooms set up then still know it."

    public struct Summary: Equatable {
        /// The fingerprint the rooms hold this house to (public).
        public var fingerprint: String
        public var createdAt: String?
    }

    /// The backup's public facts, or a reason a person can act on. A shape
    /// check only; the core's python checks the key itself.
    public static func check(_ data: Data) -> Result<Summary, HelperError> {
        guard data.count <= maxBytes else { return .failure(HelperError("this file is far too large to be a house identity backup")) }
        guard let obj = try? JSONSerialization.jsonObject(with: data), let doc = obj as? [String: Any] else {
            return .failure(HelperError("this file is not a Domovoi house identity backup (it isn't a JSON object)"))
        }
        if let k = doc["kind"] as? String, k != kind {
            return .failure(HelperError(k == "satellite-server-fingerprint"
                ? "this is a room device's record of its server (public only), not a house identity backup"
                : "this file is not a Domovoi house identity backup"))
        }
        guard let key = doc["private_key"] as? String, !key.isEmpty else {
            return .failure(HelperError("this file has no private key: it is not a house identity backup"))
        }
        guard let fp = doc["fingerprint"] as? String, !fp.isEmpty else {
            return .failure(HelperError("this backup has no fingerprint"))
        }
        return .success(Summary(fingerprint: fp, createdAt: doc["created_at"] as? String))
    }

    /// The file's bytes, read by root without following a link, only when
    /// it is a regular file of `owner` (the account that runs Setup, which
    /// copied it into its own private folder) and small enough to be a
    /// backup. So root never reads a file that account couldn't hand over.
    public static func read(path: String, owner: uid_t?) throws -> Data {
        let fd = open(path, O_RDONLY | O_NOFOLLOW | O_NONBLOCK)
        guard fd >= 0 else { throw HelperError("can't open the identity backup \(path) (\(String(cString: strerror(errno))); a link is refused)") }
        defer { close(fd) }
        var st = stat()
        guard fstat(fd, &st) == 0 else { throw HelperError("can't read the identity backup \(path)") }
        guard (st.st_mode & S_IFMT) == S_IFREG else { throw HelperError("the identity backup \(path) is not a regular file") }
        if let owner, st.st_uid != owner { throw HelperError("the identity backup \(path) doesn't belong to the account running Setup") }
        guard st.st_size <= off_t(maxBytes) else { throw HelperError("the identity backup is far too large to be one") }
        let h = FileHandle(fileDescriptor: fd, closeOnDealloc: false)
        let data = try h.readToEnd() ?? Data()
        guard data.count <= maxBytes else { throw HelperError("the identity backup is far too large to be one") }
        if case .failure(let e) = check(data) { throw e }
        return data
    }
}
