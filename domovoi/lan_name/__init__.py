"""The server's name on the home network: ``http://domovoi.local:6369``
instead of an address (design note MDNS-NAME-PLAN; docs/LINUX_HOST.md and
docs/WINDOWS_HOST.md, "The network name").

* :mod:`.names` — what a name may be, and the default's ``domovoi``,
  ``domovoi2`` ... sequence (golden list: ``vectors.json``, shared with
  the C# copy).
* :mod:`.packet` — the bounded mDNS parser and the packets we send.
* :mod:`.responder` — probe, announce, answer, defend: a state machine
  with no sockets, driven with the time.
* :mod:`.service` — who announces (the core on Linux, DomovoiHost on the
  Windows install), the request/report files, the state Settings shows.

Standard library only: python-zeroconf and Avahi's library are LGPL
(docs/CONTRIBUTING.md). A name is never proof of identity: satellites
dial the address it resolves to and still check the server's key.
"""
