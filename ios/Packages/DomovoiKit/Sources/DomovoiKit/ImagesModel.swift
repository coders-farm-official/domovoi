import Foundation
import Observation

/// The Images screen's data layer: three reads, the compose state, three
/// writes, and the thumbnails.
///
/// `ui/screens/images/ImagesScreen.kt` keeps all of this in the composable
/// itself — `rememberApi` x3 and six `remember`s (`:138-154`). Here it is a
/// model for the reason ``FilesModel`` and ``ChatModel`` are models, and for
/// one rule in particular: **a lazy row never holds a `Task`**. Android
/// already obeys the Android shape of that rule for the cancel button, which
/// runs on `app.scope` rather than the row's (`:349`); on iOS a row in a
/// `LazyVStack` is torn down as it scrolls out, which would cancel the write
/// mid-flight. So every write lives here, where it outlives the row.
///
/// ### What this screen is, and what it is not
///
/// Generate and history, nothing else. Browsing the pictures afterwards is
/// the Files tab's job, and the engine — install, start, stop, model
/// downloads — belongs to the web dashboard's Images page
/// (`ImagesScreen.kt:70-74`). **No control for any of that is drawn**, here
/// or in the view: Android's client has no route for them, inventing one
/// would be inventing server behaviour (NIGHT-RULES §6), and a button that
/// cannot do what it says is not drawn (the owner's rule). The three engine
/// states keep Android's own sentences, which point at the dashboard.
@MainActor
@Observable
public final class ImagesModel {

    // MARK: - the three reads

    /// `GET …/models` — `ImagesScreen.kt:138-140`.
    public let state: ApiRead<ImagesGenState>

    /// `GET …/jobs`, unwrapped from its `jobs` key — `:141-143`.
    public let jobs: ApiRead<[ImagesJob]>

    /// `GET …/history`, unwrapped from its **`history`** key — `:144-146`.
    public let history: ApiRead<[ImagesJob]>

    /// The thumbnails. ``VideoPosterStore`` **reused as it is**, not copied:
    /// it is a path→`Data` store that knows nothing about videos but its
    /// name, it fetches through
    /// ``DomovoiClient/raw(method:path:body:contentType:trackPairing:timeout:)``
    /// with `trackPairing: false`, it caps itself at a hundred entries
    /// without evicting anything in flight, and it checks an epoch after the
    /// `await` so a tile that lands after a house move is discarded
    /// (`VideoPosterStore.swift:80-86`, `:132-167`). Every one of those is a
    /// property this screen needs and none of them is a property of videos.
    ///
    /// `AsyncImage` is not an option: `…/file/{name}` is a device-tier route
    /// and `AsyncImage` makes its own unauthenticated request, which 401s.
    /// The cost of the reuse is that the type is called `VideoPosterStore` on
    /// the Images screen; renaming a shared file is a "Needs a shared change"
    /// note, not a thing this branch does (DECISIONS).
    public let tiles: VideoPosterStore

    // MARK: - composing a generation
    //
    // **No view writes any of this.** The composer that did — the prompt
    // field, the model picker and `generate` — is not drawn (see the note
    // over "the three writes"). The state and its rules are kept with their
    // tests, because they are what the screen gets back the day the plugin's
    // three routes take the household tier.

    /// The prompt. **Not cleared after a successful generate**, and the model
    /// choice is not reset either — `ImagesScreen.kt:156-170` clears neither,
    /// and a second go at a prompt one word different is the ordinary next
    /// thing somebody does.
    public var prompt = ""

    /// Which model the person picked, if they have. Nil means "whatever
    /// ``ImagesGenState/activeModel(chosen:)`` falls back to".
    public var chosenModelID: String?

    /// A generate is in flight — `busy`, `:151`. Only this disabled the
    /// button; a cancel or a delete did not.
    public private(set) var busy = false

    /// The history row whose delete has been asked for and not yet confirmed
    /// — `confirmDelete`, `:152`. A destructive write with no ask is the thing
    /// the dialog existed to prevent. **Nothing sets it** — the delete is not
    /// drawn.
    public var confirmingDelete: ImagesJob?

    /// The one sentence the shell shows and clears, as every other screen's
    /// toast is.
    public private(set) var toast: String?

    // MARK: - what the screen reads off this

    /// `installedModels` — `:148`.
    public var installedModels: [ImagesGenModel] {
        state.data?.installedModels ?? []
    }

    /// `active` — `:153`.
    public var activeModel: ImagesGenModel? {
        state.data?.activeModel(chosen: chosenModelID)
    }

    /// `ready` — `:154`.
    public var ready: Bool { state.data?.ready == true }

    /// The picker's label — `:307-308`.
    public var pickerLabel: String {
        state.data?.pickerLabel(chosen: chosenModelID) ?? ImagesStrings.noModelsInstalled
    }

    /// Whether a generate button would be enabled — `:231`, all four
    /// conjuncts. There is no such button on this screen.
    public var canGenerate: Bool {
        Self.canGenerate(state: state.data, active: activeModel, prompt: prompt, busy: busy)
    }

    /// `:231`'s four conjuncts as a rule of their own, so the four can be
    /// tested one at a time rather than through a view.
    ///
    /// `prompt.isNotBlank()` is Kotlin's, and it is **blank**, not empty: a
    /// prompt of three spaces enables nothing. `ready` carries two of the
    /// four (`:154`).
    public static func canGenerate(
        state: ImagesGenState?,
        active: ImagesGenModel?,
        prompt: String,
        busy: Bool
    ) -> Bool {
        guard state?.ready == true, active != nil, !busy else { return false }
        return !prompt.trimmingCharacters(in: .whitespacesAndNewlines).isEmpty
    }

    /// The live lane — `laneJobs`, `:180-183`. Pending, running **and
    /// failed**: a failure stays visible here rather than moving to history,
    /// which is how a dead job gets noticed at all.
    public var laneJobs: [ImagesJob] {
        (jobs.data ?? []).filter(\.status.inLane)
    }

    /// The history rows, in the server's order.
    public var historyJobs: [ImagesJob] { history.data ?? [] }

    /// Whether the `history` label and its cards are drawn at all —
    /// `ImagesScreen.kt:247`, where the label lives **inside** the
    /// `if (history.isNotEmpty())` and not above it.
    ///
    /// Here rather than as an `if` in the view, and that is the whole point
    /// of it: the view's guard is the one rule on this screen a package test
    /// could not reach, and train X004's negative control widened it to
    /// `count >= 0` — a `history` heading over nothing on a house that has
    /// generated nothing. `ImagesView.swift` reads this, so the rule has a
    /// test (``ImagesTests/testTheHistorySectionIsDrawnOnlyWhenThereIsHistory``)
    /// and not only a UI case on a Simulator that may not run.
    public var showsHistory: Bool { !historyJobs.isEmpty }

    /// Which of the five things the engine line says — `:190-212`.
    public var engineLine: ImagesEngineLine {
        ImagesEngineLine.of(state.data, loading: state.loading, error: state.error)
    }

    /// The band above the screen, or nil for a house with no imagegen plugin
    /// — in which case Android renders the content bare
    /// (`PluginFrame.kt:98-101`).
    public func band(_ capabilities: Capabilities) -> PluginBand? {
        PluginBand.of(capabilities.pluginFor(ImagesModel.capability))
    }

    /// `CAP_IMAGEGEN` — the capability this whole screen is gated on
    /// (`ScreenRouter.kt:67`, and ``Route/requiredCapability`` for
    /// ``Route/images``).
    public static let capability = "imagegen"

    // MARK: - the house, and the one rule that keeps answers in it

    let container: AppContainer
    private var eventSink: Int?

    /// **The mechanism for `[[read-after-await-wrong-key]]`, said once.**
    ///
    /// This track's top defect class is a value read after an `await` and
    /// written back under a key that has since changed. Here the key that
    /// changes is the house, and the collision is concrete: a job id is the
    /// *plugin's* id and two houses can both have a job 7, so an answer to
    /// house A's generate, cancel or delete must not toast under house B's
    /// name or refresh house B's lane as if something there had changed.
    ///
    /// So **every `Task` this type starts takes this number before its first
    /// `await` and checks it after**, exactly as ``ChatModel`` does
    /// (`ChatModel.swift:147-151`). A counter rather than the address itself,
    /// because leaving a house and coming back is also a change, and
    /// `normalize(serverURL)` cannot tell the two visits apart.
    ///
    /// The three reads need no such guard: ``ApiRead`` is keyed to the
    /// container and re-keys itself (`ApiRead.swift:200-210`). What needs it
    /// is the toast, the ``busy`` flag and the decision to refresh.
    private var houseEpoch = 0

    private func stillHome(_ epoch: Int) -> Bool { epoch == houseEpoch }

    public init(in container: AppContainer) {
        self.container = container
        let client = container.client
        self.state = ApiRead(in: container, eventTypes: ImagesPaths.events) {
            try await client.get(ImagesPaths.models, as: ImagesGenState.self)
        }
        // Unwrapped **in the read**, each from its own key. `jobs` and
        // `history` are two wrappers with two different keys over one row
        // type, and reading one with the other's key decodes to an empty list
        // that looks on screen exactly like "nothing has happened yet"
        // (``ImagesJobList``).
        self.jobs = ApiRead(in: container, eventTypes: ImagesPaths.events) {
            try await client.get(ImagesPaths.jobs, as: ImagesJobList.self).jobs
        }
        self.history = ApiRead(in: container, eventTypes: ImagesPaths.events) {
            try await client.get(ImagesPaths.history, as: ImagesHistoryList.self).history
        }
        // ``VideoPosterStore``, but **not** its `init(in:)`. That
        // convenience fetches with a four-second timeout chosen for video
        // poster frames — "nothing at all is worth a screen of tiles hanging
        // on" (`VideoPosterStore.swift:103-108`). These tiles are not
        // thumbnails: the plugin has no thumb route, so Android's
        // `filePath(name)` is the same path for the tile and for the open
        // (`ImagesScreen.kt:125`, `:405`), and each tile downloads the whole
        // generated picture — a 1024x1024 PNG, one to two megabytes, three to
        // a card. Coil fetches those on OkHttp's default ten-second read
        // timeout, so four seconds would time a tile out here that loads on
        // the phone beside it, and ``VideoPoster/failed`` carries no reason to
        // explain it. Twenty seconds: twice Coil's, and under the thirty the
        // picture sheet gives the same bytes at full size
        // (``ImagesPictureModel``, `ImagesPicture.swift:74`).
        let tileClient = container.client
        self.tiles = VideoPosterStore(fetch: { path in
            // `trackPairing: false` for `init(in:)`'s reason: a 401 from a
            // tile must not raise the pairing screen.
            try await tileClient.raw(
                method: "GET",
                path: path,
                trackPairing: false,
                timeout: 20
            )
        })
    }

    // MARK: - the three writes

    // -----------------------------------------------------------------------
    // **Nothing in the app calls any of the three.**
    //
    // Said here, at the top of the section, because a seam with no caller
    // that reads as shipped behaviour is a defect class this track has
    // already paid for twice ([[reasoning-that-hides-a-bug]],
    // `AudiobookModels.safeLibraryPath`). ``generate()``, ``cancel(_:)`` and
    // ``confirmDelete()`` are complete, correct and tested — and the
    // controls that used to call them are **not drawn** (2026-11-03), because
    // all three routes are `dependencies=ADMIN_ONLY` on the plugin and this
    // app sends only `X-Device-Token`, so every one answered 401 from every
    // phone. ``ImagesPaths``'s header carries the evidence and ``ImagesView``
    // the decision; the owner's answer 41 is "admin-only things stay off both
    // phones".
    //
    // They are kept rather than deleted because the fix is one line in the
    // plugin (the household tier on three routes, logged under "Needs a
    // shared change"), and because their tests are the only specification
    // either platform has for those three requests. **Their presence is not
    // evidence that a phone can generate, cancel or delete.**
    // -----------------------------------------------------------------------

    /// `POST …/generate` — `ImagesScreen.kt:156-170`. **No control calls
    /// this** — see the note above.
    ///
    /// Returns whether the request went, which is what a test asserts on and
    /// what stops this being a method whose refusal is invisible.
    ///
    /// The guard is ``canGenerate``, which is one conjunct wider than the
    /// Kotlin's own `generate()` (`:157-158` checks the model, the prompt and
    /// `busy`, and leaves `ready` to the button's `enabled`). A model method
    /// that can be called from anywhere should not depend on a view having
    /// disabled a button: a generate sent to an engine that is not running is
    /// a request nobody can answer.
    @discardableResult
    public func generate() -> Bool {
        guard let model = activeModel, canGenerate else { return false }
        let body = ImagesPaths.generateBody(model: model.id, prompt: prompt)
        busy = true
        let epoch = houseEpoch
        let client = container.client
        Task { @MainActor [weak self] in
            do throws(DomovoiError) {
                try await client.send(method: "POST", path: ImagesPaths.generate, body: body)
                guard let self, self.stillHome(epoch) else { return }
                self.busy = false
                // The new job comes from the read, not from the reply, which
                // Android ignores (`:166`). The plugin's own push would bring
                // it too; this is the belt for a house whose socket is down.
                self.jobs.refresh()
            } catch let trouble {
                guard let self, self.stillHome(epoch) else { return }
                self.busy = false
                self.toast = ImagesStrings.generateFailed(trouble.message)
            }
        }
        return true
    }

    /// `POST …/jobs/{id}/cancel` — `ImagesScreen.kt:348-355`. **No control
    /// calls this** — see the section's note.
    ///
    /// Refused for a job that is not pending or running, because Android
    /// draws the button only for those (`:347`) and a method that would send
    /// it anyway makes the check a courtesy. The refusal is the rule, not the
    /// view's: it is the half that survives a screen with no cancel on it.
    ///
    /// Two things Android does not do, both recorded in DECISIONS:
    /// the lane is **re-read** on success (Android waits for the plugin's
    /// push, which never comes on a house whose socket is down, leaving a
    /// cancelled job in the lane for good), and a failure **says so**
    /// (Android's `runCatching` has no `onFailure`, so a cancel that failed
    /// reads as a button that does nothing).
    @discardableResult
    public func cancel(_ job: ImagesJob) -> Bool {
        guard job.status.isActive else { return false }
        let epoch = houseEpoch
        let client = container.client
        Task { @MainActor [weak self] in
            do throws(DomovoiError) {
                // `{}` and not an absent body. Android's `api.post`
                // substitutes `JsonObject(emptyMap())` when no body is given
                // and always sends `application/json`
                // (`ApiClient.kt:339-353`), so that is the only shape the
                // plugin's cancel route has ever been called with; a bodyless
                // POST would be a 422 against a Pydantic body and the button
                // would fail every time. This repo's one other bodyless POST
                // passes it explicitly for the same reason
                // (`HomeWrites.swift:35`), and `DomovoiClient.post` defaults
                // to it.
                try await client.send(
                    method: "POST",
                    path: ImagesPaths.cancel(job: job.id),
                    body: DomovoiClient.emptyObject
                )
                guard let self, self.stillHome(epoch) else { return }
                self.jobs.refresh()
            } catch let trouble {
                guard let self, self.stillHome(epoch) else { return }
                self.toast = ImagesStrings.cancelFailed(trouble.message)
            }
        }
        return true
    }

    /// `DELETE …/history/{id}` — `ImagesScreen.kt:172-178`. **No control
    /// calls this** — see the section's note.
    ///
    /// Called by the confirmation, never by a row's button: the dialog is the
    /// ask, and ``confirmingDelete`` is what a button would set. Neither the
    /// button nor the dialog is drawn any more.
    public func deleteHistory(_ job: ImagesJob) {
        let epoch = houseEpoch
        let client = container.client
        Task { @MainActor [weak self] in
            do throws(DomovoiError) {
                try await client.send(method: "DELETE", path: ImagesPaths.deleteHistory(job: job.id))
                guard let self, self.stillHome(epoch) else { return }
                self.toast = ImagesStrings.deleted
                self.history.refresh()
            } catch let trouble {
                guard let self, self.stillHome(epoch) else { return }
                self.toast = ImagesStrings.deleteFailed(trouble.message)
            }
        }
    }

    /// The confirmation's body for the row waiting on it — `:275`, `:278`.
    /// Nil when nothing is waiting, so a view would have one source for both
    /// the sentence and whether to show the dialog at all. **No view reads
    /// it**: the confirmation went with the delete.
    public var deleteBody: String? {
        confirmingDelete.map { ImagesStrings.deleteBody(images: $0.imageNames.count) }
    }

    /// Confirm the pending delete, and close the dialog.
    public func confirmDelete() {
        guard let job = confirmingDelete else { return }
        confirmingDelete = nil
        deleteHistory(job)
    }

    // MARK: - lifecycle

    /// The screen appeared: join the pushes and read all three.
    public func start() {
        if eventSink == nil {
            eventSink = container.onStateEvent { [weak self] event in
                Task { @MainActor in self?.received(event) }
            }
        }
        state.refresh()
        jobs.refresh()
        history.refresh()
    }

    /// The screen is gone. "A route holds nothing" (`Pagination.swift`): the
    /// reads stop and coming back re-reads. A read with a task still in
    /// flight would otherwise keep a request alive after its reader is gone,
    /// which is what `rememberApi`'s state dying with the composable does for
    /// free.
    public func stop() {
        if let eventSink {
            container.removeStateEventSink(eventSink)
            self.eventSink = nil
        }
        state.stop()
        jobs.stop()
        history.stop()
    }

    /// A push arrived. Each read filters by type itself, exactly as the web
    /// hooks do; nothing ever sends a narrower subscription. All three listen
    /// for the same one type (`ImagesPaths.events`).
    public func received(_ event: StateEvent) {
        state.received(event)
        jobs.received(event)
        history.received(event)
    }

    /// The app moved house. Everything held is the old house's — the job ids,
    /// which two houses can both have, most of all.
    public func serverChanged() {
        // **First**, so that every task already in flight for the old house
        // fails ``stillHome(_:)`` from here on, whatever order its
        // continuation runs in.
        houseEpoch += 1
        busy = false
        confirmingDelete = nil
        toast = nil
        // The prompt is **kept**: it is the person's own typing, it belongs to
        // nothing on the server, and losing a paragraph because the phone
        // moved from Wi-Fi to the other house's address would be this app
        // throwing away work. The model choice is dropped, because a model id
        // is the other engine's.
        chosenModelID = nil
        state.serverChanged()
        jobs.serverChanged()
        history.serverChanged()
        tiles.serverChanged()
    }

    /// Take the toast, as every other screen's shell does.
    public func takeToast() -> String? {
        defer { toast = nil }
        return toast
    }
}
