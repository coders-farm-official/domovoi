import Foundation

/// Every word the Images screen says, transcribed from the Kotlin with the
/// file and line each came from — the shape ``ChatStrings`` and
/// ``FileStrings`` have.
///
/// Anything here that is **not** Android's own sentence is marked
/// **iOS-only** and recorded, with its reason, in
/// `design-notes/ios/overnight/images/IOS-ONLY-STRINGS.md`. There are
/// **four**, each marked below and recorded with its reason there (§3, §5,
/// §7, §8).
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

    /// The **dialog's** dismiss button — `ui/components/Dialogs.kt:41`, where
    /// every `ConfirmDialog` in the Android app gets the word.
    ///
    /// The same word as ``cancel`` and deliberately a second constant: one of
    /// them is "stop making this picture" and the other is "don't delete
    /// anything", they come from two different Kotlin files, and either could
    /// be reworded without the other. Every other screen in this app keeps
    /// its own (``ChatStrings/renameCancel``, ``MusicStrings/cancel``,
    /// ``ShellStrings/trustCancel``), which is the shape being followed.
    public static let dialogCancel = "cancel"

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

    /// **iOS-only.** One tile's screen-reader label.
    ///
    /// Android's `contentDescription` is the file's own name (`:407`), and
    /// that is what this said until Apple's audit answered
    /// `Label not human-readable` against `kingfisher-02.png`
    /// (train S001, 2026-11-02): a label VoiceOver spells out character by
    /// character is not a name a person hears. The file name is not lost —
    /// it is the tile's ``accessibilityValue`` and the picture sheet's own
    /// title — so a tile still says which file it is, after saying what it
    /// is. IOS-ONLY-STRINGS §8.
    ///
    /// **The position, and never "of n".** The card draws at most three of a
    /// generation's pictures and a generation can hold four
    /// (``deleteBody(images:)`` counts them all), so "picture 2 of 3" would
    /// be a count of the tiles claiming to be a count of the pictures.
    public static func tile(position: Int) -> String { "picture \(position)" }

    /// **iOS-only**, asked for by Kamron (answer 27, 2026-11-03): a card with
    /// more pictures than it shows says so.
    ///
    /// Android draws three tiles and says nothing, so a four-image generation
    /// looks exactly like a three-image one and the fourth picture is
    /// unreachable *and* unmentioned. His words, verbatim:
    /// `4 images, showing 3`. IOS-ONLY-STRINGS §9.
    ///
    /// **Drawn only when there is something to say** — the card calls this
    /// only for `total > shown` (``ImagesJob/hasMoreImagesThanTiles``), which
    /// with ``ImagesJob/tileNames``' cap of three means `total` is 4 or more.
    /// So the plural is always right and there is no singular form to get
    /// wrong; a `1 images, showing 1` is unreachable rather than unhandled.
    public static func moreImages(total: Int, shown: Int) -> String {
        "\(total) images, showing \(shown)"
    }

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

    // `pluginBadge` ("plugin", `PluginFrame.kt:152`) stood here and is gone:
    // one view draws both bands since owner answer 58, and its copy of the
    // word is ``PluginBandStrings/badge``, which `ShellFoldTests` pins.
    // `by(_:)`, `manage` and `manageLabel(name:)` below are NOT dead —
    // ``PluginBand/publisherLine`` and ``PluginBand/manageLabel`` read them
    // (`ImagesModels.swift:605`, `:610`), which `ImagesTests` asserts.

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

    /// **iOS-only.** The picture decoded and the phone could not write the
    /// copy an activity sheet reads from. Android never reaches this: it
    /// writes into a `FileProvider` cache and reports the *whole* open
    /// through one toast (`ServerFileTransfer.kt:84`), so a write failure and
    /// a "no app opens this" are one sentence there. IOS-ONLY-STRINGS §7.
    ///
    /// No detail interpolated, and deliberately: the failure is inside this
    /// app's own container, so there is no server message to pass on and
    /// `NSError`'s own wording for a full disk is not a sentence to show
    /// somebody.
    public static let couldNotHandOverPicture = "couldn't get this picture ready to share"

    /// Also reused — ``FileStrings/close`` and ``FileStrings/retry``, named
    /// here so the next reader does not go looking for a new one.
    public static let close = FileStrings.close
    public static let retry = FileStrings.retry
}
