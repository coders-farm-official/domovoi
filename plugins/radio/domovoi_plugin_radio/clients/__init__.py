"""External-service clients for the radio plugin.

Every client follows the same shape: a Protocol, a deterministic stub, a
real implementation, and a module-level getter parameterized on
``use_stubs`` (the plugin reads that flag from ``sdk.core_config``).
Outbound HTTP always carries the plugin UA
(:data:`domovoi_plugin_radio.USER_AGENT`).
"""
