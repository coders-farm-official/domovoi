/* The browser half of `ltl-remote/v1` — see ltl/docs/PROTOCOL.md.
 *
 * This is the mirror image of
 * plugin-ltl-remote/domovoi_plugin_ltl_remote/crypto.py, and every
 * constant, label and byte order below has to match it exactly. When one
 * side changes, both change, and the vectors in the plugin's test suite
 * are what proves they still agree.
 *
 * Four primitives, all native to WebCrypto:
 *
 *     ECDH P-256  ·  HKDF-SHA256  ·  AES-256-GCM  ·  HMAC-SHA256
 *
 * That choice is the reason this file has no dependencies and this app
 * has no build step. P-256 rather than X25519 because P-256 has been in
 * every shipping browser for a decade, and a key agreement that fails on
 * a two-year-old phone is not a security feature.
 *
 * The device's private key is generated non-extractable and stored as a
 * CryptoKey in IndexedDB. Page script — including this file — cannot
 * read it out; it can only ask the browser to use it. That does not
 * protect against someone holding your unlocked laptop, and
 * ltl/docs/SECURITY.md says so rather than implying otherwise.
 */

const PROTOCOL_VERSION = 1;
const LABEL = 'ltl-remote/v1';

const INFO_C2H = 'ltl-remote/v1 c2h';
const INFO_H2C = 'ltl-remote/v1 h2c';
const INFO_CONFIRM = 'ltl-remote/v1 confirm';

const PREFIX_C2H = 'C2H_';
const PREFIX_H2C = 'H2C_';

const NONCE_LENGTH = 16;
const POINT_LENGTH = 65;

const ECDH_PARAMS = { name: 'ECDH', namedCurve: 'P-256' };

/* ── bytes ───────────────────────────────────────────────────────────── */

const utf8 = (text) => new TextEncoder().encode(text);

const concat = (...parts) => {
  const total = parts.reduce((sum, part) => sum + part.length, 0);
  const out = new Uint8Array(total);
  let offset = 0;
  for (const part of parts) {
    out.set(part, offset);
    offset += part.length;
  }
  return out;
};

export const b64u = (bytes) => {
  let binary = '';
  for (const byte of new Uint8Array(bytes)) binary += String.fromCharCode(byte);
  return btoa(binary).replace(/\+/g, '-').replace(/\//g, '_').replace(/=+$/, '');
};

export const unb64u = (text) => {
  const padded = text.replace(/-/g, '+').replace(/_/g, '/')
    + '='.repeat((4 - (text.length % 4)) % 4);
  const binary = atob(padded);
  const out = new Uint8Array(binary.length);
  for (let i = 0; i < binary.length; i++) out[i] = binary.charCodeAt(i);
  return out;
};

/* Constant-time comparison. `===` on hex strings leaks position through
 * timing; it almost certainly does not matter across a network with this
 * much jitter, but a confirmation tag is exactly the kind of thing that
 * should not need the word "probably". */
const equalBytes = (a, b) => {
  if (a.length !== b.length) return false;
  let diff = 0;
  for (let i = 0; i < a.length; i++) diff |= a[i] ^ b[i];
  return diff === 0;
};

/* ── keys ────────────────────────────────────────────────────────────── */

export async function generateDeviceKey() {
  // extractable: false — the private half can be used but never read,
  // not even by this file.
  return crypto.subtle.generateKey(ECDH_PARAMS, false, ['deriveBits']);
}

export async function exportPublicKey(keyPair) {
  return new Uint8Array(await crypto.subtle.exportKey('raw', keyPair.publicKey));
}

export async function importPublicKey(raw) {
  if (raw.length !== POINT_LENGTH || raw[0] !== 0x04) {
    throw new Error('public key must be a 65-byte uncompressed SEC1 point');
  }
  // importKey rejects a point that is not on the curve, which is the
  // invalid-curve check this needs.
  return crypto.subtle.importKey('raw', raw, ECDH_PARAMS, false, []);
}

const deriveShared = async (privateKey, publicKey) =>
  new Uint8Array(await crypto.subtle.deriveBits(
    { name: 'ECDH', public: publicKey }, privateKey, 256));

/* ── the key schedule (PROTOCOL.md §3.3) ─────────────────────────────── */

const sha256 = async (bytes) =>
  new Uint8Array(await crypto.subtle.digest('SHA-256', bytes));

async function transcriptHash(parts) {
  return sha256(concat(
    utf8(LABEL),
    parts.devicePub, parts.ephClientPub, parts.nonceClient,
    parts.householdPub, parts.ephServerPub, parts.nonceServer,
  ));
}

/* RFC 5869 extract-then-expand in one call, which is exactly what
 * cryptography.hazmat's HKDF(salt, info) does on the Python side. */
async function hkdf(salt, ikm, info) {
  const key = await crypto.subtle.importKey('raw', ikm, 'HKDF', false, ['deriveBits']);
  return new Uint8Array(await crypto.subtle.deriveBits(
    { name: 'HKDF', hash: 'SHA-256', salt, info: utf8(info) }, key, 256));
}

async function hmac(key, message) {
  const imported = await crypto.subtle.importKey(
    'raw', key, { name: 'HMAC', hash: 'SHA-256' }, false, ['sign']);
  return new Uint8Array(await crypto.subtle.sign('HMAC', imported, utf8(message)));
}

/* ── fingerprints (PROTOCOL.md §7) ───────────────────────────────────── */

export async function fingerprint(dhPublicRaw, sigPublicRaw) {
  const digest = await sha256(concat(utf8('ltl-remote/v1 fp'), dhPublicRaw, sigPublicRaw));
  const hex = Array.from(digest.slice(0, 16))
    .map((byte) => byte.toString(16).padStart(2, '0'))
    .join('')
    .toUpperCase();
  return hex.match(/.{4}/g).join(' ');
}

/* ── the sealed layer (PROTOCOL.md §4) ───────────────────────────────── */

class SealedLink {
  constructor(sendKey, recvKey, sendPrefix, recvPrefix) {
    this.sendKey = sendKey;
    this.recvKey = recvKey;
    this.sendPrefix = utf8(sendPrefix);
    this.recvPrefix = utf8(recvPrefix);
    this.sendCounter = 0n;
    this.lastRecvCounter = -1n;
  }

  static counterBytes(counter) {
    const out = new Uint8Array(8);
    let value = counter;
    for (let i = 7; i >= 0; i--) {
      out[i] = Number(value & 0xffn);
      value >>= 8n;
    }
    return out;
  }

  nonce(prefix, counter) { return concat(prefix, SealedLink.counterBytes(counter)); }

  aad(prefix, counter) {
    return concat(new Uint8Array([PROTOCOL_VERSION]), prefix, SealedLink.counterBytes(counter));
  }

  async seal(plaintext) {
    const counter = this.sendCounter;
    this.sendCounter += 1n;
    const ciphertext = new Uint8Array(await crypto.subtle.encrypt(
      {
        name: 'AES-GCM',
        iv: this.nonce(this.sendPrefix, counter),
        additionalData: this.aad(this.sendPrefix, counter),
        tagLength: 128,
      },
      this.sendKey,
      plaintext,
    ));
    return concat(SealedLink.counterBytes(counter), ciphertext);
  }

  async open(frame) {
    if (frame.length < 8 + 16) throw new Error('sealed frame is too short to hold a tag');
    let counter = 0n;
    for (let i = 0; i < 8; i++) counter = (counter << 8n) | BigInt(frame[i]);
    // Strictly increasing: replay and reordering fail closed rather than
    // being quietly tolerated.
    if (counter <= this.lastRecvCounter) throw new Error('replayed or reordered frame');

    let plaintext;
    try {
      plaintext = new Uint8Array(await crypto.subtle.decrypt(
        {
          name: 'AES-GCM',
          iv: this.nonce(this.recvPrefix, counter),
          additionalData: this.aad(this.recvPrefix, counter),
          tagLength: 128,
        },
        this.recvKey,
        frame.slice(8),
      ));
    } catch {
      throw new Error('frame failed authentication');
    }
    // Advance only after a successful open, so one forged frame with a
    // huge counter cannot burn the window for legitimate traffic.
    this.lastRecvCounter = counter;
    return plaintext;
  }
}

/* ── the client's side of the handshake ──────────────────────────────── */

export class ClientHandshake {
  /**
   * @param deviceKey    the device's non-extractable ECDH key pair
   * @param householdDh  the household's DH public key, 65 raw bytes
   * @param deviceId     the id LTL assigned this device
   */
  constructor(deviceKey, householdDh, deviceId) {
    this.deviceKey = deviceKey;
    this.householdDhRaw = householdDh;
    this.deviceId = deviceId;
    this.nonce = crypto.getRandomValues(new Uint8Array(NONCE_LENGTH));
  }

  async hello() {
    this.ephemeral = await crypto.subtle.generateKey(ECDH_PARAMS, false, ['deriveBits']);
    this.devicePub = await exportPublicKey(this.deviceKey);
    this.ephPub = await exportPublicKey(this.ephemeral);
    return {
      t: 'client_hello',
      v: PROTOCOL_VERSION,
      device_id: this.deviceId,
      device_pub: b64u(this.devicePub),
      eph_pub: b64u(this.ephPub),
      nonce_c: b64u(this.nonce),
    };
  }

  /**
   * Complete the handshake against the server's reply.
   *
   * Throws if the confirmation tag does not verify — which means either
   * the household key we pinned is wrong or somebody is sitting in the
   * middle. Both mean: do not proceed.
   */
  async finish(homeHello) {
    if (homeHello.t !== 'home_hello') throw new Error('expected home_hello');
    if (homeHello.v !== PROTOCOL_VERSION) throw new Error('unsupported protocol version');

    const ephServerRaw = unb64u(homeHello.eph_pub);
    const nonceServer = unb64u(homeHello.nonce_s);
    if (nonceServer.length !== NONCE_LENGTH) throw new Error('bad server nonce length');

    const ephServer = await importPublicKey(ephServerRaw);
    const householdDh = await importPublicKey(this.householdDhRaw);

    const dh1 = await deriveShared(this.ephemeral.privateKey, ephServer);      // forward secrecy
    const dh2 = await deriveShared(this.ephemeral.privateKey, householdDh);    // authenticates the house
    const dh3 = await deriveShared(this.deviceKey.privateKey, ephServer);      // authenticates us

    const transcript = await transcriptHash({
      devicePub: this.devicePub,
      ephClientPub: this.ephPub,
      nonceClient: this.nonce,
      householdPub: this.householdDhRaw,
      ephServerPub: ephServerRaw,
      nonceServer,
    });

    const ikm = concat(dh1, dh2, dh3);
    const rawC2H = await hkdf(transcript, ikm, INFO_C2H);
    const rawH2C = await hkdf(transcript, ikm, INFO_H2C);
    const confirmKey = await hkdf(transcript, ikm, INFO_CONFIRM);

    const expected = await hmac(confirmKey, 'home');
    if (!equalBytes(unb64u(homeHello.confirm), expected)) {
      throw new Error('the house did not prove it holds the key we pinned');
    }

    const usage = ['encrypt', 'decrypt'];
    const sendKey = await crypto.subtle.importKey('raw', rawC2H, 'AES-GCM', false, usage);
    const recvKey = await crypto.subtle.importKey('raw', rawH2C, 'AES-GCM', false, usage);

    return {
      confirm: {
        t: 'client_confirm',
        v: PROTOCOL_VERSION,
        confirm: b64u(await hmac(confirmKey, 'client')),
      },
      link: new SealedLink(sendKey, recvKey, PREFIX_C2H, PREFIX_H2C),
    };
  }
}

export { SealedLink, equalBytes, concat, utf8 };
