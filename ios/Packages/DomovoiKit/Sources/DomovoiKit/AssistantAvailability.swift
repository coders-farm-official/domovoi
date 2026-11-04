import Foundation

/// Why the phone cannot answer, when it cannot.
///
/// The iOS twin of Android's two refusals put together:
/// `AssistantSupport.available` (the install has no engine for this ABI) and
/// `DeviceTier.Unsupported` (the phone has not the memory). Neither Android
/// reason has an iPhone twin — every supported iPhone is arm64, and with
/// Apple's own model there is nothing to size — so what is left is the four
/// answers the system itself gives (`ANDROID-ASSISTANT-MAP.md` §3.1, §3.2).
public enum AssistantUnavailableReason: String, Equatable, Sendable, CaseIterable {

    /// `SystemLanguageModel.Availability.UnavailableReason.deviceNotEligible`:
    /// the iPhone itself does not run Apple Intelligence.
    case deviceNotEligible

    /// `.appleIntelligenceNotEnabled`: the device could, and the person has
    /// not turned it on in Settings.
    case notEnabled

    /// `.modelNotReady`: on its way down, or waiting for power or network.
    /// The one reason that mends itself, which is why the place can appear
    /// later in the same launch without anything being done to the app.
    case modelNotReady

    /// iOS 18 to 25: `FoundationModels` is not there at all. Android has no
    /// such case — its engine ships in the APK — and M4 (a model file of our
    /// own) is what answers it.
    case osTooOld
}

/// Whether this phone can write a reply at all.
///
/// `phoneReady` (`PhoneChatPlace.kt:31-35`) answers `false` / `null` / `true`
/// off two Android facts (`tier`, `setup`); here it is one, because Apple's
/// `availability` *is* that answer. ``checking`` keeps Android's `null`: the
/// fake engine has it, and M4's download will have it again.
public enum AssistantAvailability: Equatable, Sendable {
    case checking
    case available
    case unavailable(AssistantUnavailableReason)
}

/// Whether Chat offers the phone as a place, and whether a chat can start
/// there — the iOS twin of `AssistantSupport.kt` plus `DeviceTier.kt`.
///
/// ### Android's two gates are one gate here, deliberately
///
/// `PlaceFacts` (`ChatPlace.kt:152-168`) takes `phoneEngine` **and**
/// `phoneCapable` because on Android they come apart: a 32-bit install has no
/// engine (and Settings says the 64-bit build would add it), while an 8 GB
/// floor is a fact about the phone that the person can do nothing about. The
/// place is *listed* on the first and *startable* on the second, so a phone
/// that cannot run the model still shows the chats it has.
///
/// On an iPhone there is nothing in between. `SystemLanguageModel`'s answer is
/// one runtime fact, and all four of its reasons mean the same thing to a
/// person opening Chat: no reply will be written here. So both inputs come
/// from ``phoneCanChat(_:)`` and a phone without the model gets no chip, no
/// picker and no second list — which is [[no-misleading-controls]] doing its
/// job rather than a simplification. M4, which brings a model file of our own
/// and with it a memory floor and a download, is where the two come apart
/// again: then "listed" (chats this phone already has) and "startable" (a
/// model is on the phone) are different questions, as they are on Android.
public enum AssistantSupport {

    /// This install can start a chat on the phone: the system model is here
    /// and ready. ``AssistantAvailability/checking`` answers **false** — a
    /// chip that appears a moment after Chat opens is better than one that
    /// appears and then has to be taken away.
    public static func phoneCanChat(_ availability: AssistantAvailability) -> Bool {
        availability == .available
    }

    /// `PlaceFacts.phoneEngine` (`ChatPlace.kt:160`).
    public static func phoneEngine(_ availability: AssistantAvailability) -> Bool {
        phoneCanChat(availability)
    }

    /// `PlaceFacts.phoneCapable` (`ChatPlace.kt:162`). See this type's note on
    /// why it is the same answer until M4.
    public static func phoneCapable(_ availability: AssistantAvailability) -> Bool {
        phoneCanChat(availability)
    }

    /// `phoneReady` (`PhoneChatPlace.kt:31`): nil while it is being read,
    /// which is what ``assistantDefaultPlace(choices:lastUsed:serverHasModel:)``
    /// treats as "not known yet".
    public static func phoneReady(_ availability: AssistantAvailability) -> Bool? {
        switch availability {
        case .checking: return nil
        case .available: return true
        case .unavailable: return false
        }
    }
}
