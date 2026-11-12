import Foundation

/// The seam between this screen and whatever plays audio on the phone.
///
/// Android has one: `app.player.playItems(listOf(PlayItem.fromStation(...)))`
/// (`StationPlay.kt:71`), an engine that owns a queue, a now-playing item and
/// the lock screen. On iOS that engine is the `player` item's, merged at the
/// views stage — so this screen is built against a protocol rather than
/// against a type that does not exist yet, and the branch compiles and is
/// honest on its own.
///
/// ### Why `canPlay` and not just a no-op `play`
///
/// The owner's no-misleading-controls rule. A witness that cannot play is not a
/// witness that plays silently: the screen asks ``canPlay`` and, when it is
/// false, **draws no play affordance at all** — no row tap that plays, no
/// play glyph, no `play here`. A tap that POSTs `/play` and then makes no
/// sound would also stamp the station into Recent on the server, so the one
/// visible effect of a button that did nothing would be a history entry for
/// something nobody heard.
///
/// ``StationsModel`` enforces the other half: it does not send `POST /play`
/// when `canPlay` is false, because `/play` is never sent for its
/// side-effect alone.
public protocol StationsPlayback: Sendable {

    /// False when this build has no audio engine.
    var canPlay: Bool { get }

    /// Hand a **saved** station row to the engine.
    ///
    /// The row is always one the server has stamped — the model POSTs
    /// `/play` first and hands on the row that answered, which is how an
    /// unsaved directory hit gets an id. The stream is
    /// ``StationsPath/stream(_:)``; it is not seekable and its artist is
    /// `"live radio"` (`player/PlayItem.kt:57`).
    @MainActor func play(station: StationsRow)
}

/// The witness for a build with no audio engine: nothing plays, and the
/// screen draws nothing that says it would.
///
/// **The route slot no longer passes this.** D46 landed
/// `StationsPhonePlayback` over ``ShellModel/player``, and the one line in
/// `RouteScreen` changed as this note said it would. It is kept because it is
/// what every ``StationsModel`` test that is *about* the no-engine rules is
/// built on — the screen still has to draw nothing when the player is nil.
///
/// **Which is not a phone with no device id**, as an earlier draft of this
/// sentence said: the id is minted locally on first launch
/// (`DeviceRegistry.swift:391-403`), so ``ShellModel/player`` is nil only on
/// a build with no audio engine (DomovoiKit's own macOS test build) and in a
/// shell built with no `deviceSelf`. The rules this witness pins are still
/// worth pinning — a drawing mistake here is what the owner's rule forbids —
/// but no person is ever on the other side of them.
public struct StationsNoPlayback: StationsPlayback {

    public init() {}

    public var canPlay: Bool { false }

    /// Unreachable, and a `fatalError` would be the wrong answer to a view
    /// that drew a control it should not have: the rule is enforced by
    /// ``StationsModel/play(_:)``, which checks ``canPlay`` first, and a crash
    /// here would turn a drawing mistake into a lost app.
    @MainActor public func play(station: StationsRow) {}
}
