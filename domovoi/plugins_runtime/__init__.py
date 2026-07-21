"""The plugin runtime (design §2–§4, §6.2, §7.4, §13.2).

Core-process-only machinery: manifest parsing/validation, the loader
(:mod:`.loader`), the install pipeline (:mod:`.installer`), the plugin
migration runner (:mod:`.migrations`), the ``plugins`` registry-table
access layer (:mod:`.registry`), the declarative worker registry
(:mod:`.workers`), plugin config contribution (:mod:`.config_bridge`),
chat-tool resync (:mod:`.letta_resync`) and the ``domovoi plugin`` CLI
(:mod:`.cli`).

The web process (:6369) must NEVER import this package — it reads the
``plugins`` registry table's ``manifest`` JSONB only (design §5.1).
"""

from __future__ import annotations

from domovoi.plugins_runtime.manifest import (  # noqa: F401
    ManifestError,
    PluginManifest,
    parse_manifest,
    parse_manifest_dir,
)
from domovoi.plugins_runtime.loader import LOADER, PluginContext  # noqa: F401
from domovoi.plugins_runtime.workers import (  # noqa: F401
    WORKERS,
    LongRunWorker,
    Worker,
    WorkerRunner,
)
from domovoi.plugins_runtime.config_bridge import PLUGIN_CONFIG, FieldSpec  # noqa: F401

__all__ = [
    "FieldSpec",
    "LOADER",
    "LongRunWorker",
    "ManifestError",
    "PLUGIN_CONFIG",
    "PluginContext",
    "PluginManifest",
    "WORKERS",
    "Worker",
    "WorkerRunner",
    "parse_manifest",
    "parse_manifest_dir",
]
