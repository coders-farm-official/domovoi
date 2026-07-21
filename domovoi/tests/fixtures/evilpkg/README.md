# evilpkg — security test fixture (NOT a real package)

A deliberately hostile source distribution used only by
`test_plugin_installer.py`. Its `setup.py` build backend writes a marker
file when executed, so the test can prove the installer's lockfile
validation rejects a `--no-binary :all:` smuggle **before** pip ever
builds an sdist — i.e. the marker is never written.

Do not add this to any real requirement set. The name is a generic
stand-in for an attacker-published dependency.
