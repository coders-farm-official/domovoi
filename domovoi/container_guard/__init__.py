"""The container guard (docs/CONTAINER_GUARD.md).

The core knows which Docker containers should be running — its compose
stack, each room's music player, every plugin's declared containers — and
checks them every ``container_guard_interval_sec``. A failure (missing,
stopped, restarting, unhealthy, Docker not answering) opens an incident
that alerts loudly and resolves by itself; signs of tampering (a different
image, an unknown container under Domovoi's names, privileged or host
settings Domovoi never gives) open one that also freezes the risky
operations until an admin clears it.

* :mod:`.model` — the shapes; :mod:`.compose` — the compose file;
* :mod:`.expected` — what should exist; :mod:`.docker_cli` — what does;
* :mod:`.detect` — the rules; :mod:`.engine` — debounce, incidents, the
  check loop the ``container_guard`` worker runs;
* :mod:`.store` — the database; :mod:`.views` — what each tier sees;
* :mod:`.freeze` — the refusal; :mod:`.holds` — "being worked on".

Importable by the web process: nothing here imports
``domovoi.plugins_runtime`` at module level (only the core-side
:func:`domovoi.container_guard.engine.gather_expected` does, lazily).
"""
