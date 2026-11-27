"""Away chat (1.5.0): a paired phone chats with its own house from anywhere,
over a Tor onion service of its own (``hs-away``), reusing the Friend
Houses plumbing. docs/SECURITY_PRIVACY.md, "Away chat".

* :mod:`domovoi.away.wire`: the frame, header, JSON and picture parser
  (pure, no sockets: fuzzed and unit-tested on its own);
* :mod:`domovoi.away.listener`: the ``away.sock`` side of the sandboxed
  Friend Houses listener container;
* :mod:`domovoi.away.keys`: the house's away Noise key and onion key;
* :mod:`domovoi.away.store`: enrolled phones and the house setting;
* :mod:`domovoi.away.bridge`: the core's relay between the listener and
  the web process's chat code (``web/backend/away_bridge.py``).
"""
