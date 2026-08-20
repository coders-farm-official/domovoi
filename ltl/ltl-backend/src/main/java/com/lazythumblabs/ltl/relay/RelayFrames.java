package com.lazythumblabs.ltl.relay;

import com.fasterxml.jackson.databind.JsonNode;
import com.fasterxml.jackson.databind.ObjectMapper;
import com.fasterxml.jackson.databind.node.ObjectNode;

import java.nio.ByteBuffer;
import java.nio.charset.StandardCharsets;
import java.security.SecureRandom;

/**
 * The outer frame codec — the relay's entire view of the wire.
 *
 * <p>This class knows how to read eighteen bytes: a version, an opcode,
 * and a link id. Everything after those is {@code payload}, and the
 * payload is sealed with a key derived from a Diffie-Hellman the relay
 * does not take part in. There is deliberately no inner-frame parser
 * here — not because it would be hard, but because writing one would
 * mean the relay had acquired the ability to read household traffic,
 * which is the one thing this product promises it cannot do.
 *
 * <p>Layout, matching {@code framing.py} and
 * {@code ltl/docs/PROTOCOL.md} §2:
 *
 * <pre>
 *   offset  size  field
 *   0       1     version = 0x01
 *   1       1     opcode
 *   2       16    link_id
 *   18      ...   payload (opaque)
 * </pre>
 */
public final class RelayFrames {

    public static final byte VERSION = 0x01;

    public static final byte OP_LINK_OPEN = 0x01;
    public static final byte OP_LINK_DATA = 0x02;
    public static final byte OP_LINK_CLOSE = 0x03;
    public static final byte OP_CONTROL = 0x10;

    public static final int LINK_ID_LENGTH = 16;
    public static final int HEADER_LENGTH = 2 + LINK_ID_LENGTH;
    public static final byte[] ZERO_LINK_ID = new byte[LINK_ID_LENGTH];

    // Control verbs (PROTOCOL.md §6).
    public static final String C_CHALLENGE = "challenge";
    public static final String C_HELLO = "hello";
    public static final String C_HELLO_OK = "hello_ok";
    public static final String C_HEARTBEAT = "heartbeat";
    public static final String C_QUOTA = "quota";
    public static final String C_REVOKE = "revoke";
    public static final String C_DEVICE_PENDING = "device_pending";

    // Close reasons the RELAY originates. These travel in the clear —
    // the relay cannot seal a frame, so anything it needs to say it says
    // unauthenticated, and clients render them differently for exactly
    // that reason.
    public static final String ERR_QUOTA_EXCEEDED = "QUOTA_EXCEEDED";
    public static final String ERR_SUBSCRIPTION_INACTIVE = "SUBSCRIPTION_INACTIVE";
    public static final String ERR_HOUSEHOLD_OFFLINE = "HOUSEHOLD_OFFLINE";
    public static final String ERR_PROTOCOL = "PROTOCOL_ERROR";
    public static final String ERR_UNAUTHORIZED = "UNAUTHORIZED";

    private static final ObjectMapper MAPPER = new ObjectMapper();
    private static final SecureRandom RANDOM = new SecureRandom();

    private RelayFrames() {}

    /** A malformed frame. Always fatal to the connection. */
    public static class FramingException extends RuntimeException {
        public FramingException(String message) {
            super(message);
        }
    }

    /** A decoded outer frame. {@code payload} is opaque and stays that way. */
    public record Outer(byte opcode, byte[] linkId, byte[] payload) {

        public String linkKey() {
            return java.util.HexFormat.of().formatHex(linkId);
        }
    }

    public static byte[] newLinkId() {
        byte[] id = new byte[LINK_ID_LENGTH];
        RANDOM.nextBytes(id);
        return id;
    }

    // ── encode / decode ─────────────────────────────────────────────────

    public static byte[] encode(byte opcode, byte[] linkId, byte[] payload) {
        if (linkId.length != LINK_ID_LENGTH) {
            throw new FramingException("link id must be " + LINK_ID_LENGTH + " bytes");
        }
        byte[] body = payload == null ? new byte[0] : payload;
        ByteBuffer buffer = ByteBuffer.allocate(HEADER_LENGTH + body.length);
        buffer.put(VERSION);
        buffer.put(opcode);
        buffer.put(linkId);
        buffer.put(body);
        return buffer.array();
    }

    public static Outer decode(byte[] raw) {
        if (raw == null || raw.length < HEADER_LENGTH) {
            throw new FramingException("frame is shorter than its header");
        }
        if (raw[0] != VERSION) {
            // Versions are refused, never guessed at — PROTOCOL.md §9.
            throw new FramingException("unsupported protocol version " + (raw[0] & 0xFF));
        }
        byte opcode = raw[1];
        if (opcode != OP_LINK_OPEN && opcode != OP_LINK_DATA
                && opcode != OP_LINK_CLOSE && opcode != OP_CONTROL) {
            throw new FramingException("unknown opcode 0x" + Integer.toHexString(opcode & 0xFF));
        }
        byte[] linkId = new byte[LINK_ID_LENGTH];
        System.arraycopy(raw, 2, linkId, 0, LINK_ID_LENGTH);
        byte[] payload = new byte[raw.length - HEADER_LENGTH];
        System.arraycopy(raw, HEADER_LENGTH, payload, 0, payload.length);
        return new Outer(opcode, linkId, payload);
    }

    // ── JSON payloads (control frames and link open/close only) ─────────

    public static byte[] json(ObjectNode node) {
        try {
            return MAPPER.writeValueAsBytes(node);
        } catch (Exception e) {
            throw new FramingException("could not serialize control payload");
        }
    }

    public static ObjectNode object() {
        return MAPPER.createObjectNode();
    }

    public static JsonNode parseJson(byte[] payload) {
        try {
            JsonNode node = MAPPER.readTree(new String(payload, StandardCharsets.UTF_8));
            if (node == null || !node.isObject()) {
                throw new FramingException("control payload must be a JSON object");
            }
            return node;
        } catch (FramingException e) {
            throw e;
        } catch (Exception e) {
            throw new FramingException("control payload is not valid JSON");
        }
    }

    // ── convenience builders ────────────────────────────────────────────

    public static byte[] control(ObjectNode payload) {
        return encode(OP_CONTROL, ZERO_LINK_ID, json(payload));
    }

    public static byte[] controlVerb(String verb) {
        return control(object().put("t", verb));
    }

    public static byte[] linkOpen(byte[] linkId, String deviceUid, String ipCountry) {
        ObjectNode payload = object().put("device_id", deviceUid);
        if (ipCountry != null) {
            payload.put("ip_country", ipCountry);
        }
        return encode(OP_LINK_OPEN, linkId, json(payload));
    }

    public static byte[] linkClose(byte[] linkId, String code, String reason) {
        ObjectNode payload = object().put("code", code).put("reason", reason == null ? "" : reason);
        return encode(OP_LINK_CLOSE, linkId, json(payload));
    }

    public static byte[] linkData(byte[] linkId, byte[] sealedPayload) {
        return encode(OP_LINK_DATA, linkId, sealedPayload);
    }
}
