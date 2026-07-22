"""Locally-prepared satellite media (design: plan workstream B).

Everything Domovoi-specific on a satellite's SD card derives from THIS
machine — the local code tree, the locally-installed plugins, a local
wheel/deb cache — never a CI artifact. The primary path is a **boot-partition
overlay + offline payload** written onto a stock-flashed card (no privileged
image builds on Windows/WSL2); see ``builder.py`` for the phase pipeline and
``docs/SATELLITE_HARDWARE.md`` for the user-facing flow.
"""
