import DomovoiKit
import SwiftUI
import UIKit

/// The household app: the container, the shell model and the alerts —
/// what ``DomovoiApp`` used to build in its `init`, now built on first need
/// so that **a phone that launches as a room never builds an `AppContainer`**
/// (ROOMMODE-MAP §6.4; Android's `MainActivity.kt:121`, `toRoomIfLocked`,
/// before any container exists).
///
/// The order inside is unchanged, and its reasons moved here with it.
@MainActor
final class HouseholdRoot {
    let model: ShellModel
    let container: AppContainer
    let alerts: AlertsAppWiring

    init() {
        // **One vault, handed to both.** Two `TokenVault()`s would reach the
        // same Keychain items — same service, same accounts — so this is not
        // a correctness fix; it is so that "where do the tokens live" has one
        // answer at the one place that builds the graph, and so that a test
        // or a future seam can replace it in one line rather than two.
        let vault = TokenVault()
        let container = AppContainer(store: DomovoiApp.store(vault: vault), vault: vault)
        // The device id, and the two things only UIKit can answer. Read HERE,
        // on the main actor, once: ``DeviceSelf`` exists so that a `Sendable`
        // closure running anywhere never reads `UIDevice`, which
        // ``DomovoiKit`` cannot even import.
        //
        // The id is kept in `UserDefaults` and deliberately not the Keychain
        // (``DeviceIdentity``), and `ownerPersonID` is nil because no
        // preference holds one yet — see ``DeviceSelf``, which says what the
        // route does with a null and which batch has to fill it in.
        let deviceModel = UIDevice.current.model
        let who = DeviceSelf(
            deviceID: DeviceIdentity.stable(in: DeviceIdentity.UserDefaultsStore()),
            name: DeviceRegistry.suggestedDeviceName(model: deviceModel),
            userAgent: DeviceRegistry.userAgent(
                systemVersion: UIDevice.current.systemVersion,
                model: deviceModel
            ),
            ownerPersonID: nil
        )
        let model = ShellModel(container: container, deviceSelf: { who })
        self.model = model
        self.container = container
        // **Subscribed before the socket is opened**, in this order and not
        // in a view's `.task`, which runs later: a connect that landed in
        // between would be a connect whose capability refresh and device
        // register never happened, because ``SocketRelay`` does not replay.
        // These two lines, in this order, are the whole of what prevents it —
        // which is also why ``ShellModel/watchSocket()`` takes the socket's
        // CURRENT value rather than waiting for the next change.
        model.watchSocket()
        // Registering the path monitors, and opening the state socket, are
        // side effects on the process, which is why they are a separate call
        // the app makes and not something a container does to whoever builds
        // one.
        container.start()
        // After `container.start()`, because the alerts' first act is a read
        // through the container's client, and `DomovoiApplication.onCreate`
        // starts them beside the socket in the same order
        // (`TimerAlerts.kt:97`).
        alerts = AlertsAppWiring(container: container)
    }

    /// The household side goes quiet as the phone becomes a room
    /// (`RoomSetupActivity.begin`, `:141-149`): the phone's own player stops
    /// (a room it was casting to plays on — Android's `detachForRoomMode`),
    /// the live socket closes and the household's timers stop reaching this
    /// phone. ``HouseholdLock`` was set before this, so nothing here can
    /// carry the token on its way out.
    ///
    /// Kept, not dropped: `AppContainer` has no teardown for its path
    /// monitors, so a dropped container would leave them running and a
    /// second one would add more. Keeping it quiet is the same thing Android
    /// does (its container is the process's, and `leave` restarts it).
    func quietForRoomMode() {
        alerts.centre.pause()
        container.socket.stop()
        if model.playerIsBuilt, let player = model.player, player.target.roomID == nil {
            player.stop()
        }
    }

    /// Room mode ended (`RoomModeActivity.leave`, `:243-254`).
    /// The socket only: the path monitors were registered once, by `init`,
    /// and `container.start()` would register a second set.
    func resumeAfterRoomMode() {
        container.socket.start()
        alerts.centre.resume()
    }
}

/// Which side of the app is showing, and the one place that changes it.
///
/// `roomActive` is read before ANY household object exists (``DomovoiApp``'s
/// first lines) and ``HouseholdLock`` is set from it there; from then on
/// ``RoomStore/setActive(_:)`` keeps the lock and this flag in step, the
/// lock first.
@MainActor
final class RoomModeScene: ObservableObject {

    @Published private(set) var roomActive: Bool
    /// Setup is showing (over the household app), with what a link handed in.
    @Published var setup: RoomSetupRequest?

    let store: RoomStore
    let launch: RoomModeLaunch

    /// Not published: built inside a view body on first need, and a
    /// published write there is "Modifying state during view update".
    private var household: HouseholdRoot?

    init(launch: RoomModeLaunch, roomActive: Bool) {
        self.launch = launch
        self.roomActive = roomActive
        store = launch.store
    }

    /// The household app, built the first time the household side shows.
    func householdRoot() -> HouseholdRoot {
        if let household { return household }
        let made = HouseholdRoot()
        household = made
        return made
    }

    /// "Use this device as a room", or a `domovoi-room:` link (D14: a link
    /// only fills the room-code form).
    func openSetup(payload: String? = nil) {
        guard !roomActive else { return }
        setup = RoomSetupRequest(payload: payload)
    }

    /// A link while setup is already up is the setup screen's own to take
    /// (it fills the form there, keeping what the person has read); this
    /// opens setup only when it is not showing.
    func handle(url: URL) {
        guard url.scheme?.lowercased() == RoomEnrolmentParser.scheme, setup == nil else { return }
        openSetup(payload: url.absoluteString)
    }

    /// **Become this room** (`RoomSetupActivity.begin`): enrol, then room
    /// mode ON — which seals the household token first — then the screen
    /// swaps and the household side goes quiet.
    func begin(_ e: RoomEnrolment) -> Bool {
        guard store.enrol(e) else { return false }
        store.setActive(true)
        store.setRunning(true)
        household?.quietForRoomMode()
        setup = nil
        roomActive = true
        return true
    }

    /// Keep this room / Forget this room: the kiosk has already turned room
    /// mode off (``RoomStore/setActive(_:)``, the lock with it); the
    /// household app comes back, built now if it never was, without a
    /// relaunch.
    func left() {
        household?.resumeAfterRoomMode()
        roomActive = false
    }
}

struct RoomSetupRequest: Identifiable {
    let id = UUID()
    let payload: String?
}

private struct RoomModeSceneKey: EnvironmentKey {
    static let defaultValue: RoomModeScene? = nil
}

extension EnvironmentValues {
    /// The scene switch, for the entry cards. Nil in a preview, where the
    /// card then draws no button (it would have nothing to open).
    var roomModeScene: RoomModeScene? {
        get { self[RoomModeSceneKey.self] }
        set { self[RoomModeSceneKey.self] = newValue }
    }
}

/// The app's root: the room screen while the phone is a room, the household
/// app otherwise, with setup over the household app when it is asked for.
struct RoomModeSceneRoot: View {
    @ObservedObject var scene: RoomModeScene

    var body: some View {
        Group {
            if scene.roomActive {
                RoomKioskScreen(scene: scene)
            } else {
                HouseholdRootView(root: scene.householdRoot())
                    .environment(\.roomModeScene, scene)
                    .fullScreenCover(item: $scene.setup) { request in
                        RoomSetupScreen(scene: scene, payload: request.payload)
                    }
            }
        }
        .onOpenURL { url in scene.handle(url: url) }
    }
}

/// What the process was launched as: the room store it uses, and in a
/// DEBUG build the UI tests' room arguments (ROOMMODE-MAP §7.1).
///
/// - `-domovoi-room <payload>`: room mode on, in memory, enrolled from the
///   payload by the real parser — with loopback allowed, which only this
///   flag allows (the stub house is 127.0.0.1);
/// - `-domovoi-room-key <key>`: that room's pairing key;
/// - `-domovoi-room-feed`: quiet noise instead of the microphone, no
///   microphone or notification prompt, nothing kept (setup in a test
///   enrols into memory too).
///
/// In memory for the reason `-domovoi-server` is (``DomovoiApp/store(vault:)``):
/// a room written to the Simulator's defaults would turn the next test's
/// launch into a room.
struct RoomModeLaunch {
    let store: RoomStore
    /// The tests' microphone and permissions; never in a release build.
    let feed: Bool
    /// Fixed lock facts: a real Face ID or passcode sheet would stop a test.
    let fixedLock: Bool

    static func read() -> RoomModeLaunch {
        let seal: @Sendable (Bool) -> Void = { HouseholdLock.shared.setRoomModeActive($0) }
        #if DEBUG
        let arguments = ProcessInfo.processInfo.arguments
        func value(after flag: String) -> String? {
            guard let index = arguments.firstIndex(of: flag),
                  arguments.index(after: index) < arguments.endIndex
            else { return nil }
            return arguments[arguments.index(after: index)]
        }
        let feed = arguments.contains("-domovoi-room-feed")
        if let payload = value(after: "-domovoi-room") {
            let key = InMemoryRoomKey()
            let store = RoomStore(prefs: InMemoryRoomPreferences(), key: key, onActiveChanged: seal)
            guard case .ok(let e) = RoomEnrolmentParser.parse(payload, allowingLoopback: true) else {
                assertionFailure("-domovoi-room \(payload) is not a room code the parser takes")
                return RoomModeLaunch(store: store, feed: feed, fixedLock: true)
            }
            store.enrol(e)
            // After the enrolment, which mints a key of its own.
            if let given = value(after: "-domovoi-room-key"), !given.isEmpty {
                key.write(given)
            }
            store.setActive(true)
            store.setRunning(true)
            return RoomModeLaunch(store: store, feed: feed, fixedLock: true)
        }
        if feed {
            return RoomModeLaunch(store: RoomStore.inMemory(onActiveChanged: seal), feed: true, fixedLock: true)
        }
        #endif
        return RoomModeLaunch(store: RoomStore.system(onActiveChanged: seal), feed: false, fixedLock: false)
    }

    /// Whether the phone is a room: the store's flag, read before anything
    /// else is built.
    var roomActive: Bool { store.active }
}
