import Foundation
@testable import DomovoiKit

/// The manifest rows a house with **our** plugins publishes, for the tests
/// whose subject is something downstream of the gate (a launcher, the phone
/// bar, the manual, the band).
///
/// **Written out, not derived from ``requiredPlugins``**: a test that built
/// its fixture from the table it is checking would follow a wrong table and
/// pass. The slugs and publisher are the manifests' own
/// (`plugins/radio/domovoi-plugin.toml`, and the image generation plugin's).
enum OurPlugins {
    static let radio = CapabilityPlugin(
        slug: "radio",
        name: "Radio",
        publisher: "Coders Farm",
        appCapabilities: ["stations"]
    )

    static let imagegen = CapabilityPlugin(
        slug: "imagegen",
        name: "Image Generation",
        publisher: "Coders Farm",
        appCapabilities: ["imagegen"]
    )
}

extension Capabilities {

    /// A manifest listing our plugin for each of `capabilities` — `stations`
    /// is the radio plugin, `imagegen` the image generation plugin. Anything
    /// else adds nothing: there is no plugin of ours for it.
    static func ours(_ capabilities: String...) -> Capabilities {
        ours(capabilities)
    }

    /// ``ours(_:)-variadic``, from a list a helper was handed.
    static func ours(_ capabilities: [String]) -> Capabilities {
        Capabilities(plugins: capabilities.compactMap { capability -> CapabilityPlugin? in
            switch capability {
            case "stations": return OurPlugins.radio
            case "imagegen": return OurPlugins.imagegen
            default: return nil
            }
        })
    }
}
