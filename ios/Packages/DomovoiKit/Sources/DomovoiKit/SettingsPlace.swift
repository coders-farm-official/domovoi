import Foundation

/// Where Settings is: its index, one group, or one section.
///
/// A port of `SettingsPlace`, `placeOf`, `up()` and `key` in
/// `android/.../ui/screens/settings/SettingsScreen.kt:190-224` (map §1.8),
/// read on 2026-10-29.
///
/// Settings is **not** a tab bar on either platform: it is three levels —
/// index (the five groups, plus search) → a group's sections → one section.
/// All three are one saveable string, which is what makes the level the app
/// returns to after a process death a single value rather than a pair.
///
/// The screens are a later batch's. This is the place, and the walk between
/// places, because that walk is navigation and navigation is this batch.
public enum SettingsPlace: Equatable, Sendable {
    case index
    case group(SettingsGroup)
    case section(SettingsSection)

    /// The prefix that tells a group's key from a section's. Android's
    /// `GROUP_PLACE`.
    static let groupPrefix = "group:"

    /// The place, as a string to save: `""` for the index, `"group:<key>"`
    /// for a group, else the section's own key.
    public var key: String {
        switch self {
        case .index: return ""
        case .group(let group): return Self.groupPrefix + group.key
        case .section(let section): return section.key
        }
    }

    /// One level out: a section → its group, a group → the index, the index →
    /// nowhere. This is what Android's back button does inside Settings
    /// (`SettingsScreen.kt:238`), and `nil` is where it stops handling the
    /// press.
    public var up: SettingsPlace? {
        switch self {
        case .index: return nil
        case .group: return .index
        case .section(let section): return .group(section.group)
        }
    }
}

/// The place a saved key or a link stands for.
///
/// **A group is only ever reached through the prefix**, which is what lets a
/// group's key be a section's old name: `"voice"` is both the Voice *group*'s
/// key and an alias of the Voices *section*, and an unprefixed `"voice"`
/// resolves to the section, as Android's comment says it should.
///
/// Anything unrecognised is the index, never nothing: a link naming a section
/// this build does not have lands somewhere a person can use.
public func settingsPlace(of key: String?) -> SettingsPlace {
    guard let key, !key.isEmpty else { return .index }
    if key.hasPrefix(SettingsPlace.groupPrefix) {
        let name = String(key.dropFirst(SettingsPlace.groupPrefix.count))
        guard let group = SettingsGroup.allCases.first(where: { $0.key == name }) else { return .index }
        return .group(group)
    }
    guard let section = SettingsSection.named(key) else { return .index }
    return .section(section)
}

/// The sections of one group, in the table's own order
/// (`SettingsScreen.kt:160`, `sectionsOf`).
public func sections(of group: SettingsGroup) -> [SettingsSection] {
    SettingsSection.all.filter { $0.group == group }
}
