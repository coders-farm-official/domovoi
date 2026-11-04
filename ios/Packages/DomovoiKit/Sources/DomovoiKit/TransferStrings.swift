import Foundation

/// What the save-to-device and streaming-upload controls say.
///
/// Android has none of these sentences: `DownloadManager` owns the whole of
/// its save (`DeviceDownloads.enqueueWithToken`, `VideosScreen.kt:216`), so
/// the only words it needs are the toast afterwards. On iOS the app carries
/// the file itself, so the progress, the cancel and the hand-off are all this
/// app's to word — every string here is therefore **iOS-only**, and
/// `design-notes/ios/batch-2026-11-03/transfers/REPORT.md` lists them.
///
/// Named after the unit rather than after a screen, because four screens draw
/// the same control (`Files`, `Videos`, and — once the player unit's rows
/// land — `Audiobooks` and `Podcasts`).
public enum TransferStrings {

    /// The control, before it is tapped. Android's sheet row is
    /// `"save to device"` (`VideoFeed.kt:459`), and the same words are used
    /// here so the two apps read alike.
    public static let saveToDevice = "save to device"

    /// While the bytes are arriving and nothing knows how many there are —
    /// a server that sent no `Content-Length`.
    public static func saving(_ name: String) -> String { "saving \"\(name)\"…" }

    /// While the bytes are arriving and the server said how many.
    ///
    /// The percent is whole, as Android's upload notification is
    /// (`PhoneUpload.kt:52`): a tenth of a percent on a phone screen is
    /// noise that never stops moving.
    public static func savingPercent(_ name: String, _ percent: Int) -> String {
        "saving \"\(name)\"… \(percent)%"
    }

    /// The button that stops it. A word and not a glyph: it sits beside a
    /// sentence, and an X beside a filename is the shape of a delete.
    public static let cancel = "stop saving"

    /// What a cancelled save says. Not an error — the person asked for it —
    /// so the control simply goes back to offering the save, and this is the
    /// toast that says the half-written copy is gone.
    public static let cancelled = "stopped saving — nothing was kept"

    /// A save that could not be made. `why` is ``FilesModel/why(_:)``'s
    /// wording, which is the same sentence every other failed read on these
    /// screens gets.
    public static func couldNotSave(_ why: String) -> String { "couldn't save: \(why)" }

    /// The file arrived but could not be written onto this phone — no space,
    /// or a container the system would not let the app write into.
    public static let couldNotWrite = "couldn't write it onto this phone — check your storage"

    // MARK: - the upload side

    /// While a phone file is on its way up and its size is known.
    ///
    /// Only a percent, because the row's own name is beside it: the whole
    /// sentence is ``PhoneMediaStrings/uploadingTo(_:_:)``'s toast, said once
    /// when the upload starts.
    public static func uploadPercent(_ percent: Int) -> String { "\(percent)%" }
}
