import DomovoiKit
import SwiftUI

/// Rooms: every satellite in the house, what it is doing, and the few things
/// a phone may ask of it.
///
/// A port of `android/.../ui/screens/satellites/SatellitesScreen.kt:80`, which
/// is one composable with an `embedded` flag behind two entry points — and so
/// is this: ``RouteScreen``'s `satellites` slot draws it with its own header,
/// and Settings → Rooms draws it with `embedded: true`, which drops the header
/// the section already has (`SatellitesScreen.kt:144-148`). `navigate(.satellites)`
/// resolves to the Settings section (`Route.swift:137-139`), so that is the one
/// a person reaches; the route slot is filled with the same view so that a
/// direct `go(.satellites)` is not a dead screen.
///
/// ### What is not here, and why there is no control for it
///
/// This phone holds the **household device token** and never an admin session
/// (`require_admin_*` is Bearer or dashboard-cookie only), so each of the
/// following would 401, 403 or 501 **every time**. Under Kamron's
/// no-misleading-controls rule the control is left out and the fact is kept,
/// with one line saying where the button is. Each entry names the gate.
///
/// * **`restart`** — `web/backend/api/satellites.py:1576-1581`, admin.
/// * **`upgrade`**, Android's press ledger, its six-minute clock and its
///   outcome sentences (`SatUpgrade.kt`) — `:1618-1623`, admin security, and a
///   501 before an admin exists at all. The `needs upgrade` pill **stays**: it
///   is a fact computed from two device-tier reads
///   (``roomsNeedsUpgrade(coreSha:roomVersion:)``), and
///   ``RoomsStrings/upgradeIsOnTheDashboard`` says where the button is.
/// * **`save & restart` on the settings tab** — `PATCH …/config` is admin
///   (`:1712-1716`) while the GET is device read, so the fields are shown
///   read-only. No switches, no dropdowns, no numeric fields a tap could
///   pretend to save (``RoomsSettingsTabView``).
/// * **Starting a room-to-room drop-in** — the core's
///   `/v1/admin/dropin/start` is admin *security* (`domovoi/main.py:2649-2658`)
///   and the web hop forwards this phone's token unchanged, so Android's peer
///   picker and its `start` could only ever fail. **`hang up` is kept**:
///   `/v1/admin/dropin/end` is device tier (`main.py:2718-2721`), and ending a
///   call somebody else started is the one thing a phone in the house
///   genuinely needs here.
/// * **"Drop in from this phone"** (`PhoneDropinCall.kt`, `DropinCallClient.kt`)
///   — permitted and deferred: the app has no audio capture or playback layer
///   at all, and a button that opens a dialog stuck on `connecting…` is
///   exactly what the rule forbids.
/// * **Display controls and logs** — `:1597-1602` and `:1679-1684`, both
///   admin. Android's phone screen draws neither either.
/// * **"Use this device as a room"** (`SatellitesScreen.kt:169`) — the
///   `roommode` item's card. This screen leaves the gap rather than drawing a
///   dead one.
///
/// ### The detail replaces the roster in place, and is not pushed
///
/// Android's compact layout replaces the grid with the detail and takes the
/// system back button for it (`SatellitesScreen.kt:119-131`); its wide layout
/// puts the detail in a 440 dp pane, which this app has nowhere (there is no
/// wide layout anywhere in it — `MusicRoomCards.swift:13-16`).
///
/// A `NavigationLink` would have given the back gesture for free and is not
/// available: the workspace's stack is `NavigationStack(path:)` over `[Route]`
/// (`WorkspaceView.swift`), a typed path takes no other value, and the Settings
/// entry point is inside Settings' own `ScrollView`. So the swap is in place
/// with an explicit `close this room` control, which is Android's compact
/// behaviour minus the gesture. Recorded in DECISIONS.
struct RoomsView: View {

    let model: ShellModel

    /// `embedded` (`SatellitesScreen.kt:80`): drawn inside Settings, which
    /// already has the heading **and already scrolls**. The screen must not
    /// add a second `ScrollView` there — a scroll view inside a scroll view is
    /// the bug ``SettingsPlaceholder`` records at length.
    var embedded = false

    @State private var rooms: RoomsModel?

    /// Whether this view is on screen, as `onAppear`/`onDisappear` report it.
    /// ``HomeView``'s guard, for its reason: this is inside the workspace's
    /// `NavigationStack`, so a pushed destination fires `onDisappear` while
    /// the view stays in the hierarchy with its `onChange` live.
    @State private var visible = false

    var body: some View {
        content
            .task {
                // Built here and not in `init` for ``HomeView``'s reason: the
                // model needs the container, and a `@State` initialiser runs
                // before the view has one it can rely on.
                if rooms == nil {
                    rooms = RoomsModel(in: model.container)
                }
                rooms?.start()
            }
            .onAppear { visible = true }
            .onDisappear {
                visible = false
                rooms?.stop()
            }
            // **The toast is the shell's**, drawn over the whole app
            // (`AppShellView.swift:36`): a toast about an announcement that
            // was accepted and did not play must outlive a scroll, and does
            // outlive this screen. The model holds its own until something
            // takes it, so this is a hand-off and not a second copy.
            .onChange(of: rooms?.toast) { _, said in
                guard said != nil, let text = rooms?.takeToast() else { return }
                model.showToast(text)
            }
            // A fresh roster landed: the open room's slider follows the
            // server's level again unless a drag is still in flight
            // (`SatOverviewTab.kt:251`), and the timer-scope switch drops its
            // optimistic override once the server agrees with it
            // (`SatOverviewTab.kt:415-417`). Keyed on when the data landed
            // rather than on the rooms themselves, so a payload that says the
            // same thing does not re-write a draft.
            .onChange(of: rooms?.roster.dataAt) { _, _ in
                guard let rooms else { return }
                rooms.openReads?.followServerVolume(rooms.openRoom)
                rooms.openReads?.followServerTimerScope(rooms.openRoom)
            }
            .onChange(of: model.inputs.serverURL) { _, _ in
                guard visible else { return }
                rooms?.serverChanged()
            }
    }

    @ViewBuilder
    private var content: some View {
        if embedded {
            panel
                .frame(maxWidth: .infinity, alignment: .leading)
        } else {
            ScrollView {
                panel
                    .padding(16)
                    .frame(maxWidth: .infinity, alignment: .leading)
            }
            .frame(maxWidth: .infinity, maxHeight: .infinity)
        }
    }

    @ViewBuilder
    private var panel: some View {
        // A plain `VStack`: this is a house's rooms, which is a handful of
        // cards and not a files-like page, so the owner's load-as-you-scroll
        // decision of 2026-10-11 does not reach it — and a lazy row is the
        // one place a write must never live.
        VStack(alignment: .leading, spacing: 16) {
            if let rooms {
                if !embedded {
                    Text(RoomsStrings.title)
                        .font(.title2)
                        .accessibilityAddTraits(.isHeader)
                        .fixedSize(horizontal: false, vertical: true)
                }
                // The counts, in both entry points: Android draws them under
                // the header and in place of it (`SatellitesScreen.kt:143`).
                Text(rooms.summary)
                    .font(.footnote)
                    .foregroundStyle(Color.fgMuted)
                    .fixedSize(horizontal: false, vertical: true)
                if let open = rooms.openRoom {
                    RoomsDetailView(model: rooms, room: open)
                } else {
                    roster(rooms)
                }
            }
        }
    }

    /// `SatellitesScreen.kt:152-159`'s four states, as the model's one value.
    @ViewBuilder
    private func roster(_ rooms: RoomsModel) -> some View {
        switch rooms.content {
        case .loading:
            ProgressView()
                .frame(maxWidth: .infinity, alignment: .center)
                .accessibilityLabel(RoomsStrings.title)
        case .failed(let why):
            // The read's own words. ``ApiRead`` keeps its last value, so this
            // branch is only ever a read that has **never** landed — which
            // includes the ordinary unpaired case, since `/api/satellites` is
            // device read.
            Text(why)
                .font(.subheadline)
                .foregroundStyle(Color.errInk)
                .fixedSize(horizontal: false, vertical: true)
        case .empty:
            VStack(alignment: .leading, spacing: 8) {
                Text(RoomsStrings.noRoomsYet)
                    .font(.subheadline)
                    .fixedSize(horizontal: false, vertical: true)
                Text(RoomsStrings.noRoomsHint)
                    .font(.footnote)
                    .foregroundStyle(Color.fgMuted)
                    .fixedSize(horizontal: false, vertical: true)
            }
        case .rooms(let list):
            VStack(alignment: .leading, spacing: 12) {
                ForEach(list) { room in
                    RoomsCardView(
                        room: room,
                        extraSec: rooms.extraSec,
                        nowMs: rooms.clockMs,
                        onOpen: { rooms.open(room.roomID) }
                    )
                }
                RoomsBroadcastCard(model: rooms)
            }
        }
    }
}

/// The broadcast composer (`BroadcastComposer`, `SatellitesScreen.kt:301`):
/// one message, every connected room.
///
/// The draft and the send are the **model's**, not this view's, and both for
/// the reason Android gives at `HomeScreen.kt:347`: a card that holds the
/// request loses it the moment the card goes away. ``RoomsModel/broadcast()``
/// reuses Home's write and both its rules, so the phone's two broadcast
/// buttons say the same words for the same outcome.
struct RoomsBroadcastCard: View {

    let model: RoomsModel

    @Environment(\.dynamicTypeSize) private var typeSize

    private var draft: Binding<String> {
        // Written out rather than `@Bindable`: the model is held as an
        // optional `@State` one level up, and a binding through an optional is
        // a binding to a value that can vanish.
        Binding(get: { model.broadcastDraft }, set: { model.broadcastDraft = $0 })
    }

    var body: some View {
        HomeCard(title: RoomsStrings.broadcastHeading) {
            HStack {
                HomeTonePill(
                    label: model.onlineCount == 0
                        ? HomeAnnounceStrings.noRoomsOnline
                        : RoomsStrings.onlinePill(model.onlineCount),
                    tone: model.onlineCount == 0 ? .idle : .brand
                )
                Spacer(minLength: 0)
            }
            Text(RoomsStrings.broadcastHelp)
                .font(.footnote)
                .foregroundStyle(Color.fgMuted)
                .fixedSize(horizontal: false, vertical: true)
            if typeSize.isAccessibilitySize {
                // The button goes under the field rather than beside it: a
                // field wide enough to type into and a 44-point button do not
                // share a line at AX5.
                VStack(alignment: .leading, spacing: 8) {
                    field
                    send
                }
            } else {
                HStack(alignment: .center, spacing: 8) {
                    field
                    send
                }
            }
        }
    }

    /// `axis: .vertical` with `lineLimit(1...)`, which is what stops a
    /// one-line field clipping its own text at an accessibility size — and
    /// therefore **no `submitLabel` and no `onSubmit`**: a vertical field's
    /// Return key makes a newline and `onSubmit` never fires, so a key
    /// labelled `send` would lie. Measured on 2026-11-02 against the Chat
    /// composer, which shipped that pair.
    private var field: some View {
        TextField(RoomsStrings.broadcastPlaceholder, text: draft, axis: .vertical)
            .lineLimit(1...)
            .font(.subheadline)
            .padding(.horizontal, 8)
            .padding(.vertical, 10)
            .frame(minHeight: 44)
            .background(Color(.secondarySystemBackground), in: RoundedRectangle(cornerRadius: 8))
            .overlay {
                RoundedRectangle(cornerRadius: 8).strokeBorder(.quaternary)
            }
            // Nothing is online, so nothing can hear it. The field says so by
            // being unavailable rather than by taking a message and dropping
            // it, which is Home's rule for the same control.
            .disabled(model.onlineCount == 0)
            // The placeholder vanishes the moment anything is typed, so it
            // cannot be the label — and neither sentence on the card can be,
            // because each is drawn as text of its own and two elements
            // answering to one name is what sends a test's tap to the
            // heading. ``RoomsStrings/broadcastFieldLabel`` is iOS only.
            .accessibilityLabel(RoomsStrings.broadcastFieldLabel)
    }

    private var send: some View {
        Button(RoomsStrings.broadcastButton) {
            model.broadcast()
        }
        .buttonStyle(.borderedProminent)
        // The app's tint is `brandInk`, so a prominent button would fill with
        // the ink and draw near-black `brandFg` on it at 3.08:1. The fill is
        // the other token, and 8.49:1 with this label colour.
        .tint(Color.brand)
        .foregroundStyle(Color.brandFg)
        .font(.subheadline)
        .fixedSize(horizontal: false, vertical: true)
        .frame(minHeight: 44)
        .contentShape(.rect)
        .disabled(model.onlineCount == 0 || model.broadcasting)
    }
}
