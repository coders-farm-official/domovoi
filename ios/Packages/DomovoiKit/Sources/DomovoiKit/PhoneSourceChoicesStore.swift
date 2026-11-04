import Foundation

/// Where ``PhoneSourceChoices`` lives, and how long for.
///
/// Android's `LocalSourceChoices` is provided by the **workspace** shell
/// (`YourDevice.kt:57`): one per server, kept across a trip through local
/// media and a rotation, rebuilt when the workspace is. On iOS the view that
/// wraps the three switched routes is ``RouteScreen``, and that is created
/// fresh for every pushed destination — so a `@State` on it would last as
/// long as one visit to one page, and switching Videos to the phone, going to
/// Home and coming back would find the server's side again.
///
/// ``ShellModel`` is do-not-edit (NIGHT-RULES §2), so the lifetime is kept
/// here instead, **keyed to the address** — which is the same key every
/// ``ApiRead`` and ``Pager`` in this app is keyed to, and for the same
/// reason: a choice made about one house is not a choice about the next one.
///
/// One entry, not a dictionary per server visited. Android rebuilds on a
/// workspace change rather than remembering every house it has ever been in,
/// and so does this.
@MainActor
public enum PhoneSourceChoicesStore {

    private static var server: String?
    private static var choices = PhoneSourceChoices()

    /// The choices for `server`, **reset when the address changed**.
    public static func of(server address: String) -> PhoneSourceChoices {
        if server != address {
            server = address
            choices = PhoneSourceChoices()
        }
        return choices
    }

    /// Put it back to one empty set with no address, for a test that must not
    /// inherit the last one's.
    public static func forget() {
        server = nil
        choices = PhoneSourceChoices()
    }
}
