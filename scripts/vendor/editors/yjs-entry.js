// Entry for web/static/vendor/yjs/yjs.min.js (built by build.mjs).
//
// ONE copy of Yjs for the whole dashboard. The Documents editor bundle
// (vendor/tiptap) does not carry its own: its y-prosemirror is bound to
// this `window.Y`, because two copies of Yjs in one page break Yjs's
// type checks (and Yjs warns about it). Load this file first.
//
//   window.Y      the yjs module (Y.Doc, Y.UndoManager, encodeStateAsUpdate, ...)
//   window.YKit   the protocol and provider helpers that share this Y:
//                 awareness, sync (y-protocols), encoding, decoding (lib0),
//                 WebsocketProvider (y-websocket), IndexeddbPersistence
//                 (y-indexeddb), versions
import * as Y from 'yjs';
import * as awareness from 'y-protocols/awareness';
import * as sync from 'y-protocols/sync';
import * as encoding from 'lib0/encoding';
import * as decoding from 'lib0/decoding';
import { WebsocketProvider } from 'y-websocket';
import { IndexeddbPersistence } from 'y-indexeddb';

/* global __VERSIONS__ */
window.Y = Y;
window.YKit = {
  awareness,
  sync,
  encoding,
  decoding,
  WebsocketProvider,
  IndexeddbPersistence,
  versions: __VERSIONS__,
};
