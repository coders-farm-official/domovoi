import Foundation

/// Every word the Images screen says, transcribed from the Kotlin with the
/// file and line each came from — the shape ``ChatStrings`` and
/// ``FileStrings`` have.
///
/// Anything here that is **not** Android's own sentence is marked
/// **iOS-only** and recorded, with its reason, in
/// `design-notes/ios/overnight/images/IOS-ONLY-STRINGS.md`. There are two,
/// both for a failure Android reports through a toast that this app has no
/// toast for at that moment.
///
/// The ellipsis in `describe the image…` and `working…` is U+2026, the
/// separator in the meta lines is U+00B7 with a space either side, and the
/// `·` in `  ·  recommended` has **two** spaces either side — all three
/// copied, not retyped, from `ImagesScreen.kt`.
public enum ImagesStrings {

    // MARK: - the page

    /// `ImagesScreen.kt:186`.
    public static let title = "Images"
    public static let subtitle = "generate locally on your own GPU · browse in Files"

    // MARK: - the engine line's five states

    /// `ImagesScreen.kt:192-195`. The sub is the fall-back the Kotlin uses
    /// when the read has no message of its own.
    public static let unavailableTitle = "image generation unavailable"
    public static let unavailableSub = "the Image Generation plugin didn't answer"

    /// `ImagesScreen.kt:196-199`.
    public static let notInstalledTitle = "engine not installed"
    public static let notInstalledSub = "set it up from the web dashboard's Images page (one click)"

    /// `ImagesScreen.kt:200-203`.
    public static let offlineTitle = "engine offline"
    public static let offlineSub = "start it from the web dashboard's Images page"

    /// `ImagesScreen.kt:207`.
    public static let engineRunning = "engine running"

    // MARK: - composing a generation

    /// `ImagesScreen.kt:220`.
    public static let promptPlaceholder = "describe the image…"

    /// `ImagesScreen.kt:308` — the picker's label when there is nothing to
    /// pick, and when there is something but nothing is chosen.
    public static let noModelsInstalled = "no models installed"
    public static let pickAModel = "pick a model"

    /// `ImagesScreen.kt:318`, both spaces either side of the separator.
    public static let recommendedSuffix = "  ·  recommended"

    /// `ImagesScreen.kt:238`.
    public static let generate = "generate"

    /// `ImagesScreen.kt:167` — the toast when the POST never started a job.
    public static func generateFailed(_ why: String) -> String {
        why.isEmpty ? generateFailedFallback : why
    }

    /// The Kotlin's `it.message ?: "generation failed to start"` (`:167`).
    public static let generateFailedFallback = "generation failed to start"

    // MARK: - the live lane

    /// `ImagesScreen.kt:362`.
    public static let working = "working…"

    /// `ImagesScreen.kt:353`, the cancel button's only label.
    public static let cancel = "cancel"

    /// **iOS-only.** Android's cancel is `runCatching { … }` with no
    /// `onFailure` (`ImagesScreen.kt:350`): a cancel that fails says nothing
    /// at all and the row stays where it was, which on this screen reads as a
    /// button that does nothing. IOS-ONLY-STRINGS §5.
    public static func cancelFailed(_ why: String) -> String {
        "cancel failed: \(why)"
    }

    // MARK: - history

    /// `ImagesScreen.kt:250`.
    public static let history = "history"

    /// `ImagesScreen.kt:431`, the delete button's only label.
    public static let delete = "delete"

    /// `ImagesScreen.kt:277`.
    public static let deleteTitle = "delete generation"

    /// `ImagesScreen.kt:278`, plural and all: `"Delete this generation and
    /// its $n image${if (n == 1) "" else "s"}? This can't be undone."`, with
    /// `n = job.result?.images?.size ?: 0` (`:275`) — **the count of every
    /// image in the generation**, not of the three tiles drawn.
    public static func deleteBody(images count: Int) -> String {
        "Delete this generation and its \(count) image\(count == 1 ? "" : "s")? This can't be undone."
    }

    /// `ImagesScreen.kt:175-176`.
    public static let deleted = "deleted"
    public static func deleteFailed(_ why: String) -> String {
        "delete failed: \(why)"
    }

    // MARK: - the plugin band

    /// `PluginFrame.kt:152`, the bordered badge.
    public static let pluginBadge = "plugin"

    /// `PluginFrame.kt:144`.
    public static func by(_ publisher: String) -> String { "by \(publisher)" }

    /// `PluginFrame.kt:178`, the word; and `:181`, its screen-reader label.
    public static let manage = "manage"
    public static func manageLabel(name: String) -> String {
        "manage \(name) on the dashboard's Plugins page"
    }

    // MARK: - the picture sheet

    /// **iOS-only**, and the only sentence the viewer needs that Android has
    /// no counterpart for: Android hands the file to another app
    /// (`ServerFileTransfer.open`, `ImagesScreen.kt:263`) and iOS opens the
    /// picture in the app, so a fetch that fails happens with the sheet
    /// already on screen. IOS-ONLY-STRINGS §3. The words come through
    /// ``FilesModel/why(_:)``, this app's one place for turning a
    /// ``DomovoiError`` into a sentence.
    public static func couldNotLoadPicture(_ why: String) -> String {
        "couldn't load this picture: \(why)"
    }

    /// Bytes arrived and would not decode. **Reused, not new**:
    /// ``FileStrings/notAPicture`` is this app's existing sentence for
    /// exactly this, one screen over (`FileViewers.swift:191`), and a second
    /// voice for one failure is how two screens come to disagree.
    public static let couldNotShowPicture = FileStrings.notAPicture

    /// Also reused — ``FileStrings/close`` and ``FileStrings/retry``, named
    /// here so the next reader does not go looking for a new one.
    public static let close = FileStrings.close
    public static let retry = FileStrings.retry
}
