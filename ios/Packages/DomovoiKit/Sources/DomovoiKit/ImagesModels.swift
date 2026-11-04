import Foundation

/// The rows and the rules of the Images screen — the Image Generation
/// plugin's own surface, `/api/plugins/imagegen`.
///
/// Ported field for field from `ui/screens/images/ImagesScreen.kt:76-125`,
/// and **only** from there. The plugin is not in this repository: `plugins/`
/// holds `radio/` only, the plugin mounts its own router under
/// `/api/plugins/{slug}` (`web/backend/plugin_host.py:531`), core's
/// `web/backend/api/images.py` is a different surface entirely (thumb/raw over
/// the Files libraries, as its own docstring says at `:17-22`), and
/// `domovoi/db/migrations/V008__drop_image_jobs.sql` is the migration that
/// *removed* the core's job table. So none of these shapes can be checked
/// against server code tonight, Android's client is the whole specification,
/// and nothing here is invented — see
/// `design-notes/ios/overnight/images/DECISIONS.md`,
/// "Seven routes that no server code in this repo describes".
///
/// **Every field decodes with `decodeIfPresent ?? default`**, hand written,
/// as ``Capabilities`` does at `Capabilities.swift:65-77` and for its stated
/// reason: that is what Kotlin's `coerceInputValues` plus a `data class`
/// default means, and a synthesised `Decodable` would throw on an explicit
/// `null` in a field the Kotlin declares non-null. A plugin that ships a new
/// field, or one that sends `null` where it used to send a number, must not
/// take the screen down.

// MARK: - the models the engine offers

/// One generation model — `ImagesScreen.kt:79`.
public struct ImagesGenModel: Equatable, Sendable, Decodable, Identifiable {
    public let id: String
    public let label: String
    public let installed: Bool
    public let recommended: Bool

    public init(id: String = "", label: String = "", installed: Bool = false, recommended: Bool = false) {
        self.id = id
        self.label = label
        self.installed = installed
        self.recommended = recommended
    }

    private enum CodingKeys: String, CodingKey {
        case id, label, installed, recommended
    }

    public init(from decoder: any Decoder) throws {
        let values = try decoder.container(keyedBy: CodingKeys.self)
        id = try values.decodeIfPresent(String.self, forKey: .id) ?? ""
        label = try values.decodeIfPresent(String.self, forKey: .label) ?? ""
        installed = try values.decodeIfPresent(Bool.self, forKey: .installed) ?? false
        recommended = try values.decodeIfPresent(Bool.self, forKey: .recommended) ?? false
    }

    /// What one row of the picker reads — `ImagesScreen.kt:318`, including its
    /// two spaces either side of the separator.
    public var menuLabel: String {
        recommended ? label + ImagesStrings.recommendedSuffix : label
    }
}

/// `GET /api/plugins/imagegen/models` — `ImagesScreen.kt:87`.
public struct ImagesGenState: Equatable, Sendable, Decodable {
    public let installed: Bool
    public let engineOnline: Bool
    public let models: [ImagesGenModel]

    public init(installed: Bool = false, engineOnline: Bool = false, models: [ImagesGenModel] = []) {
        self.installed = installed
        self.engineOnline = engineOnline
        self.models = models
    }

    private enum CodingKeys: String, CodingKey {
        case installed
        case engineOnline = "engine_online"
        case models
    }

    public init(from decoder: any Decoder) throws {
        let values = try decoder.container(keyedBy: CodingKeys.self)
        installed = try values.decodeIfPresent(Bool.self, forKey: .installed) ?? false
        engineOnline = try values.decodeIfPresent(Bool.self, forKey: .engineOnline) ?? false
        models = try values.decodeIfPresent([ImagesGenModel].self, forKey: .models) ?? []
    }

    /// `installedModels` — `ImagesScreen.kt:148`.
    ///
    /// **Only installed models are ever offered**, and nothing on this screen
    /// can install one: the engine and its downloads belong to the
    /// dashboard's Images page (`ImagesScreen.kt:70-74`). A picker that
    /// listed a model this house cannot run would be a control that cannot do
    /// what it says.
    public var installedModels: [ImagesGenModel] {
        models.filter(\.installed)
    }

    /// `ready` — `ImagesScreen.kt:154`.
    public var ready: Bool { installed && engineOnline }

    /// The model a generate would use: the chosen one if it is still
    /// installed, else the first installed one — `ImagesScreen.kt:153`.
    ///
    /// The fall-back is why the picker can read a model's label before
    /// anybody has touched it, and why a chosen model that the engine has
    /// since removed does not leave the screen pointing at nothing.
    public func activeModel(chosen: String?) -> ImagesGenModel? {
        let installed = installedModels
        return installed.first { $0.id == chosen } ?? installed.first
    }

    /// What the read-only picker field reads — `ImagesScreen.kt:307-308`.
    public func pickerLabel(chosen: String?) -> String {
        if let active = activeModel(chosen: chosen) { return active.label }
        return installedModels.isEmpty ? ImagesStrings.noModelsInstalled : ImagesStrings.pickAModel
    }
}

// MARK: - a job, and what it is doing

/// What a generation was asked for — `ImagesScreen.kt:112`.
///
/// Every field is optional on the wire and **the app never sends any of
/// them**: `POST …/generate` carries `model` and `prompt` only
/// (`ImagesScreen.kt:162-165`), and these come back from the server.
public struct ImagesJobParams: Equatable, Sendable, Decodable {
    public let width: Int?
    public let height: Int?
    public let steps: Int?

    /// Kotlin `Long` (`ImagesScreen.kt:116`), so `Int64` — a seed is a
    /// 64-bit number and a 32-bit `Int` would be wrong on a watch and
    /// lossy in a decoder that rounds.
    public let seed: Int64?

    public init(width: Int? = nil, height: Int? = nil, steps: Int? = nil, seed: Int64? = nil) {
        self.width = width
        self.height = height
        self.steps = steps
        self.seed = seed
    }

    private enum CodingKeys: String, CodingKey {
        case width, height, steps, seed
    }

    public init(from decoder: any Decoder) throws {
        let values = try decoder.container(keyedBy: CodingKeys.self)
        width = try values.decodeIfPresent(Int.self, forKey: .width)
        height = try values.decodeIfPresent(Int.self, forKey: .height)
        steps = try values.decodeIfPresent(Int.self, forKey: .steps)
        seed = try values.decodeIfPresent(Int64.self, forKey: .seed)
    }
}

/// What a finished generation produced — `ImagesScreen.kt:94`.
public struct ImagesJobResult: Equatable, Sendable, Decodable {
    public let images: [String]

    public init(images: [String] = []) {
        self.images = images
    }

    private enum CodingKeys: String, CodingKey {
        case images
    }

    public init(from decoder: any Decoder) throws {
        let values = try decoder.container(keyedBy: CodingKeys.self)
        images = try values.decodeIfPresent([String].self, forKey: .images) ?? []
    }
}

/// `status`, as four words this app knows and **any other word kept whole**.
///
/// Android compares `status` to four literals and nothing else — `"pending"`
/// and `"running"` (`:329`, `:181-183`), `"failed"` (`:182`, `:335`), and
/// everything else falls through to the idle tone (`:336`). The vocabulary is
/// the plugin's and it is open, so ``other(_:)`` carries the raw word: the
/// pill draws `job.status` verbatim (`:334`), and a status this app has never
/// heard of must still render **its own word**, not "other".
///
/// The two predicates live here rather than in three places, which is what
/// they were in the Kotlin.
public enum ImagesJobStatus: Equatable, Sendable {
    case pending
    case running
    case failed
    case other(String)

    public init(_ raw: String) {
        switch raw {
        case "pending": self = .pending
        case "running": self = .running
        case "failed": self = .failed
        default: self = .other(raw)
        }
    }

    /// The word the server sent, which is the word the pill draws.
    public var word: String {
        switch self {
        case .pending: "pending"
        case .running: "running"
        case .failed: "failed"
        case .other(let raw): raw
        }
    }

    /// `activeJob` — `ImagesScreen.kt:329`. The spinner, the progress line and
    /// the cancel button are all this.
    public var isActive: Bool { self == .pending || self == .running }

    /// `laneJobs`' filter — `ImagesScreen.kt:180-183`. **A failure stays in
    /// the lane**; it does not move to history.
    public var inLane: Bool { isActive || self == .failed }

    /// Which tone the pill takes (`:335-338`), as a fact rather than a
    /// comparison a view has to repeat.
    public var isFailure: Bool { self == .failed }
}

/// One generation — a live job or a history row, which are the same shape on
/// the wire. `ImagesScreen.kt:97`.
public struct ImagesJob: Equatable, Sendable, Decodable, Identifiable {

    /// Kotlin `Long` (`:98`).
    public let id: Int64
    public let kind: String
    public let model: String
    public let prompt: String?
    public let params: ImagesJobParams?

    /// The raw word. ``status`` is the parsed one; this is what is drawn.
    public let statusWord: String
    public let pct: Int?
    public let statusText: String?
    public let error: String?
    public let result: ImagesJobResult?
    public let completedAt: String?

    public init(
        id: Int64 = 0,
        kind: String = "",
        model: String = "",
        prompt: String? = nil,
        params: ImagesJobParams? = nil,
        statusWord: String = "",
        pct: Int? = nil,
        statusText: String? = nil,
        error: String? = nil,
        result: ImagesJobResult? = nil,
        completedAt: String? = nil
    ) {
        self.id = id
        self.kind = kind
        self.model = model
        self.prompt = prompt
        self.params = params
        self.statusWord = statusWord
        self.pct = pct
        self.statusText = statusText
        self.error = error
        self.result = result
        self.completedAt = completedAt
    }

    private enum CodingKeys: String, CodingKey {
        case id, kind, model, prompt, params, pct, error, result
        case statusWord = "status"
        case statusText = "status_text"
        case completedAt = "completed_at"
    }

    public init(from decoder: any Decoder) throws {
        let values = try decoder.container(keyedBy: CodingKeys.self)
        id = try values.decodeIfPresent(Int64.self, forKey: .id) ?? 0
        kind = try values.decodeIfPresent(String.self, forKey: .kind) ?? ""
        model = try values.decodeIfPresent(String.self, forKey: .model) ?? ""
        prompt = try values.decodeIfPresent(String.self, forKey: .prompt)
        params = try values.decodeIfPresent(ImagesJobParams.self, forKey: .params)
        statusWord = try values.decodeIfPresent(String.self, forKey: .statusWord) ?? ""
        pct = try values.decodeIfPresent(Int.self, forKey: .pct)
        statusText = try values.decodeIfPresent(String.self, forKey: .statusText)
        error = try values.decodeIfPresent(String.self, forKey: .error)
        result = try values.decodeIfPresent(ImagesJobResult.self, forKey: .result)
        completedAt = try values.decodeIfPresent(String.self, forKey: .completedAt)
    }

    public var status: ImagesJobStatus { ImagesJobStatus(statusWord) }

    /// The lane row's one line of text — `ImagesScreen.kt:341`.
    public var laneTitle: String { prompt ?? model }

    /// The progress line, composed exactly as `:362-366` composes it:
    /// `status_text` or `working…`, and the percentage only when there is
    /// one.
    public var progressLine: String {
        (statusText ?? ImagesStrings.working) + (pct.map { " · \($0)%" } ?? "")
    }

    /// Every image this generation produced.
    public var imageNames: [String] { result?.images ?? [] }

    /// The tiles a history card draws — **at most three**
    /// (`ImagesScreen.kt:398`). A fourth image is not drawn and not counted:
    /// see DECISIONS, "A fourth generated image is not shown, and not
    /// counted". No "+1 more" affordance, because it would open nothing.
    public var tileNames: [String] { Array(imageNames.prefix(3)) }

    /// The history card's monospace meta line — `ImagesScreen.kt:385-390`.
    ///
    /// Three facts the Kotlin spells out and this keeps:
    /// * the size appears **only when both** width and height are present
    ///   (`:387`) — half a size is not a size;
    /// * `model` is a non-null Kotlin `String` with a `""` default, so
    ///   `listOfNotNull` keeps it even when it is empty. An empty model
    ///   therefore leads with the separator on both platforms. Dropping it
    ///   here would be a silent divergence in the one line a person reads to
    ///   find out what made a picture;
    /// * `kind` and `completed_at` are decoded (the shape is the server's) and
    ///   **not drawn**, because no Android view draws either one and inventing
    ///   a date format for a field whose timezone convention nothing in this
    ///   repository describes is how `[[verify-against-reality]]` entries get
    ///   written.
    public var metaLine: String {
        var parts: [String] = [model]
        if let params {
            if let width = params.width, let height = params.height {
                parts.append("\(width) × \(height)")
            }
            if let steps = params.steps { parts.append("\(steps) steps") }
            if let seed = params.seed { parts.append("seed \(seed)") }
        }
        return parts.joined(separator: " · ")
    }
}

/// `GET …/jobs` — a wrapper over `jobs` (`ImagesScreen.kt:120`).
///
/// **Two wrappers, two different keys, one row type.** `…/history` answers
/// `history` (below), not `jobs`, and neither answers a bare array. That is
/// exactly the trap `design-notes/ios/stubhouse/fixtures/README.md` records:
/// three of Home's four reads decoded to nothing for a week because a wrapper
/// was read as an array, and "decoded to nothing" looks on screen like "no
/// data yet".
public struct ImagesJobList: Equatable, Sendable, Decodable {
    public let jobs: [ImagesJob]

    public init(jobs: [ImagesJob] = []) {
        self.jobs = jobs
    }

    private enum CodingKeys: String, CodingKey {
        case jobs
    }

    public init(from decoder: any Decoder) throws {
        let values = try decoder.container(keyedBy: CodingKeys.self)
        jobs = try values.decodeIfPresent([ImagesJob].self, forKey: .jobs) ?? []
    }
}

/// `GET …/history` — a wrapper over `history` (`ImagesScreen.kt:123`).
public struct ImagesHistoryList: Equatable, Sendable, Decodable {
    public let history: [ImagesJob]

    public init(history: [ImagesJob] = []) {
        self.history = history
    }

    private enum CodingKeys: String, CodingKey {
        case history
    }

    public init(from decoder: any Decoder) throws {
        let values = try decoder.container(keyedBy: CodingKeys.self)
        history = try values.decodeIfPresent([ImagesJob].self, forKey: .history) ?? []
    }
}

// MARK: - the engine line

/// Which of five things the top of the screen says — `ImagesScreen.kt:190-212`,
/// in Android's own `when` order.
///
/// A type rather than a `when` inside a view, because the order is the whole
/// behaviour: "no data **and** loading" must be tested before "no data", or a
/// screen that is still reading announces that the plugin did not answer.
public enum ImagesEngineLine: Equatable, Sendable {

    /// `:191` — nothing has come back yet.
    case loading

    /// `:192-195` — the read failed, or answered nothing. Carries the read's
    /// own message when it has one, else Android's sentence.
    case unavailable(detail: String)

    /// `:196-199` — the engine is not installed on this house.
    case notInstalled

    /// `:200-203` — installed, not running.
    case offline

    /// `:204-211` — a live dot and `engine running`.
    case running

    /// The `when` of `:190-212`, arm for arm.
    public static func of(_ state: ImagesGenState?, loading: Bool, error: String?) -> ImagesEngineLine {
        guard let state else {
            // Android reads `state.loading`, which ``ApiRead/loading`` is the
            // port of: true only while there has never been a value
            // (`ApiRead.swift:57`).
            if loading { return .loading }
            return .unavailable(detail: error ?? ImagesStrings.unavailableSub)
        }
        if !state.installed { return .notInstalled }
        if !state.engineOnline { return .offline }
        return .running
    }

    /// The card's title, or nil for the two arms that are not a card.
    public var title: String? {
        switch self {
        case .loading, .running: nil
        case .unavailable: ImagesStrings.unavailableTitle
        case .notInstalled: ImagesStrings.notInstalledTitle
        case .offline: ImagesStrings.offlineTitle
        }
    }

    /// The card's second line, or nil as above.
    public var sub: String? {
        switch self {
        case .loading, .running: nil
        case .unavailable(let detail): detail
        case .notInstalled: ImagesStrings.notInstalledSub
        case .offline: ImagesStrings.offlineSub
        }
    }
}

// MARK: - the plugin band

/// What the band above the screen can say — the part of
/// `PluginScreenFrame` (`ui/components/PluginFrame.kt:92`) an iPhone can
/// draw.
///
/// `ScreenRouter.kt:67` does not draw `ImagesScreen()` bare: it wraps it in
/// `PluginScreenFrame(CAP_IMAGEGEN, route.icon)`, which is a band naming the
/// plugin **and** a re-tint of the whole subtree with the plugin's
/// server-derived accent (`PluginFrame.kt:95-115`). The re-tint is **not**
/// ported: iOS's colours are static `Color` extensions
/// (`ios/Domovoi/DomovoiApp.swift:264-345`), there is no `DomovoiColors`
/// struct and no `LocalDomovoiColors`, so there is no theme environment to
/// swap — and faking it per view would be a second colour system. DECISIONS,
/// "The plugin accent is not ported".
///
/// With no plugin for the capability, Android renders the content bare
/// (`PluginFrame.kt:98-101`); here that is a nil ``of(_:)``.
public struct ImagesBand: Equatable, Sendable {

    /// `plugin.name?.takeIf { it.isNotBlank() } ?: plugin.slug`
    /// (`PluginFrame.kt:124`) — a blank name falls back to the slug, so the
    /// band never draws an empty space where a name should be.
    public let name: String

    /// `by <publisher>`, drawn only when the publisher is non-blank
    /// (`:144`). Nil rather than an empty string, so a view cannot draw a
    /// bare "by".
    public let publisher: String?

    /// Where `manage` goes — the dashboard's Plugins page, at this plugin's
    /// row (`:170-176`). **Relative**; the caller makes it absolute through
    /// its own client, which is the only thing that knows the address.
    public let managePath: String

    public init(name: String, publisher: String?, managePath: String) {
        self.name = name
        self.publisher = publisher
        self.managePath = managePath
    }

    public static func of(_ plugin: CapabilityPlugin?) -> ImagesBand? {
        guard let plugin else { return nil }
        let named = nonBlank(plugin.name)
        return ImagesBand(
            name: named ?? plugin.slug,
            publisher: nonBlank(plugin.publisher),
            managePath: "/#plugins/\(plugin.slug)"
        )
    }

    /// `takeIf { it.isNotBlank() }`, which is what both of the band's two
    /// names are guarded by (`PluginFrame.kt:124`, `:144`).
    ///
    /// One hair's difference: Kotlin keeps the original string and this keeps
    /// the trimmed one, so a name the plugin sent with padding does not draw
    /// its padding. Nothing else reads these values.
    private static func nonBlank(_ raw: String?) -> String? {
        guard let trimmed = raw?.trimmingCharacters(in: .whitespacesAndNewlines), !trimmed.isEmpty else {
            return nil
        }
        return trimmed
    }

    /// `"by $it"` (`PluginFrame.kt:144`), or nil.
    public var publisherLine: String? {
        publisher.map { ImagesStrings.by($0) }
    }

    /// `"manage $name on the dashboard's Plugins page"` (`:181`) — the
    /// screen-reader label of an icon-and-word control.
    public var manageLabel: String { ImagesStrings.manageLabel(name: name) }
}
