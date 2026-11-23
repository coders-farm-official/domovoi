# Tiptap 3.33.1 + ProseMirror (vendored)

`tiptap.min.js` sets `window.DomovoiTiptap`: the Tiptap editor, the
extensions the Documents editor uses (StarterKit, Underline, TextStyle,
Color, Highlight, TextAlign, FontFamily, Link, Table, TaskList/TaskItem,
Image, Placeholder, Collaboration, CollaborationCaret), ProseMirror's
state/view/model/tables, prosemirror-markdown's serializer, markdown-it,
and y-tiptap 3.3.0 (`DomovoiTiptap.yTiptap`, Tiptap's fork of
y-prosemirror; documents are stored in Yjs the same way).

It needs `window.Y` from `../yjs/yjs.min.js`, loaded first: the bundle has
no Yjs of its own.

`web/static/doc_editor.jsx` loads both on demand and builds
`window.DomovoiDocEditor` on top. Nothing else should use this directly.

Exact versions and licences of all 50 packages inside: `THIRD-PARTY.json`;
licence texts: `licenses/` (most of Tiptap's npm packages ship without one,
so its repository's MIT text is used). All MIT, plus entities (BSD-2-Clause).

Rebuild: `scripts/vendor/editors/build.mjs` (see `../yjs/README.md`).
