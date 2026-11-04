import Foundation

/// The write side of the saved phone bar, as a seam this item can reach.
///
/// ### Why this exists at all
///
/// ``ShellStore`` declares `var phoneBar: String? { get }` and no setter
/// (`AppContainer.swift:53`), and `AppContainer.swift:75` lists "the write
/// side of `phoneBar`" among the things part 2 deliberately left out —
/// because the editor that writes it is Settings → App layout, which is this
/// item. Both implementations already have the method and both already
/// persist (``InMemoryShellStore/setPhoneBar(_:)``,
/// `AppContainer.swift:252`; ``SavedShellStore/setPhoneBar(_:)``,
/// `SavedShellStore.swift:308`); only the **protocol** is missing it, and
/// `AppContainer.store` is typed `any ShellStore` (`AppContainer.swift:434`),
/// so the setter cannot be reached through the type.
///
/// The honest fix is one line in `ShellStore`, and this item may not edit
/// that file (NIGHT-RULES §2). It is written up as **Needs a shared change**
/// in `design-notes/ios/overnight/systemsettings/DECISIONS.md`, and a
/// reviewer should fold it in before this merges: the day `ShellStore` gains
/// `setPhoneBar`, this file and
/// ``systemSettingsBarWriter(of:)``'s callers go away and nothing else
/// changes.
///
/// Until then: a one-method protocol, both existing stores conformed to it
/// (same module, so no `@retroactive` and no risk of a conformance someone
/// else also declares), and a cast.
///
/// ### What is not, and why there is no control for it
///
/// A store that is **neither** of the two — none exists today, and a
/// `#if DEBUG` preview double would be the first — answers `nil` here, and
/// ``SystemSettingsLayoutModel/canEdit`` is then false. The panel draws no
/// move, remove or add control at all in that state. Drawing them disabled
/// would at least be honest; drawing them live over a store that cannot save
/// is the misleading control Kamron has ruled out twice, and an editor whose
/// edits vanish on the next launch is exactly that.
public protocol SystemSettingsBarWriter: Sendable {

    /// This phone's bar as the store keeps it: ``PhoneBar/encode(_:)``'s JSON
    /// array of route **names**, or nil for "never saved" (the default bar).
    ///
    /// Not `[Route]`, for the reason ``ShellStore/phoneBar`` gives: the raw
    /// string is what Android's `Prefs` key `phone_bar` holds
    /// (`data/Prefs.kt:81`), and ``PhoneBar/decode(_:)`` is the one place a
    /// name this build does not know is dropped.
    func setPhoneBar(_ raw: String?)
}

extension SavedShellStore: SystemSettingsBarWriter {}
extension InMemoryShellStore: SystemSettingsBarWriter {}

/// The writer behind `store`, or nil where there is none.
///
/// A free function and not a `ShellStore` extension, because an extension
/// with a default implementation would answer *something* for every store and
/// hide the one case this is here to surface.
public func systemSettingsBarWriter(of store: any ShellStore) -> (any SystemSettingsBarWriter)? {
    store as? any SystemSettingsBarWriter
}
