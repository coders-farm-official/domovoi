/* The tunnel: inner framing plus the relay socket.
 *
 * Mirrors plugin-ltl-remote/domovoi_plugin_ltl_remote/framing.py, and
 * layers on top of e2e.js. What comes out is a `fetch`-shaped call that
 * happens to run over an end-to-end encrypted WebSocket to somebody's
 * house:
 *
 *     const tunnel = await Tunnel.connect({...});
 *     const response = await tunnel.request('GET', '/api/satellites');
 *
 * The relay in between sees an 18-byte header and an opaque blob. It
 * cannot see the method, the path, the headers or the body, and that is
 * the entire reason this file is more complicated than an XHR.
 */

import { ClientHandshake, b64u, unb64u, concat, utf8 } from './e2e.js';

/* ── outer frame (PROTOCOL.md §2) ────────────────────────────────────── */
/* On a client socket a frame is the payload alone: one socket is one
 * link, so the relay attaches and strips the header on the agent side. */

/* ── inner frame types (PROTOCOL.md §5) ──────────────────────────────── */
export const REQ = 0x01;
export const REQ_CHUNK = 0x02;
export const REQ_END = 0x03;
export const RES_HEAD = 0x11;
export const RES_CHUNK = 0x12;
export const RES_END = 0x13;
export const WS_OPEN = 0x21;
export const WS_OPEN_OK = 0x22;
export const WS_DATA = 0x23;
export const WS_CLOSE = 0x24;
export const PING = 0x31;
export const PONG = 0x32;
export const ERROR = 0x41;

const MAX_HEADER_BYTES = 64 * 1024;
const MAX_INNER_BYTES = 4 * 1024 * 1024;

export function encodeInner(type, streamId, header = {}, body = new Uint8Array()) {
  const headerBytes = utf8(JSON.stringify(header));
  if (headerBytes.length > MAX_HEADER_BYTES) throw new Error('inner header is too large');
  const out = new Uint8Array(9 + headerBytes.length + body.length);
  const view = new DataView(out.buffer);
  out[0] = type;
  view.setUint32(1, streamId, false);
  view.setUint32(5, headerBytes.length, false);
  out.set(headerBytes, 9);
  out.set(body, 9 + headerBytes.length);
  return out;
}

export function decodeInner(raw) {
  if (raw.length < 9) throw new Error('inner frame is shorter than its header');
  if (raw.length > MAX_INNER_BYTES) throw new Error('inner frame exceeds the size cap');
  const view = new DataView(raw.buffer, raw.byteOffset, raw.byteLength);
  const headerLength = view.getUint32(5, false);
  // Check the declared length against what we hold BEFORE slicing.
  if (headerLength > MAX_HEADER_BYTES) throw new Error('declared header length is too large');
  if (9 + headerLength > raw.length) throw new Error('declared header length runs past the frame');
  const headerText = new TextDecoder().decode(raw.subarray(9, 9 + headerLength));
  return {
    type: raw[0],
    streamId: view.getUint32(1, false),
    header: headerText ? JSON.parse(headerText) : {},
    body: raw.subarray(9 + headerLength),
  };
}

/* ── device keys, kept in IndexedDB ──────────────────────────────────── */
/*
 * CryptoKey objects are structured-clonable, so a non-extractable
 * private key can be stored and reloaded without ever being readable by
 * script — including by this file. localStorage could not do this: it
 * holds strings, and a string is by definition extractable.
 */

const DB_NAME = 'ltl-remote';
const STORE = 'keys';

const openDb = () => new Promise((resolve, reject) => {
  const request = indexedDB.open(DB_NAME, 1);
  request.onupgradeneeded = () => request.result.createObjectStore(STORE);
  request.onsuccess = () => resolve(request.result);
  request.onerror = () => reject(request.error);
});

const dbGet = async (key) => {
  const db = await openDb();
  return new Promise((resolve, reject) => {
    const request = db.transaction(STORE, 'readonly').objectStore(STORE).get(key);
    request.onsuccess = () => resolve(request.result);
    request.onerror = () => reject(request.error);
  });
};

const dbPut = async (key, value) => {
  const db = await openDb();
  return new Promise((resolve, reject) => {
    const transaction = db.transaction(STORE, 'readwrite');
    transaction.objectStore(STORE).put(value, key);
    transaction.oncomplete = () => resolve();
    transaction.onerror = () => reject(transaction.error);
  });
};

export const DeviceStore = {
  async keyPair() {
    const existing = await dbGet('deviceKey');
    if (existing) return existing;
    const created = await crypto.subtle.generateKey(
      { name: 'ECDH', namedCurve: 'P-256' }, false, ['deriveBits']);
    await dbPut('deviceKey', created);
    return created;
  },

  async publicKeyB64() {
    const pair = await this.keyPair();
    return b64u(new Uint8Array(await crypto.subtle.exportKey('raw', pair.publicKey)));
  },

  async deviceId() { return dbGet('deviceId'); },
  async setDeviceId(id) { return dbPut('deviceId', id); },

  /* Trust-on-first-use pinning. After one successful handshake the
   * household's key is fixed here; a change is a hard failure the user
   * has to look at, not something to shrug through. */
  async pinnedKey(householdId) { return dbGet('pin:' + householdId); },
  async pinKey(householdId, publicKeyB64) { return dbPut('pin:' + householdId, publicKeyB64); },

  async forget(householdId) { return dbPut('pin:' + householdId, undefined); },
};

/* ── the tunnel ──────────────────────────────────────────────────────── */

export class TunnelError extends Error {
  constructor(code, message) {
    super(message || code);
    this.code = code;
  }
}

export class Tunnel {
  constructor(socket, link, householdFingerprint) {
    this.socket = socket;
    this.link = link;
    this.fingerprint = householdFingerprint;
    this.nextStreamId = 1;
    this.streams = new Map();
    this.closed = false;
    this.onClose = null;

    socket.onmessage = (event) => this.receive(new Uint8Array(event.data));
    socket.onclose = (event) => this.handleClose(event);
  }

  /**
   * Open a tunnel to a household.
   *
   * Verifies the pinned key before anything else: if the household's key
   * has changed since last time, this throws rather than connecting, and
   * the caller shows both fingerprints.
   */
  static async connect({ relayUrl, householdId, deviceId, householdDhKey, onPinMismatch }) {
    const pinned = await DeviceStore.pinnedKey(householdId);
    if (pinned && pinned !== householdDhKey) {
      const mismatch = new TunnelError('KEY_CHANGED',
        'This house is presenting a different key than the one this device pinned.');
      if (onPinMismatch) onPinMismatch(pinned, householdDhKey);
      throw mismatch;
    }

    const deviceKey = await DeviceStore.keyPair();
    const url = `${relayUrl}?household=${encodeURIComponent(householdId)}`
      + `&device=${encodeURIComponent(deviceId)}`;

    const socket = new WebSocket(url);
    socket.binaryType = 'arraybuffer';

    await new Promise((resolve, reject) => {
      socket.onopen = resolve;
      socket.onerror = () => reject(new TunnelError('RELAY_UNREACHABLE',
        'Could not reach the relay.'));
      socket.onclose = (event) => reject(new TunnelError(
        event.reason || 'RELAY_CLOSED', closeReasonText(event.reason)));
    });

    const handshake = new ClientHandshake(deviceKey, unb64u(householdDhKey), deviceId);

    const send = (obj) => socket.send(utf8(JSON.stringify(obj)));
    const nextJson = () => new Promise((resolve, reject) => {
      socket.onmessage = (event) => {
        try { resolve(JSON.parse(new TextDecoder().decode(event.data))); }
        catch { reject(new TunnelError('PROTOCOL_ERROR', 'Unreadable handshake reply.')); }
      };
      socket.onclose = (event) => reject(new TunnelError(
        event.reason || 'RELAY_CLOSED', closeReasonText(event.reason)));
    });

    send(await handshake.hello());
    const homeHello = await nextJson();
    if (homeHello.t === 'error') {
      throw new TunnelError(homeHello.code, homeHello.message);
    }

    const { confirm, link } = await handshake.finish(homeHello);
    send(confirm);

    // The handshake verified; pin the key so a future substitution is a
    // hard stop rather than a silent success.
    await DeviceStore.pinKey(householdId, householdDhKey);

    return new Tunnel(socket, link, homeHello.household_fp);
  }

  /* ── requests ─────────────────────────────────────────────────────── */

  async request(method, path, { headers = {}, body = null } = {}) {
    if (this.closed) throw new TunnelError('CLOSED', 'The tunnel is closed.');
    const streamId = this.nextStreamId;
    this.nextStreamId += 2;              // odd ids are the client's

    const bodyBytes = body == null ? new Uint8Array()
      : (typeof body === 'string' ? utf8(body) : body);

    const stream = { chunks: [], status: 0, headers: {} };
    const finished = new Promise((resolve, reject) => {
      stream.resolve = resolve;
      stream.reject = reject;
    });
    this.streams.set(streamId, stream);

    await this.send(encodeInner(REQ, streamId, {
      method, path, headers, streaming: false,
    }, bodyBytes));

    return finished;
  }

  async json(method, path, body) {
    const response = await this.request(method, path, {
      headers: body ? { 'content-type': 'application/json' } : {},
      body: body ? JSON.stringify(body) : null,
    });
    const text = new TextDecoder().decode(response.body);
    return { status: response.status, data: text ? JSON.parse(text) : null };
  }

  async send(inner) {
    this.socket.send(await this.link.seal(inner));
  }

  /* ── receiving ────────────────────────────────────────────────────── */

  async receive(raw) {
    let frame;
    try {
      frame = decodeInner(await this.link.open(raw));
    } catch (error) {
      // A frame that fails authentication means the tunnel is no longer
      // trustworthy. There is no partial recovery worth attempting.
      this.failAll(new TunnelError('PROTOCOL_ERROR', error.message));
      this.close();
      return;
    }

    const stream = this.streams.get(frame.streamId);
    switch (frame.type) {
      case RES_HEAD:
        if (stream) {
          stream.status = frame.header.status;
          stream.headers = frame.header.headers || {};
        }
        break;
      case RES_CHUNK:
        if (stream) stream.chunks.push(frame.body);
        break;
      case RES_END:
        if (stream) {
          this.streams.delete(frame.streamId);
          stream.resolve({
            status: stream.status,
            headers: stream.headers,
            body: concat(...stream.chunks),
          });
        }
        break;
      case ERROR:
        if (stream) {
          this.streams.delete(frame.streamId);
          stream.reject(new TunnelError(frame.header.code, frame.header.message));
        }
        break;
      case PING:
        await this.send(encodeInner(PONG, 0, { ts: frame.header.ts }));
        break;
      default:
        break;
    }
  }

  handleClose(event) {
    this.closed = true;
    this.failAll(new TunnelError(
      event.reason || 'CLOSED', closeReasonText(event.reason)));
    if (this.onClose) this.onClose(event.reason);
  }

  failAll(error) {
    for (const stream of this.streams.values()) stream.reject(error);
    this.streams.clear();
  }

  close() {
    this.closed = true;
    try { this.socket.close(); } catch { /* already gone */ }
  }
}

/**
 * Turn a relay close reason into something worth showing a person.
 *
 * These arrive unauthenticated — the relay cannot seal a frame, so
 * everything it says it says in the clear. Worth rendering differently
 * from an error the house itself sent, which is why they are translated
 * in one place rather than printed raw.
 */
export function closeReasonText(reason) {
  switch (reason) {
    case 'HOUSEHOLD_OFFLINE':
      return 'Your Domovoi server is not connected right now.';
    case 'SUBSCRIPTION_INACTIVE':
      return 'Remote access is paused because the subscription is not active.';
    case 'QUOTA_EXCEEDED':
      return 'This month’s data allowance is used up.';
    case 'UNAUTHORIZED':
      return 'This device is not allowed to connect.';
    case 'PROTOCOL_ERROR':
      return 'The connection ended unexpectedly.';
    default:
      return reason || 'The connection closed.';
  }
}
