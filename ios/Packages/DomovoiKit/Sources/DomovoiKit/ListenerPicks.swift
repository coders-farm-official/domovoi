import Foundation

/// "Listening as" (Settings → Phones & browsers), the web's SpokenAudio rule
/// (web/static/player.jsx `getPerson` / `setPerson`), kept per server.
/// A port of `android/.../listens/ListenerPicks.kt` (a1e611b6):
///
///  * nothing picked: the phone follows whose it is (the server's answer,
///    "this is me"), unless it is a shared screen — then the household;
///  * a person picked: that person, until changed;
///  * "household" picked on a phone that IS someone's: the household on
///    purpose (a claimed phone lent to a guest), remembered as ``household``;
///  * picking whose the phone is again, or "household" on a phone that is
///    nobody's, goes back to following the phone.
///
/// Pure: ``PlayerListenerStore`` stores what ``toStore(_:owner:shared:)``
/// says and answers what ``effective(_:owner:shared:)`` says.
public enum ListenerPicks {

    /// The stored pick for "the household, on purpose".
    public static let household = "none"

    /// Who the phone listens as: a person id, or nil for the household.
    public static func effective(_ pick: String?, owner: Int?, shared: Bool) -> String? {
        if pick == household { return nil }
        if let pick { return pick }
        if shared { return nil }
        return owner.map(String.init)
    }

    /// What to store when `personID` (nil: household) is picked; nil means
    /// "follow the phone" (nothing stored).
    public static func toStore(_ personID: String?, owner: Int?, shared: Bool) -> String? {
        let device = shared ? nil : owner
        guard let personID else { return device == nil ? nil : household }
        if let device, personID == String(device) { return nil }
        return personID
    }
}
