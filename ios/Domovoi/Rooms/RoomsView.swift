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
/// * **Display controls and logs** — `:1597-1602` and `:1679-1684`, both
///   admin. Android's phone screen draws neither either.
/// * **"Use this device as a room"** (`SatellitesScreen.kt:169`) — the
///   `roommode` item's card. This screen leaves the gap rather than drawing a
///   dead one.
///
/// **"Drop in from this phone" IS here now** (`PhoneDropinRow`,
/// `PhoneDropinCall.kt:61`): Kamron, answer 54. It is a device-tier socket of
/// its own, ``DropInCallModel`` drives it, and ``RoomsDropInRow`` draws the
/// control inside the opened room's drop-in section.
///
/// ### A room is its own pushed screen (D56)
///
/// Kamron, answer 56: "take the recommendation: a room opens as its own
/// pushed screen with swipe back." It used to be a swap in place — the roster
/// replaced by the detail, with an explicit `close this room` control — which
/// was Android's compact behaviour (`SatellitesScreen.kt:119-131`) minus the
/// gesture, because a `NavigationStack(path:)` over `[Route]` takes no other
/// value and this item could not add one.
///
/// The push is a **destination-based** `NavigationLink`: the card is the
/// link's label and the link carries ``RoomsRoomScreen`` itself. The system's
/// back button and swipe-back pop it; there is no `Route` case, no second
/// `path` on ``ShellModel``, and ``WorkspaceView`` and ``RouteScreen`` are
/// untouched. Both mountings are inside the same `NavigationStack`
/// (`RouteScreen.swift:147-154` and `:390-392`), so both get the push and
/// neither of those lines changes.
///
/// ### Why not `navigationDestination(for:)` and `NavigationLink(value:)`
///
/// Because it does not work here, and train **I003** measured it: every one
/// of this unit's twenty-four cases that opens a room failed on its first
/// step, "the kitchen card did not open", and the tree each one printed shows
/// the tap landing on the right card and the Settings page still on screen
/// with a navigation bar that has a back button and **no title**
/// (`overnight-trains/I003/ALL/testRooms_OpeningARoomShowsItsOverview.log`).
///
/// The declaration was fine; the *path* is not. This view is inside
/// `NavigationStack(path: $model.path)` and ``ShellModel/path`` is
/// `[Route]` (`ShellModel.swift:515`) — a **typed** path can only ever hold
/// its own element type, so a `navigationDestination(for: RoomsOpenRoom.self)`
/// resolves and then has nothing to resolve *from*: no `RoomsOpenRoom` can be
/// appended to an array of `Route`, and a value-based link whose value cannot
/// go on the path is inert. It draws, it highlights, it takes the tap, and
/// nothing happens. Two other items had already hit this wall and written it
/// down — ``AudiobookDetailView`` for an `Int64`
/// (`AudiobooksView.swift:81-85`), ``ManualView`` for a `ManualPlace`
/// (`ManualView.swift:198-212`) — and this item's two reviews checked that
/// the declaration was single and not lazy without checking the path's
/// element type.
///
/// A destination-based link needs no path element at all: it pushes onto the
/// enclosing stack directly, which is also what it did under `NavigationView`
/// and the one `NavigationLink` initialiser iOS 16 did **not** deprecate
/// (only the `isActive:` and `tag:selection:` forms went). So `path` keeps
/// reporting the route the person is on and the room is one push above it.
///
/// The proper fix is one line in a file this item may not touch — make
/// ``ShellModel/path`` a `NavigationPath`, which is the heterogeneous kind —
/// and it would give Manual, Audiobooks, People and this screen a real
/// typed push each. It is in `DECISIONS.md` under "Needs a shared change",
/// as it has been since the overnight build.
///
/// ``RoomsOpenRoom`` stays: it is what names the room a pushed screen is for
/// and it is the value ``RoomsRoomScreen`` takes. It is no longer a path
/// element.
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
    ///
    /// **"Live" means it fires, not that it fires promptly**, and the two
    /// halves of that are both paid for. It fires: that is why the one-sided
    /// version of this guard cost ``HomeView`` its socket for whole sessions.
    /// It fires *late*: a covered view's body is not re-evaluated while it is
    /// covered, so the closure runs at the pop — with `visible` still false,
    /// because body evaluation precedes `onAppear` — which is why the toast
    /// hand-off below could not stay this view's alone once D56 made a room a
    /// push (train I004's six reds; see ``RoomsRoomScreen``).
    @State private var visible = false

    /// Whether this view is one of ``RoomsModel``'s holders. See the
    /// `onAppear` below: it keeps `hold()`/`release()` balanced however many
    /// times the two callbacks arrive for one appearance.
    @State private var held = false

    var body: some View {
        content
            // **Both halves in `onAppear`/`onDisappear`, which is a change
            // D56 forced.** The model used to be built and started in
            // `.task` and stopped in `onDisappear`, and that pair is not
            // symmetric: `.task` runs once per view identity and a pushed
            // room does not end this view's identity, so coming back from a
            // room would never have restarted a model the push had stopped.
            // ``RoomsModel/hold()`` and ``RoomsModel/release()`` are a
            // count, so the two callbacks may arrive in either order — see
            // ``RoomsModel/holders``.
            //
            // Built here and not in `init` for ``HomeView``'s reason: the
            // model needs the container, and a `@State` initialiser runs
            // before the view has one it can rely on. `onAppear` runs with
            // the view fully configured, so `model.container` is there.
            //
            // ``held`` is the same guard ``RoomsRoomScreen`` carries, for the
            // same reason: a repeated `onAppear` must not hold twice, or the
            // count never reaches zero and the screen never stops.
            .onAppear {
                visible = true
                if rooms == nil {
                    rooms = RoomsModel(in: model.container)
                }
                guard !held else { return }
                held = true
                rooms?.hold()
            }
            .onDisappear {
                visible = false
                guard held else { return }
                held = false
                rooms?.release()
            }
            // **The toast is the shell's**, drawn over the whole app
            // (`AppShellView.swift:36`): a toast about an announcement that
            // was accepted and did not play must outlive a scroll, and does
            // outlive this screen. The model holds its own until something
            // takes it, so this is a hand-off and not a second copy.
            //
            // **This is the roster's half only.** ``RoomsRoomScreen`` carries
            // the same four lines, and has to: a pushed room covers this view
            // and a covered view's body is not re-evaluated while it is
            // covered, so a toast raised from an opened room reached the
            // shell only after the pop — train I004's six "said nothing"
            // reds, measured against I002 where the room was not a push.
            // (That is the late half of "the `onChange` stays live" on
            // ``visible`` above, not a contradiction of it: the closure does
            // fire, which is what that guard is for; it fires at the pop.)
            // ``RoomsModel/takeToast()`` clears as it reads, so the two
            // hand-offs cannot both raise the same sentence. What is left
            // here is the broadcast card's, which is raised with no room
            // open at all.
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
        // No `navigationDestination` here: the push is the link's own
        // destination, for the reason this type's doc comment measures. On
        // the destination side the model is told which room is open and the
        // room screen holds it up for as long as it is on screen; the pop is
        // the system's.
        mounting
    }

    @ViewBuilder
    private var mounting: some View {
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
                // The roster, always: a room is pushed on top of this screen
                // now (D56), so this one stays mounted underneath rather
                // than being swapped for the detail.
                roster(rooms)
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
                    // A link and not a `Button`: this is what gives the
                    // pushed screen the system back button and the
                    // swipe-back gesture for nothing (D56). The card is
                    // still the whole target, as Android's is
                    // (`SatellitesScreen.kt:195`), and a `NavigationLink`
                    // reports as a button to VoiceOver and to a test's
                    // `app.buttons` query.
                    //
                    // **The destination, not a value** — see this type's doc
                    // comment: the shell's path is `[Route]`, so a
                    // `NavigationLink(value: RoomsOpenRoom(…))` had nowhere
                    // to go and train I003 measured twenty-four cases dying
                    // on a tap that did nothing.
                    //
                    // The destination is built with the row's id and the
                    // model, both of which outlive the row — `rooms` is the
                    // screen's one ``RoomsModel`` and `roomID` is a `String`
                    // — so an eagerly built `RoomsRoomScreen` holds nothing
                    // a redraw can pull out from under it. It also does no
                    // work until it appears: every read and the hold are
                    // taken in its `onAppear` (`RoomsDetailView.swift:267`),
                    // which is what makes one of these per row per clock
                    // tick free.
                    NavigationLink {
                        RoomsRoomScreen(
                            shell: model,
                            model: rooms,
                            open: RoomsOpenRoom(room.roomID)
                        )
                    } label: {
                        RoomsCardView(
                            room: room,
                            extraSec: rooms.extraSec,
                            nowMs: rooms.clockMs
                        )
                    }
                    .buttonStyle(.plain)
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
        TextField(
            RoomsStrings.broadcastPlaceholder,
            text: draft,
            // Owner answer 65's vertical leg (`ShellHintPrompt`).
            prompt: ShellHintPrompt.text(RoomsStrings.broadcastPlaceholder),
            axis: .vertical
        )
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
        .buttonStyle(BrandFillButtonStyle())
        .font(.subheadline)
        .fixedSize(horizontal: false, vertical: true)
        .frame(minHeight: 44)
        .contentShape(.rect)
        .disabled(model.onlineCount == 0 || model.broadcasting)
    }
}
