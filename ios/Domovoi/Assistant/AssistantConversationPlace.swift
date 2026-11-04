import DomovoiKit
import SwiftUI

/// Which place the conversation on screen is in, handed down so that the
/// conversation's **own header row** can say it (D63, Kamron's answer 63:
/// "Android's way: the place goes inside each conversation's header row").
///
/// ### Why an environment value and not an argument
///
/// Android has one composable for both places' headers — `ChatHeader`
/// (`ChatHome.kt:700`) takes the chip as a parameter and every conversation
/// passes its own. Here the two conversations are two views in two
/// directories, and the view that knows the place is ``AssistantPlaceSwitch``,
/// which does not build either of them: ``ChatView`` hands its own screen in
/// as a `@ViewBuilder`, and the phone's list builds its conversation three
/// views further down. An argument would therefore have to be threaded through
/// `ChatView` (a shared-ish file this unit owns, but whose signature the
/// transfers unit's cases drive) and through `AssistantPhoneChatView`. One
/// environment value, written in exactly one place and read in exactly two, is
/// the smaller change — and it is what the environment is for.
///
/// ### Nil is the one-place phone, and that is not a missing value
///
/// Nil — the default — is every iPhone without the system model, and the whole
/// of iOS 18-25. There is one place there, and a chip saying "on home server"
/// on a screen with nowhere else to be is furniture rather than information:
/// `testTheChatListDrawsEveryThreadTheHouseHas` asserts its absence by name
/// ("a place chip was drawn on a screen with one place"), which is
/// [[no-misleading-controls]]'s own argument one step short of a control.
private struct AssistantConversationPlaceKey: EnvironmentKey {
    static let defaultValue: ChatPlace? = nil
}

extension EnvironmentValues {

    /// The place the conversation on screen is in; nil on a phone with one
    /// place. See ``AssistantConversationPlaceKey``.
    var assistantConversationPlace: ChatPlace? {
        get { self[AssistantConversationPlaceKey.self] }
        set { self[AssistantConversationPlaceKey.self] = newValue }
    }
}

/// The place chip for a conversation's header row, or nothing at all on a
/// phone with one place.
///
/// It is ``AssistantPlacePickerView``'s own one-choice chip and not a second
/// drawing of it: that file carries the measurement the chip's fill was fixed
/// by (0.10 and not 0.18, `brandInk` at 4.87:1 on it rather than 4.30:1), and
/// two chips would be two chances to get that wrong. `inHeader` is what drops
/// the band's own padding and its greed for the width, both of which the
/// header row already has.
struct AssistantConversationPlaceChip: View {

    let place: ChatPlace?

    var body: some View {
        if let place {
            AssistantPlacePickerView(
                choices: [place],
                selected: place,
                // One choice draws a chip and not a button, so nothing can
                // call this (`AssistantPlacePickerView`'s own branch).
                onPick: { _ in },
                inHeader: true
            )
        }
    }
}
