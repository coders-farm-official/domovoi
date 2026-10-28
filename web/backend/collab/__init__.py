"""Real-time co-editing for Documents (editors v1, builder B).

Browsers and the app run **Yjs** with the stock ``y-websocket`` provider
(from the one Yjs bundle, ``web/static/vendor/yjs``, opened by
``window.DomovoiCollab.open`` in ``web/static/collab.js``); this process runs
**pycrdt** and speaks the same sync and awareness protocol on
``/api/collab/{doc_id}`` (``web/backend/api/collab.py``).

Modules:
  protocol  the y-websocket frames, plus Domovoi's small control messages
  guard     a cheap linear pre-filter every incoming update passes
  rooms     one live room per open document: relay, persistence, saving,
            history, outside-change detection, limits
  workers   starting, calling (with timeouts) and replacing workers
  worker    the subprocess that holds a document: the ONLY place pycrdt
            runs (apply, diff, encode, restore, preview), with a nesting
            check after every apply
  store     Postgres (V042): collab_docs, collab_updates, collab_snapshots
  content   what the worker reads of a document (restore, preview)

**Crash isolation (security review round 2).** yrs, under pycrdt, recurses
without a depth limit, and a crafted update can overflow the native stack.
So the web process never loads pycrdt: each open document lives in its own
worker subprocess, an update is relayed and stored only after its worker
has taken it, and a worker that dies or stalls costs only the update that
did it (its sender is closed, a new worker loads the document from
Postgres, everyone else stays and resyncs). A stored state that breaks the
worker twice turns co-editing off for that document; it then opens
single-editor from its file until the file changes.

**Two copies, one truth each.**

* The **shared state** (Yjs) is authoritative while a document is being
  edited. It lives in Postgres, never in the file.
* The **file** in Documents is always a clean, ordinary ``.html`` / ``.md`` /
  ``.docx`` / ``.xlsx`` that any other program can open. Nothing about
  co-editing is ever written into it.

**The save path (decided 2026-10-26): the client writes the file.** The
server cannot turn a Tiptap/ProseMirror document into HTML, Markdown or
Word without the editor's own schema and serializers, which are JavaScript
(builder A). So:

1. While people edit, the room keeps the shared state and counts edits
   (``rev``). When there are edits the file doesn't have and nobody has
   typed for ``SAVE_IDLE_SEC`` (2.5 s), the room sends a ``save`` control
   message to ONE writable client: the one that edited last (the "last
   active editor"), among clients that said they can save.
2. That client serializes its editor (it is synced, so it holds everything)
   and saves through the **normal Documents save route**
   (builder A's ``PUT /api/documents/doc/...`` for ``.html``/``.md``/
   ``.docx``, ``PUT /api/documents/sheet/...`` for spreadsheets,
   ``PUT /api/documents/text/...``), with the header
   ``X-Domovoi-Collab: <doc_id>;<epoch>;<rev>``. Same tier, same path checks,
   same per-device block as any save.
3. The save route calls :func:`web.backend.api.collab.note_file_saved`, which
   records the new file's SHA-256, size and mtime and that ``rev`` is saved.
4. **On leave**, the helper saves before it disconnects (``close()``), and
   tries once more on ``pagehide``. If the last editor vanishes without
   saving (a crash, a dead battery), ``rev > saved_rev`` is kept in the
   database and the next editor to open the document is asked to save as
   soon as it has synced.
5. A save WITHOUT the header (an editor that is not co-editing, the app's
   older screens), an upload over the file, or another program changing it
   on disk shows up as a fingerprint that is not the recorded one. The room
   then **rebuilds from the file**: the co-edited state is kept in history
   ("before_rebuild"), the shared state starts again from the file under a
   new **epoch**, and connected editors are told to reopen. A phone that was
   offline across that comes back with the old epoch: its copy is kept as
   an "offline copy" version (restorable), and it starts again from the new
   one rather than merging old text into new (which would duplicate it).

**Seeding.** A document's shared state starts empty. The first writable
client to connect gets the seeding lease (``hello.seed``), loads the file the
usual way, puts it into the shared document, and reports ``seeded``; the
room then tells everyone ``ready``. Editors stay read-only until ready.
"""
