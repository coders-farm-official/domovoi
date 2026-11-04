import Foundation

/// Every word the MusicMore batch adds to Music — playlists, lyrics, cover
/// art, casting and the phone's own player.
///
/// Gathered here for ``MusicStrings``' reason: Android's are literals at
/// their call sites, and one place to reword is the only way `grep` finds
/// every use. Each one is ported word for word from the Kotlin named beside
/// it; a sentence this app says differently is in
/// `overnight/musicmore/IOS-ONLY-STRINGS.md` with the reason.
///
/// Separate from ``MusicStrings`` rather than appended to it: twenty branches
/// are adding files to this module tonight and an enum two of them extend is
/// a merge conflict in a file nobody owns.
public enum MusicMoreStrings {

    // MARK: - the two new tabs

    public static let tabPlaylists = "playlists"
    public static let tabPlayer = "player"

    // MARK: - cover art (`player/CoverArt.kt`)

    /// What VoiceOver says for a cover on a **sheet**, where there is one of
    /// them and whether the song has art is worth being told. Android hides
    /// the cover from TalkBack everywhere and so does this on a list; these
    /// two sentences are iOS-only (IOS-ONLY-STRINGS.md).
    public static let coverArt = "cover art"
    public static let noCoverArt = "no cover art"

    // MARK: - the playlists tab (`PlaylistsTab.kt`)

    /// `PlaylistsTab.kt:49-53`, both lines.
    public static let noPlaylists = "no playlists yet"
    public static let noPlaylistsNote =
        "tap + on a library row to start one, or say \"make a new playlist called X\""

    /// A row's second line — `PlaylistsTab.kt:114`'s three-way `when`, in its
    /// order: the description if there is one, then the virtual playlist's
    /// own sentence, then the count.
    ///
    /// The order is the point. A description and a count are both true of the
    /// same row, and the one worth the line is the one a person wrote.
    public static func playlistSubtitle(
        description: String?,
        isVirtual: Bool,
        trackCount: Int
    ) -> String {
        if let description, !description.trimmingCharacters(in: .whitespacesAndNewlines).isEmpty {
            return description
        }
        if isVirtual { return derivedFromFavorites }
        return trackCountText(trackCount)
    }

    public static let derivedFromFavorites = "derived from your favorites"

    /// `"3 tracks"`, `"1 track"` — `PlaylistsTab.kt:116`'s pluralisation.
    public static func trackCountText(_ count: Int) -> String {
        count == 1 ? "1 track" : "\(count) tracks"
    }

    public static let play = "play"
    public static let shuffleAll = "shuffle all"

    /// `MusicScreen.kt:334` — the row's play button with no room reported.
    public static func noRoomsForPlaylist(_ name: String) -> String {
        "no rooms online — set up a room to play \(name)"
    }

    /// `MusicScreen.kt:336` / `PlaylistDrawer.kt:100`, ellipsis included: the
    /// request has gone and the room has not answered yet.
    public static func playingPlaylist(_ name: String, room: String) -> String {
        "playing \(name) in \(room)…"
    }

    public static func shufflingPlaylist(_ name: String, room: String) -> String {
        "shuffling \(name) in \(room)…"
    }

    /// `PlaylistDrawer.kt:107` names the failure after what was asked for.
    public static func playPlaylistFailed(_ reason: String) -> String {
        "play failed: \(reason)"
    }

    public static func shufflePlaylistFailed(_ reason: String) -> String {
        "shuffle failed: \(reason)"
    }

    // MARK: - the playlist sheet (`PlaylistDrawer.kt`)

    /// `PlaylistDrawer.kt:178`: `"playlist · #4"`, or `"virtual · #0"`.
    public static func playlistEyebrow(id: Int64, isVirtual: Bool) -> String {
        "\(isVirtual ? "virtual" : "playlist") · #\(id)"
    }

    /// `play on this device` (D46).
    ///
    /// The whole phrase, not `play`, because this sits beside the room chips
    /// and `play` there means "play in the room you picked". Android's
    /// equivalent says the same words in its toast
    /// (`MusicScreen.kt:583`: *playing "X" on this device*) and has no button
    /// of its own, because its playlist drawer plays to rooms only.
    public static let playHere = "play on this device"

    /// `shuffle on this device` — the phone twin of ``shuffleAll``.
    public static let shuffleHere = "shuffle on this device"

    /// The section label over the two phone buttons.
    ///
    /// **`this device`: the thing, not the verb.** Three wordings have stood
    /// here. It was `play on this phone`, which disagreed with its own two
    /// buttons about what the handset is called; the fix pass made it `play
    /// on this device`, which agreed with them **exactly** — the same
    /// sentence as the button directly beneath it, so VoiceOver read it
    /// twice and the sheet had two elements with one label, which is the trap
    /// this track has already paid for ([[audit-reports-one-issue]]: two
    /// controls sharing a label). This is the third: Android's own word for
    /// the handset where it labels one, `Text("this device")`
    /// (`ui/screens/music/PlayerTab.kt:408`) — `device` as
    /// `MusicScreen.kt:583` has it, and no repeat of either button.
    ///
    /// Android has no heading over its phone controls at all, so this is
    /// iOS-only (IOS-ONLY-STRINGS §7) and the alternative was to draw none:
    /// the buttons do say where they play. It is kept because it is what
    /// separates them from the room chips under them, and a sighted reader
    /// has nothing else to read the grouping from.
    public static let playHereLabel = "this device"

    /// What a start on this phone says (`MusicScreen.kt:583`).
    ///
    /// One spelling of it: ``PlayerStrings/playingNamedOnThisDevice(_:)`` is
    /// the same sentence, and the player uses it as the first half of the
    /// combined `… · paused kitchen` note. Two literals would drift, and the
    /// drift would show as one tap saying two different things.
    public static func playingHere(_ title: String) -> String {
        PlayerStrings.playingNamedOnThisDevice(title)
    }

    public static let playInRoomLabel = "play in room"
    /// `PlaylistDrawer.kt:222` — the section label says the room list is
    /// empty rather than offering a picker with nothing in it.
    public static let playInRoomNoRooms = "play in room · no rooms online"

    public static let edit = "edit"
    public static let cancel = "cancel"
    public static let save = "save"
    public static let namePlaceholder = "name"
    public static let descriptionPlaceholder = "description (optional)"
    public static let emojiPlaceholder = "emoji"

    /// `PlaylistDrawer.kt:288` — `it.take(4)`, counted in **grapheme
    /// clusters** here, which is what `String.prefix` and `String.count` mean.
    public static let emojiMaxLength = 4

    /// What the **server** counts.
    ///
    /// `PlaylistPatch.cover_emoji` is `max_length=16` and Pydantic measures
    /// code points, so four grapheme clusters are not four of anything the
    /// server recognises: a family emoji is seven code points, and four of
    /// them are 4 to `String.count` and 28 to the server — a PATCH that comes
    /// back 422 and an edit that reports `update failed: 422 …` with nothing
    /// a person could do about it. Android's `it.take(4)` takes UTF-16 units,
    /// which can never exceed 16, so it never had this.
    public static let emojiMaxScalars = 16

    /// The emoji trimmed and clamped to what may be sent.
    public static func clampEmoji(_ raw: String) -> String {
        clampEmojiLength(raw.trimmingCharacters(in: .whitespacesAndNewlines))
    }

    /// Both clamps and **no trim**, for the field as it is typed: trimming
    /// there would eat a space the moment it was pressed.
    public static func clampEmojiLength(_ raw: String) -> String {
        let clusters = String(raw.prefix(emojiMaxLength))
        guard clusters.unicodeScalars.count > emojiMaxScalars else { return clusters }
        return String(String.UnicodeScalarView(clusters.unicodeScalars.prefix(emojiMaxScalars)))
    }

    public static let playlistUpdated = "playlist updated"
    public static func updateFailed(_ reason: String) -> String { "update failed: \(reason)" }

    public static let moveUp = "move up"
    public static let moveDown = "move down"
    public static let removeFromPlaylist = "remove from playlist"

    public static func reorderFailed(_ reason: String) -> String { "reorder failed: \(reason)" }

    /// `PlaylistDrawer.kt:166`.
    public static func removedFromPlaylist(_ title: String, playlist: String) -> String {
        "removed \"\(title)\" from \(playlist)"
    }

    public static func removeFromPlaylistFailed(_ reason: String) -> String {
        "remove failed: \(reason)"
    }

    /// `PlaylistDrawer.kt:313-316`, both lines, and the second one differs for
    /// the virtual playlist because the way to fill it is different.
    public static let noTracksYet = "no tracks yet"
    public static let favoriteATrackToAddIt = "favorite a track to add it here"
    public static let tapPlusToAddTracks = "tap + on a library row to add tracks"

    public static let deletePlaylist = "delete playlist"
    public static let delete = "delete"

    /// `PlaylistDrawer.kt:401` — the second sentence is the one that matters:
    /// deleting a playlist is not deleting songs.
    public static func deletePlaylistBody(_ name: String) -> String {
        "Delete playlist \"\(name)\"? Tracks stay in the library."
    }

    public static func deletedPlaylist(_ name: String) -> String { "deleted \(name)" }
    public static func deletePlaylistFailed(_ reason: String) -> String {
        "delete failed: \(reason)"
    }

    // MARK: - add to playlist (`AddToPlaylistSheet.kt`)

    public static let addToPlaylist = "add to playlist"
    /// `AddToPlaylistSheet.kt:191`'s row subtitle for a playlist this track is
    /// already in.
    public static let inThisPlaylist = "in this playlist"
    public static let noPlaylistsCreateOne = "no playlists yet — create one below"
    public static let createNewPlaylist = "create new playlist"
    public static let playlistNamePlaceholder = "playlist name…"
    public static let create = "create"

    public static func addedToPlaylist(_ name: String) -> String { "added to \(name)" }
    public static func removedFromPlaylistShort(_ name: String) -> String {
        "removed from \(name)"
    }

    /// The Favorites row's own wordings — it is not a playlist, so its toast
    /// is not a playlist's (`AddToPlaylistSheet.kt:89`).
    public static func favorited(_ title: String) -> String { "favorited \"\(title)\"" }
    public static func unfavorited(_ title: String) -> String { "unfavorited \"\(title)\"" }

    /// `AddToPlaylistSheet.kt:131` for the single add; the wording is the
    /// bulk add's, because a 409 is not a failure.
    ///
    /// `MusicScreen.kt:255-264` counts "already"/"409" apart from a real
    /// failure and says "added 3 tracks, 1 already in". A single add that 409s
    /// says the second half of that and nothing about failing — a track that
    /// is already there is the state the person asked for.
    public static func alreadyInPlaylist(_ title: String, playlist: String) -> String {
        "\"\(title)\" is already in \(playlist)"
    }

    public static func addToPlaylistFailed(_ reason: String) -> String { "failed: \(reason)" }

    /// `AddToPlaylistSheet.kt:120`: the create and the add are one gesture, so
    /// they are one sentence.
    public static func createdAndAdded(_ name: String, title: String) -> String {
        "created \(name) · added \"\(title)\""
    }

    public static func createFailed(_ reason: String) -> String { "create failed: \(reason)" }

    /// The 201 answered without an id. Android `error("no playlist id
    /// returned")`s and shows the throw's message
    /// (`AddToPlaylistSheet.kt:118`); this is that message, in words a person
    /// can act on — the playlist probably exists, so the thing to do is look.
    public static let createGaveNoID =
        "the house didn't say which playlist it made — check the playlists tab"

    // MARK: - lyrics (`LyricsPanel.kt`, `player/Lyrics.kt`)

    public static let lyrics = "lyrics"
    public static let showLyrics = "show lyrics"
    public static let hideLyrics = "hide lyrics"

    /// `LyricsPanel.kt:431-433`, one per state. Each is a different true
    /// sentence rather than one vague one, which is the whole reason
    /// ``MusicLyricsDoc/state()`` has five cases.
    public static let instrumental = "instrumental — no words to show"
    public static let lookingForLyrics = "looking for lyrics…"
    public static let noLyricsForThisSong = "no lyrics for this song"

    /// `LyricsPanel.kt:423` and its one control.
    public static let lyricsFailed = "couldn't load the lyrics"
    public static let retry = "retry"

    /// `LyricsPanel.kt:124` — a gap line's accessibility description. A blank
    /// line read out as nothing is a line VoiceOver skips.
    public static let instrumentalBreak = "instrumental break"

    /// `LyricsPanel.kt:550` — a tap on a line seeks, and only where seeking
    /// is possible.
    public static let playFromHere = "play from here"

    public static let follow = "follow"
    public static let followTheSongAgain = "follow the song again"

    /// The room nudge row (`LyricsPanel.kt:628-630`). The chips carry the
    /// quarter-second step as a fraction, as Android's do.
    public static let lyricsEarlier = "lyrics earlier"
    public static let lyricsLater = "lyrics later"
    public static let earlierChip = "−¼ s"
    public static let laterChip = "+¼ s"
    public static let resetChip = "reset"
    public static let resetLyricsTiming = "reset lyrics timing"

    // MARK: - casting (`player/CastPlan.kt`)

    public static let cast = "cast to a room"
    public static func castingTo(_ room: String) -> String { "casting to \(room)" }

    /// `CastPlanner.refusal(plan)`'s three branches, word for word
    /// (`CastPlan.kt:131-139`).
    public static let castPhoneOnly =
        "songs saved on this phone can't play in a room: the domovoi can't "
        + "read this phone's storage. rooms play from the domovoi's library."
    public static let castPhoneOnlyAndOther =
        "nothing from here on can play in a room: songs saved on this phone "
        + "stay on the phone, and only library songs can be cast."
    public static let castLibraryOnly = "only library songs can be cast to a room."

    /// `CastPlanner.refusal(item)` (`CastPlan.kt:143-148`).
    public static func castItemPhoneOnly(_ title: String) -> String {
        "\"\(title)\" is saved on this phone only; a room can't play it."
    }

    public static func castItemNotInLibrary(_ title: String) -> String {
        "\"\(title)\" isn't in the library; only library songs can be cast."
    }

    /// `CastPlanner.failureNote` (`CastPlan.kt:179-201`). Never the server's
    /// raw reply: the toast used to read `cast failed: 502 Bad Gateway:
    /// {"detail":"MPD error: …"}`.
    public static let castNothingToCast = "nothing here can play in a room"
    public static let castCouldNotComeBack = "couldn't switch back to this device"

    public static func castFailedMusicPlayer(_ room: String) -> String {
        "couldn't cast to \(room): its music player on the domovoi server isn't answering "
            + "(try again in a minute; if it keeps failing, restart the domovoi)"
    }

    public static func castFailedSatellite(_ room: String) -> String {
        "couldn't cast to \(room): no room has connected to the domovoi yet, "
            + "so there's no speaker to play on"
    }

    public static func castFailedPairing(_ room: String) -> String {
        "couldn't cast to \(room): this phone needs pairing with the domovoi again"
    }

    public static func castFailedNotInLibrary(_ room: String) -> String {
        "couldn't cast to \(room): none of these songs are in the library now "
            + "(try a library rescan)"
    }

    public static func castFailedServer(_ room: String, status: Int) -> String {
        "couldn't cast to \(room): the domovoi couldn't start it (it said \(status))"
    }

    public static func castFailedStatus(_ room: String, status: Int) -> String {
        "couldn't cast to \(room) (the domovoi said \(status))"
    }

    public static func castFailedUnreachable(_ room: String) -> String {
        "couldn't cast to \(room): the domovoi can't be reached (offline?)"
    }

    public static func castFailedPlain(_ room: String) -> String { "couldn't cast to \(room)" }

    // MARK: - the phone's own player (`PlayerTab.kt`)

    /// `PlayerTab.kt:169-171`, both lines.
    public static let nothingQueued = "nothing queued"

    /// What to do about an empty queue.
    ///
    /// **It names the playlists tab, not the library tab.** It said "from the
    /// library tab" while nothing anywhere could queue to this phone, and the
    /// 2026-11-03 batch made that the one wrong word on the screen: the
    /// control that queues to the handset is `play on this device` in a
    /// playlist's sheet (D46), and the library row's sheet still casts to a
    /// room only. A note pointing at a tab with no such control is the same
    /// misleading instruction as a button that does nothing.
    public static let nothingQueuedNote =
        "open a playlist and play it on this device to start"

    public static func queueHeader(_ count: Int) -> String { "queue · \(count)" }
    public static let saveAsPlaylist = "save as playlist"
    public static let clear = "clear"
    public static let saveQueueAsPlaylist = "save queue as playlist"

    public static let speed = "speed"
    public static let sleep = "sleep"
    public static let endOfTrack = "end of track"
    public static let cancelSleepTimer = "cancel sleep timer"

    /// `PlayerTab.kt:654`: `1x`, not `1.0x`; `1.25x` keeps its fraction.
    public static func speedLabel(_ value: Double) -> String {
        let whole = value.rounded()
        if abs(value - whole) < 0.0001, let exact = Int(exactly: whole) { return "\(exact)x" }
        return "\(value)x"
    }

    /// `"15m"` — `PlayerTab.kt:505`.
    public static func sleepMinutes(_ minutes: Int) -> String { "\(minutes)m" }

    /// `ChapterRow`'s fallback for a chapter with no title
    /// (`PlayerTab.kt:552`), one-based as it is shown.
    public static func chapterFallback(_ index: Int) -> String { "Chapter \(index + 1)" }
}
