import Foundation

/// Every word the "on this phone" side says, transcribed from the Kotlin.
///
/// The sources are `ui/screens/local/LocalMediaScreens.kt`,
/// `ui/screens/local/DeviceUpload.kt`, `ui/screens/local/YourDevice.kt` and
/// `ui/screens/files/PhoneFiles.kt`. Where a sentence had to change because
/// this phone does something Android's does not — the upload ceiling, the
/// limited-photo-access state iOS has and Android has no equivalent of, the
/// absence of a handset audio player — it is marked **iOS-only** and recorded
/// in `design-notes/ios/overnight/phonemedia/IOS-ONLY-STRINGS.md`.
public enum PhoneMediaStrings {

    // MARK: - the two pages' headers

    /// `LocalMediaScreens.kt:122`.
    public static let musicTitle = "Music"
    /// `LocalMediaScreens.kt:283`.
    public static let videosTitle = "Videos"

    /// The local shell's own subtitle, where there is no server to upload to
    /// (`LocalMediaScreens.kt:119`, `:277`).
    public static let unconnectedSubtitle = "on this device — connect to a domovoi for your full library"

    /// `YourDevice.kt:264`.
    public static let deviceMusicSubtitle = "on this device — upload sends a track to the server's music library"

    /// `YourDevice.kt:271`.
    public static let deviceVideosSubtitle = "on this device — upload sends a video to the server"

    /// `PhoneFileSource.kt:79`.
    public static let phoneFilesSubtitle = "on this phone — folders you add, and your photos"

    // MARK: - the permission gate

    /// `LocalMediaScreens.kt:106`.
    public static let permissionNeeded = "domovoi needs permission to read media on this device"
    /// `LocalMediaScreens.kt:108`.
    public static let allowAccess = "allow access"
    /// `LocalMediaScreens.kt:129`.
    public static let noAccessToMusic = "no access to device music"
    /// `LocalMediaScreens.kt:287`.
    public static let noAccessToVideos = "no access to device videos"

    /// iOS has a third state Android does not: the person chose **some**
    /// photos rather than all of them (`PHAuthorizationStatus.limited`). Not
    /// saying so would show a near-empty library that reads as broken.
    /// **iOS-only.**
    public static let limitedPhotoAccess = "you've shared some photos with domovoi — the rest aren't visible here"
    /// The control that opens iOS's own "select more photos" sheet.
    /// **iOS-only.**
    public static let chooseMorePhotos = "choose more photos"

    // MARK: - the lists

    /// `LocalMediaScreens.kt:171`.
    public static let filterPlaceholder = "filter…"
    /// `LocalMediaScreens.kt:177`.
    public static let readingLibrary = "reading device library…"
    /// `LocalMediaScreens.kt:315`.
    public static let readingVideos = "reading device videos…"
    /// `LocalMediaScreens.kt:180`.
    public static let noTracksMatch = "no tracks match"
    /// `LocalMediaScreens.kt:180`.
    public static func filterIs(_ needle: String) -> String { "filter = \"\(needle)\"" }
    /// `LocalMediaScreens.kt:181`.
    public static let noMusicOnDevice = "no music on this device"
    /// `LocalMediaScreens.kt:319`.
    public static let noVideosOnDevice = "no videos on this device"
    /// Both empty states' second line (`LocalMediaScreens.kt:182`, `:320`).
    public static let downloadsLandHere = "downloads from a domovoi land in Downloads/Domovoi and show up here"
    /// A row with neither artist nor album (`LocalMediaScreens.kt:243`).
    public static let unknownArtistAndAlbum = "unknown"
    /// A row whose duration the phone did not report
    /// (`LocalMediaScreens.kt:249`).
    public static let noDuration = "—"
    /// The word the phone's own libraries list its photos under
    /// (`PhoneFileSource.kt:116`).
    public static let photosLibrary = "photos"
    /// `PhoneFileSource.kt:81`.
    public static let openInAnotherApp = "open in another app"
    /// `PhoneFiles.kt:196`.
    public static let imageCannotBeShown = "This image can't be shown here."

    /// Why a phone audio row has no play control. **iOS-only** — Android
    /// plays a window of the phone's tracks on the handset
    /// (`LocalMediaScreens.kt:159`); this app has no handset audio engine
    /// yet, so under the owner's no-misleading-controls rule the control is
    /// left out and this line says so instead.
    public static let noPhonePlayerYet = "playing a phone track on the phone is its own milestone — this side uploads and browses"

    // MARK: - the upload button and its card

    /// `DeviceUpload.kt:344`.
    public static let upload = "upload"
    /// `DeviceUpload.kt:326`, with the percent when there is one.
    public static func uploadingLabel(_ percent: Int?) -> String {
        guard let percent else { return "uploading" }
        return "uploading \(percent)%"
    }

    /// `DeviceUpload.kt:355`, the blocked tail.
    public static let blockedTail = ". You can browse and play what's on this phone, but not upload it. " +
        "An admin lifts the block in the dashboard under Settings → Phones & browsers."
    /// `DeviceUpload.kt:358`, the read-only tail.
    public static let uploadsOffTail = ", so uploads are off. Everything else here works."

    /// One card above the list: the reason, then the tail that fits
    /// (`DeviceUpload.kt:352`).
    public static func refusedCard(reason: String, blocked: Bool) -> String {
        reason + (blocked ? blockedTail : uploadsOffTail)
    }

    // MARK: - the refusals, which are the button's own words

    /// `DeviceUpload.kt:127`.
    public static let deviceMayNotChangeFiles = "this device isn't allowed to change files"
    /// `DeviceUpload.kt:139`.
    public static let serverHasNoMusicLibrary = "this server has no Music library to upload into"
    /// `DeviceUpload.kt:140`.
    public static let musicLibraryIsReadOnly = "the server's Music library is read-only"
    /// `DeviceUpload.kt:147`. The parenthesis is only there when the read
    /// failed with a message of its own.
    public static func couldNotReadLibraries(_ why: String?) -> String {
        let detail = why?.trimmingCharacters(in: .whitespacesAndNewlines) ?? ""
        return "couldn't read the server's libraries" + (detail.isEmpty ? "" : " (\(detail))")
    }
    /// `DeviceUpload.kt:151`, and the same sentence for a file
    /// (`DeviceUpload.kt:155`).
    public static let everyLibraryIsReadOnly = "every library on the server is read-only"

    // MARK: - the toasts

    /// `DeviceUpload.kt:238`.
    public static let stillAskingTheServer = "still asking the server whether this phone may upload…"
    /// `DeviceUpload.kt:257`.
    public static func alreadyUploading(_ name: String) -> String { "\"\(name)\" is already uploading" }
    /// `DeviceUpload.kt:260`.
    public static func uploadingTo(_ name: String, _ where_: String) -> String {
        "uploading \"\(name)\" to \(where_)…"
    }
    /// `DeviceUpload.kt:196` / `PhoneUpload.kt:157`.
    public static func cannotReadOnThisPhone(_ name: String) -> String {
        "can't read \"\(name)\" on this phone"
    }
    /// `DeviceUpload.kt:198`.
    public static func uploadRefused(_ detail: String) -> String { "upload refused: \(detail)" }
    /// `DeviceUpload.kt:198`'s own fallback.
    public static let deviceMayNotUploadHere = "this device may not upload here"
    /// `DeviceUpload.kt:199`.
    public static func uploadFailed(_ detail: String) -> String { "upload failed: \(detail)" }
    /// `DeviceUpload.kt:205`.
    public static func uploadedTo(_ name: String, _ where_: String) -> String {
        "uploaded \"\(name)\" to \(where_)"
    }
    /// `DeviceUpload.kt:205`'s other half.
    public static let nothingWasUploaded = "nothing was uploaded"
    /// `DeviceUpload.kt:206`.
    public static func skippedTail(_ count: Int) -> String { " · \(count) skipped" }
    /// `DeviceUpload.kt:207`.
    public static let indexingTail = " · indexing…"

    /// The name the Music door is called in a toast (`DeviceUpload.kt:69`).
    public static let musicDestination = "Music"

    // MARK: - the ceiling, which Android has not got

    /// A row the app will not pull into memory gets **no upload control** and
    /// this line, which is ``FileStrings/tooBigToSave(_:)``'s pattern the
    /// other way round. **iOS-only** — Android streams the bytes from the
    /// content uri and has no ceiling (`PhoneUpload.kt:29`).
    public static func tooBigToUpload(_ size: Int64) -> String {
        "too big to upload from a phone (\(fmtBytes(size))) — on the dashboard"
    }

    // MARK: - the switch

    /// `YourDevice.kt:215`. The other segment is the server's own title,
    /// which the shell knows and this enum does not.
    public static let yourDevice = "your device"

    /// What VoiceOver says for the **server's** segment, whose visible words
    /// are the house's address.
    ///
    /// Apple's audit: "the accessibilityLabel of this element is not
    /// human-readable" against `phone-source-server` labelled
    /// `127.0.0.1:6601` (train S006). `ShellModel.serverLabel` is always
    /// `ServerCredentials.address(_:)`, so this segment is the second place
    /// in the app where an address is the whole of an element's label — the
    /// first was the forget-server card, and the wording that cleared the
    /// finding there (``forgetServerAddressLabel(_:)``, 2026-10-30) is the
    /// wording used here. **The words on screen are unchanged**: the segment
    /// still shows the address, which is what a person compares with the
    /// dashboard. **iOS-only** — Android's segment has no separate
    /// content description (`YourDevice.kt:212`).
    public static func serverSideLabel(_ title: String) -> String {
        "the server at \(title)"
    }

    /// What VoiceOver says for the **collapsed** switch — the one row
    /// ``PhoneSourceCollapsedSwitch`` draws at an accessibility type size,
    /// where two segments across a phone cost the page 164 of its 852 points.
    ///
    /// The row shows one side's name and offers the other in a menu, so its
    /// label has to say which of the two the name belongs to; the name on its
    /// own ("127.0.0.1:6601") reads as a heading rather than as a control.
    /// Over ``serverSideLabel(_:)`` or ``yourDevice``, never over the
    /// truncated glyphs on screen.
    ///
    /// **iOS-only.** Android's switch never collapses — it is a fixed 40 dp
    /// row whose segments ellipsise (`YourDevice.kt:204`, `:251-252`) — so
    /// there is no Kotlin wording to port. `sourceswitch-r4.md` records it.
    public static func showingSide(_ side: String) -> String { "showing \(side)" }

    /// The collapsed switch's hint: what the menu behind it is for.
    /// **iOS-only**, for ``showingSide(_:)``'s reason.
    public static let pickWhichSide = "choose which side this page shows"

    // MARK: - saving to this phone

    /// The second destination this item adds beside the share sheet.
    /// **iOS-only** — Android hands the file to `DownloadManager`
    /// (`ServerFileTransfer.download`), which has no "Photos" notion.
    public static let saveToPhotos = "save to Photos"
    /// `PhoneSaveToPhotos` refuses anything that is not a picture or a video,
    /// because Photos holds nothing else. **iOS-only.**
    public static let onlyPicturesAndVideosGoToPhotos = "Photos takes pictures and videos — everything else saves through the share sheet"
    /// **iOS-only.**
    public static func savedToPhotos(_ name: String) -> String { "saved \"\(name)\" to Photos" }
    /// **iOS-only.**
    public static func couldNotSaveToPhotos(_ why: String) -> String { "couldn't save to Photos: \(why)" }
    /// The person said no to the add-only prompt. **iOS-only.**
    public static let photosAccessRefused = "domovoi may not add to Photos — Settings › Privacy › Photos"

    // MARK: - the folders the person adds

    /// The picker's door (`PhoneFiles.kt:84`'s `addPhoneFolder`).
    public static let addAFolder = "add a folder"
    public static let removeFolder = "remove"
    /// Android's own promise, said out loud because "remove" reads as a
    /// delete (`PhoneFolders.kt:44`).
    public static let removingKeepsTheFiles = "removing a folder here leaves everything in it alone"
    /// Android's "null when Android won't persist it" (`PhoneFiles.kt:98`) is
    /// a bookmark that will not resolve here. **iOS-only.**
    public static let folderCouldNotBeKept = "this phone wouldn't keep access to that folder — pick it again from Files"
    /// A listed folder whose bookmark no longer resolves: the volume is gone,
    /// the folder was deleted, or the app's data was cleared. **iOS-only.**
    public static let folderNoLongerReadable = "no longer readable — remove it, or pick it again"

    /// `SendHomeWords.of(name:asUpload:)` (`PhoneFiles.kt:283`), both
    /// vocabularies. Only the `asUpload` half has a caller in this item; the
    /// "send" half is the local shell's, which has no server to send to yet,
    /// and is kept so the two apps' words stay in step.
    public static func sendHomeTitle(_ name: String, asUpload: Bool) -> String {
        asUpload ? "upload \"\(name)\" to…" : "send \"\(name)\" to…"
    }
    public static func sendHomeAction(asUpload: Bool) -> String {
        asUpload ? "upload here" : "send here"
    }
    public static func sendHomeVerb(asUpload: Bool) -> String {
        asUpload ? "upload" : "send"
    }

    // MARK: - the two doors that need no permission (iOS-only)

    /// The PhotosUI door. **iOS-only**: Android has no such control, because
    /// there "upload" is a button on a row of a list it read with a media
    /// permission (`LocalMediaScreens.kt:204`), so picking and uploading are
    /// one gesture. IOS-ONLY-STRINGS records both of these.
    public static let pickAPhoto = "upload a photo"

    /// The document-picker door. **Not**
    /// ``openInAnotherApp``, which is Android's label for the open-with
    /// action on a file you are already looking at (`PhoneFileSource.kt:81`)
    /// — the opposite direction from this one.
    public static let pickAFile = "upload a file"

    /// What the person is looking at while the picker's bytes come across.
    /// Deliberately not ``uploadingTo(_:_:)``: nothing has been sent yet, and
    /// the destination is not known until the uploader's rules have answered,
    /// so a "to photos…" here told people their file was going to the phone's
    /// own photo library.
    public static func readingFromThisPhone(_ name: String) -> String {
        "reading \"\(name)\" from this phone…"
    }

    /// A picture row's VoiceOver hint. **iOS-only**: Android's row has no
    /// hint, and the three tap targets on the device side of Files
    /// (`folder`, `image`, `video`) each need their own word — one hint for
    /// all of them said "play" on a photograph.
    public static let lookAtThePicture = "look at the picture"
}
