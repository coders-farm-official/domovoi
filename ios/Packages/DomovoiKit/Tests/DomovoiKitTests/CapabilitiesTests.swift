import XCTest
@testable import DomovoiKit

/// The capability manifest (design §8): decoding and the gates built on it.
///
/// A port of `android/.../net/CapabilitiesTest.kt`, with the same manifest.
/// The tolerance is the point: a server that grows a field, a plugin that
/// declares a capability this app has never heard of, and a manifest that did
/// not load at all must all leave the app standing.
final class CapabilitiesTests: XCTestCase {

    private let manifest = Data("""
        {
          "domovoi_api": "1",
          "server_version": "0.9.0",
          "plugins": [
            {"slug": "radio", "version": "1.2", "android_capabilities": ["stations"]},
            {"slug": "provider", "android_capabilities": []},
            {"slug": "future", "android_capabilities": ["holograms"], "extra_field": true}
          ],
          "handler_display": [
            {"name": "radio", "label": "Radio", "tone": "media"},
            {"name": "weird", "tone": "sparkly"}
          ],
          "features": {"chat": true},
          "unknown_top_level": 42
        }
        """.utf8)

    private func decoded() throws -> Capabilities {
        try JSONDecoder().decode(Capabilities.self, from: manifest)
    }

    func testDecodesTolerantly() throws {
        let caps = try decoded()
        XCTAssertEqual(caps.serverVersion, "0.9.0")
        XCTAssertEqual(caps.domovoiAPI, "1")
        XCTAssertEqual(caps.plugins.count, 3)
        XCTAssertEqual(caps.plugins[1].appCapabilities, [])
        XCTAssertNil(caps.plugins[1].version)
        XCTAssertEqual(caps.features, ["chat": true])
    }

    func testAnEmptyManifestDecodesFromAnEmptyObject() throws {
        XCTAssertEqual(try JSONDecoder().decode(Capabilities.self, from: Data("{}".utf8)), .empty)
    }

    /// A field the server sends as `null` where the Kotlin declares it
    /// non-null: `coerceInputValues` gives it the default there, and
    /// `decodeIfPresent ?? default` gives it the default here.
    func testAnExplicitNullTakesTheDefault() throws {
        let caps = try JSONDecoder().decode(Capabilities.self, from: Data("""
            {"plugins": null, "features": null, "handler_display": null, "server_version": null}
            """.utf8))
        XCTAssertEqual(caps, .empty)
        let plugin = try JSONDecoder().decode(CapabilityPlugin.self, from: Data("""
            {"slug": null, "android_capabilities": null, "accent": null}
            """.utf8))
        XCTAssertEqual(plugin.slug, "")
        XCTAssertEqual(plugin.appCapabilities, [])
        XCTAssertNil(plugin.accent)
    }

    func testHasMatchesAnyPluginDeclaringIt() throws {
        let caps = try decoded()
        XCTAssertTrue(caps.has(capStations))
        XCTAssertTrue(caps.has("holograms"), "unknown slugs are still data")
        XCTAssertFalse(caps.has(capImagegen))
        XCTAssertFalse(Capabilities.empty.has(capStations))
        XCTAssertEqual(caps.pluginFor(capStations)?.slug, "radio")
        XCTAssertNil(caps.pluginFor(capImagegen))
    }

    /// `features.ai` is off only when the server says false. Absent (an older
    /// server; a manifest that failed to load) counts as on, and
    /// `features.chat` — the voice open-mic mode — is not it.
    func testAiEnabledIsOffOnlyWhenTheServerSaysSo() throws {
        XCTAssertTrue(Capabilities.empty.aiEnabled)
        XCTAssertTrue(try decoded().aiEnabled)
        XCTAssertTrue(Capabilities(features: [featureAI: true]).aiEnabled)
        XCTAssertFalse(Capabilities(features: [featureAI: false]).aiEnabled)
        XCTAssertTrue(Capabilities(features: ["chat": false]).aiEnabled)
        let off = try JSONDecoder().decode(
            Capabilities.self,
            from: Data(#"{"features": {"chat": true, "ai": false}}"#.utf8)
        )
        XCTAssertFalse(off.aiEnabled)
        XCTAssertEqual(featureAI, "ai")
    }

    func testToneAndLabelFallBackForUnknownHandlers() throws {
        let caps = try decoded()
        XCTAssertEqual(caps.toneFor("radio"), "media")
        XCTAssertEqual(caps.labelFor("radio"), "Radio")
        XCTAssertEqual(caps.toneFor("weird"), "sparkly", "the server's value passes through; the view maps unknown to neutral")
        XCTAssertEqual(caps.labelFor("weird"), "weird", "no label: the raw name")
        XCTAssertEqual(caps.toneFor("nope"), "neutral")
        XCTAssertEqual(caps.labelFor("nope"), "nope")
        XCTAssertNil(caps.labelFor(nil))
        XCTAssertEqual(Capabilities.empty.toneFor("radio"), "neutral")
    }

    func testAPluginsAccentDecodesPerTheme() throws {
        let caps = try JSONDecoder().decode(Capabilities.self, from: Data("""
            {"plugins":[{"slug":"radio","name":"Radio","publisher":"Coders Farm",
             "accent":{"source":"plugin","light":{"brand":"#336699"},"dark":{"brand":"#99ccff"}},
             "android_capabilities":["stations"]}]}
            """.utf8))
        let plugin = try XCTUnwrap(caps.plugins.first)
        XCTAssertEqual(plugin.name, "Radio")
        XCTAssertEqual(plugin.publisher, "Coders Farm")
        XCTAssertEqual(plugin.accent?.source, "plugin")
        XCTAssertEqual(plugin.accent?.light["brand"], "#336699")
        XCTAssertEqual(plugin.accent?.dark["brand"], "#99ccff")
        XCTAssertEqual(PluginAccent().source, "default", "the neutral plugin slate")
    }

    // ---- the fetch rule -------------------------------------------------------

    private func client() -> DomovoiClient {
        DomovoiClient(
            book: .fixed(server: "http://10.0.0.9:6369", token: "household-abc"),
            configuration: StubURLProtocol.session(timeout: 5)
        )
    }

    func testTheManifestIsFetchedFromTheOpenRoute() async throws {
        StubURLProtocol.reset()
        StubURLProtocol.enqueue(StubURLProtocol.Answer(status: 200, body: manifest))
        let caps = await Capabilities.fetch(using: client())
        XCTAssertTrue(caps.has(capStations))
        XCTAssertEqual(StubURLProtocol.recorded.first?.url?.path, "/api/capabilities")
    }

    /// A manifest that did not load hides the gated screens rather than
    /// taking the app down — and it is an untracked read, so its 200 can
    /// never clear a pairing screen a household read has raised (see the
    /// comment on `Capabilities.fetch`).
    func testAFetchThatFailsIsAnEmptyManifestAndNeverAnError() async {
        StubURLProtocol.reset()
        StubURLProtocol.enqueue(StubURLProtocol.Answer(status: 404))
        var caps = await Capabilities.fetch(using: client())
        XCTAssertEqual(caps, .empty)
        StubURLProtocol.enqueue(StubURLProtocol.Answer(error: URLError(.cannotConnectToHost)))
        caps = await Capabilities.fetch(using: client())
        XCTAssertEqual(caps, .empty)
        StubURLProtocol.enqueue(.json("<html>a router's login page</html>"))
        caps = await Capabilities.fetch(using: client())
        XCTAssertEqual(caps, .empty)
        XCTAssertFalse(caps.has(capStations))
        XCTAssertTrue(caps.aiEnabled, "and Chat is not hidden by a manifest that never arrived")
    }

    func testAnUnpairedPhonesPairingScreenSurvivesACapabilityFetch() async {
        StubURLProtocol.reset()
        let client = client()
        client.notePossiblePairingRefusal(status: 401, body: "X-Device-Token or admin session required")
        XCTAssertTrue(client.pairingRequired)
        StubURLProtocol.enqueue(StubURLProtocol.Answer(status: 200, body: manifest))
        _ = await Capabilities.fetch(using: client)
        XCTAssertTrue(client.pairingRequired, "an Open route's 200 says nothing about pairing")
    }

    /// The wire name is Android's, and iOS reads it. There is no
    /// `ios_capabilities` on the server, and a plugin that declares
    /// `stations` means the same thing to both phones.
    func testTheCapabilityListIsReadFromTheServersAndroidField() throws {
        let plugin = try JSONDecoder().decode(
            CapabilityPlugin.self,
            from: Data(#"{"slug":"radio","android_capabilities":["stations"]}"#.utf8)
        )
        XCTAssertEqual(plugin.appCapabilities, ["stations"])
        let hopeful = try JSONDecoder().decode(
            CapabilityPlugin.self,
            from: Data(#"{"slug":"radio","ios_capabilities":["stations"]}"#.utf8)
        )
        XCTAssertEqual(hopeful.appCapabilities, [], "there is no such field, and reading one would gate on nothing")
    }
}
