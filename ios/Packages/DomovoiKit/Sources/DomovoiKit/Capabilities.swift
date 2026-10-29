import Foundation

/// What this server can do — `GET /api/capabilities`, the capability
/// manifest (design §8).
///
/// A port of `android/.../net/Capabilities.kt`. The web process serves it
/// from data it already has, so it answers even while the core is down. The
/// app fetches it at connect (and again on socket reconnect) and gates
/// compiled-in screens on it: the Stations route renders only when some
/// installed plugin declares the `"stations"` capability, and badges for
/// gated routes are skipped when the capability is absent. Unknown capability
/// strings are ignored, which is what makes a new plugin on an old app
/// harmless. The route table stays compiled in — only *visibility* is
/// data-driven.
///
/// Everything is defensive: a missing endpoint or a failed fetch means
/// ``Capabilities/empty``, which hides every gated screen. The one gate that
/// reads a server switch rather than a plugin, Chat on `features.ai`, fails
/// **open**: Chat hides only when the server says AI is off.
///
/// **Where the cache lives on iOS.** Android holds the fetched manifest in a
/// Compose `staticCompositionLocalOf` — in memory, for the life of the
/// connection, re-fetched at connect and on socket reconnect, never written
/// to disk. iOS gets the same lifetime from an `@Observable` connection model
/// owned by the shell and put into the environment, which is I1-C's file, not
/// this one. Nothing about the manifest is persisted on either phone: it
/// describes the server that is answering right now, and a cached one would
/// gate screens on a server that has since changed its plugins.
public struct Capabilities: Equatable, Sendable, Decodable {
    public let domovoiAPI: String?
    public let serverVersion: String?
    public let plugins: [CapabilityPlugin]
    public let handlerDisplay: [HandlerDisplay]
    public let features: [String: Bool]

    /// Gated screens hidden, neutral tones — what a failed fetch means.
    public static let empty = Capabilities()

    public init(
        domovoiAPI: String? = nil,
        serverVersion: String? = nil,
        plugins: [CapabilityPlugin] = [],
        handlerDisplay: [HandlerDisplay] = [],
        features: [String: Bool] = [:]
    ) {
        self.domovoiAPI = domovoiAPI
        self.serverVersion = serverVersion
        self.plugins = plugins
        self.handlerDisplay = handlerDisplay
        self.features = features
    }

    enum CodingKeys: String, CodingKey {
        case domovoiAPI = "domovoi_api"
        case serverVersion = "server_version"
        case plugins
        case handlerDisplay = "handler_display"
        case features
    }

    /// Written out rather than synthesised so every field has the Kotlin
    /// `data class`'s default. `decodeIfPresent ?? default` also absorbs an
    /// explicit JSON `null` in a field the Kotlin declares non-null, which is
    /// what `coerceInputValues` does for it.
    public init(from decoder: any Decoder) throws {
        let c = try decoder.container(keyedBy: CodingKeys.self)
        domovoiAPI = try c.decodeIfPresent(String.self, forKey: .domovoiAPI)
        serverVersion = try c.decodeIfPresent(String.self, forKey: .serverVersion)
        plugins = try c.decodeIfPresent([CapabilityPlugin].self, forKey: .plugins) ?? []
        handlerDisplay = try c.decodeIfPresent([HandlerDisplay].self, forKey: .handlerDisplay) ?? []
        features = try c.decodeIfPresent([String: Bool].self, forKey: .features) ?? [:]
    }

    /// True when any installed plugin declares `capability`.
    public func has(_ capability: String) -> Bool {
        plugins.contains { $0.appCapabilities.contains(capability) }
    }

    /// The plugin that provides `capability` (the first, as ``has(_:)``
    /// counts).
    public func pluginFor(_ capability: String) -> CapabilityPlugin? {
        plugins.first { $0.appCapabilities.contains(capability) }
    }

    /// Server-supplied tone slug for a handler/source name; `"neutral"` when
    /// the name is unknown (design §8). The server's value passes through
    /// even when it is a slug this app has never heard of; the view maps an
    /// unknown one to neutral, as Android's does.
    public func toneFor(_ name: String?) -> String {
        handlerDisplay.first { $0.name == name }?.tone ?? "neutral"
    }

    /// Server-supplied label for a handler/source name, else the raw name.
    public func labelFor(_ name: String?) -> String? {
        handlerDisplay.first { $0.name == name }?.label ?? name
    }

    /// Fetch the manifest for the active server.
    ///
    /// Never throws, and that is the rule rather than laziness: Android's
    /// `rememberCapabilities` decodes an error to `data == null` and every
    /// caller treats that as ``empty``, because a manifest that did not load
    /// must hide the gated screens rather than take the app down. A caller
    /// that wants to know *why* it failed should ask the client directly.
    ///
    /// **One deliberate difference from Android.** This read is untracked.
    /// `GET /api/capabilities` is an **Open** route (docs/API_REFERENCE.md),
    /// so its 200 says nothing about whether this phone is paired — and
    /// Android fetches it with a tracked `api.get`, which means its 200
    /// *clears* the pairing screen that a household read's 401 had just
    /// raised. That is precisely the flicker `getUntracked` exists to
    /// prevent, and the manifest is fetched at connect and on every socket
    /// reconnect, which is when an unpaired phone is most likely to be doing
    /// both at once. Reported to the owner as an Android finding rather than
    /// fixed there; `android/` is out of bounds for this batch.
    public static func fetch(using client: DomovoiClient) async -> Capabilities {
        (try? await client.getUntracked("/api/capabilities", as: Capabilities.self)) ?? .empty
    }

    /// False only when the server says its AI features are off
    /// (`features.ai == false`). Absent means on: an older server has no
    /// switch, and a manifest that failed to load (``empty``) must not hide
    /// Chat on a server that has its models.
    public var aiEnabled: Bool { features[featureAI] != false }
}

/// Well-known capability slugs.
public let capStations = "stations"
public let capImagegen = "imagegen"

/// `features.ai`: the household's "AI features" switch on the server (the
/// chat models). `false` = turned off, so the server's Chat (and its chat
/// mic) can't answer and the app hides it. **Not** `features.chat`, which is
/// the voice open-mic chat mode.
public let featureAI = "ai"

public struct CapabilityPlugin: Equatable, Sendable, Decodable {
    public let slug: String
    public let version: String?

    /// For the plugin screen frame: the manifest's name and publisher, and
    /// the page colour per theme. Absent from an older server, and the frame
    /// then shows the slug and keeps the app's own colours.
    public let name: String?
    public let publisher: String?
    public let accent: PluginAccent?

    /// The capabilities this plugin declares for a phone app.
    ///
    /// **The wire name is `android_capabilities`, and iOS reads that field.**
    /// It is the only list the server publishes — there is no
    /// `ios_capabilities` — and a plugin that declares `stations` means the
    /// same thing to both phones, so reading Android's field is correct
    /// rather than a shortcut. The Swift name drops the platform because a
    /// property called `androidCapabilities` in an iOS app reads like a bug.
    /// Renaming the server's field is a server batch, and it would have to
    /// keep answering the old name for every shipped Android build.
    public let appCapabilities: [String]

    public init(
        slug: String = "",
        version: String? = nil,
        name: String? = nil,
        publisher: String? = nil,
        accent: PluginAccent? = nil,
        appCapabilities: [String] = []
    ) {
        self.slug = slug
        self.version = version
        self.name = name
        self.publisher = publisher
        self.accent = accent
        self.appCapabilities = appCapabilities
    }

    enum CodingKeys: String, CodingKey {
        case slug, version, name, publisher, accent
        case appCapabilities = "android_capabilities"
    }

    public init(from decoder: any Decoder) throws {
        let c = try decoder.container(keyedBy: CodingKeys.self)
        slug = try c.decodeIfPresent(String.self, forKey: .slug) ?? ""
        version = try c.decodeIfPresent(String.self, forKey: .version)
        name = try c.decodeIfPresent(String.self, forKey: .name)
        publisher = try c.decodeIfPresent(String.self, forKey: .publisher)
        accent = try c.decodeIfPresent(PluginAccent.self, forKey: .accent)
        appCapabilities = try c.decodeIfPresent([String].self, forKey: .appCapabilities) ?? []
    }
}

/// A plugin's accent as the server derived it (plugins_runtime/accent.py):
/// per theme, `brand`, `brand_hover`, `brand_press`, `brand_fg`,
/// `brand_soft`, `brand_halo`, `brand_ring`, `brand_ink` as `#rrggbb` or
/// `#rrggbbaa`, already contrast-checked against the dashboard's surfaces
/// (the app's are the same tokens). `source` is "plugin" or "default" (the
/// neutral plugin slate).
public struct PluginAccent: Equatable, Sendable, Decodable {
    public let source: String
    public let light: [String: String]
    public let dark: [String: String]

    public init(source: String = "default", light: [String: String] = [:], dark: [String: String] = [:]) {
        self.source = source
        self.light = light
        self.dark = dark
    }

    enum CodingKeys: String, CodingKey {
        case source, light, dark
    }

    public init(from decoder: any Decoder) throws {
        let c = try decoder.container(keyedBy: CodingKeys.self)
        source = try c.decodeIfPresent(String.self, forKey: .source) ?? "default"
        light = try c.decodeIfPresent([String: String].self, forKey: .light) ?? [:]
        dark = try c.decodeIfPresent([String: String].self, forKey: .dark) ?? [:]
    }
}

/// One handler's display metadata: server-supplied label + tone slug. Tone
/// slugs are the open set neutral|media|device|info|comms; anything unknown
/// renders as neutral.
public struct HandlerDisplay: Equatable, Sendable, Decodable {
    public let name: String
    public let label: String?
    public let tone: String

    public init(name: String = "", label: String? = nil, tone: String = "neutral") {
        self.name = name
        self.label = label
        self.tone = tone
    }

    enum CodingKeys: String, CodingKey {
        case name, label, tone
    }

    public init(from decoder: any Decoder) throws {
        let c = try decoder.container(keyedBy: CodingKeys.self)
        name = try c.decodeIfPresent(String.self, forKey: .name) ?? ""
        label = try c.decodeIfPresent(String.self, forKey: .label)
        tone = try c.decodeIfPresent(String.self, forKey: .tone) ?? "neutral"
    }
}
