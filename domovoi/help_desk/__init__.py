"""Domovoi as its own help desk (part 9 of the assistant plan, 2026-10-21).

Chat and voice answer questions about Domovoi itself from what Domovoi
knows for certain, and from nothing else:

* :mod:`.index` — the manual (``web/static/manual-content.json``),
  ``docs/FAQ.md``, ``docs/TROUBLESHOOTING.md``, the help entries for the
  dashboard's error codes (:mod:`.error_codes`) and the help files of
  installed plugins (the manifest's ``help``), cut into passages and
  searched with a small BM25 of our own;
* :mod:`.source` — that index as a LOCAL lookup source (``domovoi.lookup``):
  always on, no internet, no permission card;
* :mod:`.answer` — an answer made only from the passages found, naming the
  section and linking to it, or "the manual doesn't cover that";
* :mod:`.facts` — live facts, read straight from the server: version,
  rooms, the internet answer, AI on or off, plugins and their versions;
* :mod:`.troubleshoot` — read-only summaries of the system's own records
  (rooms' health and self-checks, the update record, Home's attention
  checks, the container guard), through :mod:`.redact`;
* :mod:`.diagnostics` — the opt-in "report a problem" summary the person
  reviews, then copies or saves; nothing is sent;
* :mod:`.delivery` — voice "how do I…" steps sent to the asker's own
  phone as a home-server chat titled "Help: …".

Stdlib and the core's own modules only: the web process imports this too.
"""
