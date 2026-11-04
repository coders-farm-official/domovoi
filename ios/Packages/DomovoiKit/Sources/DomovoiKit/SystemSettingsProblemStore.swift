import Foundation

/// Where the problem log lives on this phone: one JSON file, in a directory
/// handed in.
///
/// A port of `android/.../diagnostics/ProblemStore.kt` (read 2026-11-02),
/// minus the `pending/` folder a dying Android process writes one file into:
/// nothing in this app writes a problem from inside a crash, because nothing
/// in this app catches one (see ``SystemSettingsProblemKind``'s heading). iOS
/// hands over the record afterwards, so there is one writer and it is calm.
///
/// An **actor**, and not a struct of free functions, for two reasons: the
/// read, the merge and the write are one critical section — two absorbs
/// racing would lose a problem — and it keeps file I/O off the main actor, as
/// Android keeps it off the main thread (`ProblemReports.kt:67`,
/// `withContext(Dispatchers.IO)`).
///
/// ### The directory is kept out of this phone's backup
///
/// `isExcludedFromBackup` on the directory, and it is what makes the privacy
/// card's sentence true: the problem reports are not in a backup and not in a
/// phone-to-phone transfer. Android gets the same from `allowBackup=false`
/// and `res/xml/data_extraction_rules.xml`, app-wide; iOS has no app-wide
/// switch, which is why the privacy wording had to be split — see
/// `IOS-ONLY-STRINGS.md` and `DECISIONS.md`.
///
/// ### Nothing here throws at the caller
///
/// A missing file, a damaged file, a directory that cannot be created: all of
/// them read as an empty log (``systemSettingsDecodeLog(_:)``'s rule, which
/// is `decodeLog`'s). A card that cannot draw because its own log file is
/// malformed is a worse answer than a card that says nothing is recorded, and
/// the file is this app's own: there is nobody to report a parse error to.
public actor SystemSettingsProblemStore {

    private let directory: URL
    private let fileName: String

    /// `problems.json` in the directory, as Android names it
    /// (`ProblemStore.kt:21`).
    public init(directory: URL, fileName: String = "problems.json") {
        self.directory = directory
        self.fileName = fileName
    }

    /// `Application Support/Diagnostics` in the app; a temporary directory in
    /// a test. Not `Caches`: iOS may evict a cache under pressure, and the one
    /// record of a crash is not something to lose to free a few kilobytes.
    public static func applicationSupport(
        fileManager: FileManager = .default
    ) -> SystemSettingsProblemStore? {
        guard let base = fileManager.urls(for: .applicationSupportDirectory, in: .userDomainMask).first
        else { return nil }
        return SystemSettingsProblemStore(directory: base.appendingPathComponent("Diagnostics", isDirectory: true))
    }

    private var file: URL { directory.appendingPathComponent(fileName, isDirectory: false) }

    public func read() -> SystemSettingsProblemLog {
        systemSettingsDecodeLog(try? Data(contentsOf: file))
    }

    /// Fold `incoming` into the stored log and answer what is now stored.
    ///
    /// The log's own de-duplication decides what is new
    /// (``SystemSettingsProblemLog/with(contentsOf:)``), which is what makes
    /// absorbing the same payloads twice — every time the card is opened —
    /// harmless. **Nothing is written when nothing changed**, so re-opening
    /// the card does not touch the file.
    @discardableResult
    public func absorb(_ incoming: [SystemSettingsProblem]) -> SystemSettingsProblemLog {
        let before = read()
        let after = before.with(contentsOf: incoming)
        guard after != before else { return before }
        write(after)
        return after
    }

    public func clear() {
        write(SystemSettingsProblemLog())
    }

    private func write(_ log: SystemSettingsProblemLog) {
        prepareDirectory()
        // `.atomic`: a write interrupted by the app going away leaves the old
        // file, never half a file. Android's store does the same with a
        // temporary file and a rename (`ProblemStore.kt:60-74`).
        try? systemSettingsEncodeLog(log).write(to: file, options: .atomic)
    }

    private func prepareDirectory() {
        let manager = FileManager.default
        if !manager.fileExists(atPath: directory.path) {
            try? manager.createDirectory(at: directory, withIntermediateDirectories: true)
        }
        var url = directory
        var values = URLResourceValues()
        values.isExcludedFromBackup = true
        // Set every time, not once: the directory may have been recreated by a
        // restore, and the flag is cheap. It is also the privacy card's claim,
        // so it is not left to a first run that may have failed.
        try? url.setResourceValues(values)
    }
}
