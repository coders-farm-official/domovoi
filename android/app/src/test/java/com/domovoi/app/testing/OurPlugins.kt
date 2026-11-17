package com.domovoi.app.testing

import com.domovoi.app.net.CAP_IMAGEGEN
import com.domovoi.app.net.CAP_STATIONS
import com.domovoi.app.net.Capabilities
import com.domovoi.app.net.CapabilityPlugin

/*
 * Our two plugins as GET /api/capabilities lists them, from their manifests
 * (plugins/radio/domovoi-plugin.toml; the Image Generation plugin's): the
 * literal slug and publisher, written out here rather than read from the
 * app's table, so a test notices if the table drifts.
 */

internal val ourRadio = CapabilityPlugin(
    slug = "radio", name = "Radio", publisher = "Coders Farm",
    androidCapabilities = listOf(CAP_STATIONS),
)

internal val ourImagegen = CapabilityPlugin(
    slug = "imagegen", name = "Image Generation", publisher = "Coders Farm",
    androidCapabilities = listOf(CAP_IMAGEGEN),
)

/** A manifest with our plugin behind each of [capabilities] (stations,
 *  imagegen), and [features]. */
internal fun ourCaps(vararg capabilities: String, features: Map<String, Boolean> = emptyMap()) = Capabilities(
    plugins = capabilities.map {
        when (it) {
            CAP_STATIONS -> ourRadio
            CAP_IMAGEGEN -> ourImagegen
            else -> error("no plugin of ours declares $it")
        }
    },
    features = features,
)
