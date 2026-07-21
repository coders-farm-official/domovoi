"""Domovoi radio plugin — internet radio + FM via RTL-SDR.

Published by Coders Farm; MIT. This package is the bundled flagship
plugin AND the reference example for Domovoi plugin development.

Layout (design §2.1):

* ``core.py`` — the core-process entry point: ``register(ctx)``.
* ``web.py``  — the web-process entry point: ``register_web(ctx)``.
  It must never import ``core.py`` or the Domovoi runtime.
* ``handlers/`` / ``workers/`` / ``clients/`` — internal structure.
"""

SCHEMA = "plugin_radio"

# Plugin-branded outbound UA (several directories — radio-browser.info
# among them — ask clients to identify themselves).
USER_AGENT = "domovoi-radio/1.0 (+https://github.com/coders-farm-official/domovoi)"
