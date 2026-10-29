import DomovoiKit
import SwiftUI

/// One destination of the graph.
///
/// The counterpart of `android/.../ui/screens/ScreenRouter.kt`: a `switch`
/// over the route, with the capability check in front of it. **The screens
/// themselves are later batches'** — Home's grid, Music, Videos, Files, Chat,
/// Settings and the rest — so each is a marked placeholder that names what
/// belongs there, and nothing here pretends to be one.
///
/// The one piece of real behaviour is the check at the top, and it is
/// ported rather than invented: a route a link reached that the server does
/// not offer gets a **quiet note saying why**, never a dead screen
/// (`ScreenRouter.kt:49`, ``Route/unavailableNote(_:)``). Chat hidden because
/// the server's AI features are off says so; anything else is a plugin that
/// is not installed.
struct RouteScreen: View {

    let model: ShellModel
    let route: Route

    var body: some View {
        if !route.visibleWith(model.visibility) {
            let note = route.unavailableNote(model.capabilities)
            VStack(spacing: 8) {
                Text(note.title).font(.headline)
                Text(note.detail)
                    .font(.footnote)
                    .foregroundStyle(Color.fgMuted)
                    .multilineTextAlignment(.center)
                    .fixedSize(horizontal: false, vertical: true)
            }
            .padding(24)
        } else if route == .settings {
            SettingsPlaceholder(model: model)
        } else {
            ScreenPlaceholder(route: route, onThisPhone: false)
        }
    }
}

/// A screen a later batch builds.
///
/// It says which screen it is and that it is not built, because an empty page
/// in a shell that is otherwise working is the kind of thing that gets
/// mistaken for a loading failure.
struct ScreenPlaceholder: View {

    let route: Route
    /// The local shell's copy of a screen (the phone's own media) rather than
    /// the server's.
    let onThisPhone: Bool

    var body: some View {
        VStack(spacing: 10) {
            Image(systemName: route.systemImage)
                .font(.largeTitle)
                .foregroundStyle(Color.fgMuted)
            Text(route.label.lowercased())
                .font(.title3)
            Text(onThisPhone ? "on this phone — not built yet" : "not built yet")
                .font(.footnote)
                .foregroundStyle(Color.fgMuted)
        }
        .frame(maxWidth: .infinity, maxHeight: .infinity)
        .accessibilityElement(children: .combine)
    }
}

/// Settings, far enough to show that the **link side** of it works: a link
/// naming a section — `"rooms"`, or the old `"satellites"` and `"connection"`
/// — lands on that section, which is ``settingsPlace(of:)`` resolving it
/// through the alias table.
///
/// The three levels, the search and the fourteen panels are a later batch's.
/// What this draws is where the request landed, and takes it, so coming back
/// to Settings later starts at the index.
struct SettingsPlaceholder: View {

    let model: ShellModel
    @State private var place: SettingsPlace = .index

    var body: some View {
        VStack(alignment: .leading, spacing: 10) {
            Text(ShellStrings.settingsTitle).font(.title2)
            Text(ShellStrings.settingsSub)
                .font(.footnote)
                .foregroundStyle(Color.fgMuted)
                .fixedSize(horizontal: false, vertical: true)
            switch place {
            case .index:
                ForEach(SettingsGroup.allCases, id: \.self) { group in
                    Button(group.label) { place = .group(group) }
                }
            case .group(let group):
                Text(group.label).font(.headline)
                ForEach(sections(of: group), id: \.key) { section in
                    Button(section.label) { place = .section(section) }
                }
                Button(ShellStrings.allSettings) { place = place.up ?? .index }
            case .section(let section):
                Text(section.label).font(.headline)
                Text(section.sub)
                    .font(.footnote)
                    .foregroundStyle(Color.fgMuted)
                    .fixedSize(horizontal: false, vertical: true)
                if section.key == "phones-and-browsers" {
                    // The one section with a screen. Its first three cards
                    // are part 3's; §6.5's four are a batch of their own, as
                    // ``PhonesAndBrowsersPanel`` says and the report sizes.
                    PhonesAndBrowsersPanel(model: model)
                } else {
                    Text("not built yet")
                        .font(.footnote)
                        .foregroundStyle(Color.fgMuted)
                }
                Button(ShellStrings.backTo(section.group.label)) { place = place.up ?? .index }
            }
            Spacer()
        }
        .padding(16)
        .frame(maxWidth: .infinity, alignment: .leading)
        .onAppear {
            // The request is TAKEN, so a later visit starts at the index
            // (`SettingsScreen.kt:233-236`). An unknown key lands on the
            // index rather than nowhere.
            if let asked = model.takeSettingsTab() {
                place = settingsPlace(of: asked)
            }
        }
    }
}
