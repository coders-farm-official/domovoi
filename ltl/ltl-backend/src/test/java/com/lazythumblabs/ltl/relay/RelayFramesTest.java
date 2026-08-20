package com.lazythumblabs.ltl.relay;

import com.lazythumblabs.ltl.util.CrossImplementationVectors;
import org.junit.jupiter.api.DisplayName;
import org.junit.jupiter.api.Test;

import java.util.HexFormat;

import static org.junit.jupiter.api.Assertions.*;

/**
 * The outer frame codec, checked against frames the Python side actually
 * produced.
 *
 * <p>This is the seam where a one-byte disagreement would produce a
 * mysterious "the relay works but nothing gets through" bug, so it is
 * pinned to bytes rather than to behavior.
 */
class RelayFramesTest {

    @Test
    @DisplayName("decodes a LINK_DATA frame produced by the Python plugin")
    void decodesPythonLinkData() {
        byte[] raw = HexFormat.of().parseHex(CrossImplementationVectors.OUTER_LINK_DATA_HEX);
        RelayFrames.Outer frame = RelayFrames.decode(raw);

        assertEquals(RelayFrames.OP_LINK_DATA, frame.opcode());
        assertArrayEquals(new byte[]{0, 1, 2, 3, 4, 5, 6, 7, 8, 9, 10, 11, 12, 13, 14, 15},
                frame.linkId());
        assertEquals("sealed", new String(frame.payload()));
    }

    @Test
    @DisplayName("decodes a CONTROL frame produced by the Python plugin")
    void decodesPythonControl() {
        byte[] raw = HexFormat.of().parseHex(CrossImplementationVectors.OUTER_CONTROL_HEX);
        RelayFrames.Outer frame = RelayFrames.decode(raw);

        assertEquals(RelayFrames.OP_CONTROL, frame.opcode());
        assertArrayEquals(RelayFrames.ZERO_LINK_ID, frame.linkId());
        assertEquals("heartbeat", RelayFrames.parseJson(frame.payload()).path("t").asText());
    }

    @Test
    @DisplayName("encodes byte-identically to the Python implementation")
    void encodesLikePython() {
        byte[] linkId = new byte[16];
        for (int i = 0; i < 16; i++) {
            linkId[i] = (byte) i;
        }
        byte[] encoded = RelayFrames.encode(
                RelayFrames.OP_LINK_DATA, linkId, "sealed".getBytes());
        assertEquals(CrossImplementationVectors.OUTER_LINK_DATA_HEX,
                HexFormat.of().formatHex(encoded));
    }

    @Test
    @DisplayName("the header is exactly 18 bytes: version, opcode, link id")
    void headerLayout() {
        byte[] encoded = RelayFrames.encode(
                RelayFrames.OP_CONTROL, RelayFrames.ZERO_LINK_ID, new byte[0]);
        assertEquals(18, encoded.length);
        assertEquals(RelayFrames.VERSION, encoded[0]);
        assertEquals(RelayFrames.OP_CONTROL, encoded[1]);
    }

    @Test
    @DisplayName("round-trips an arbitrary payload without touching it")
    void roundTripsPayload() {
        byte[] payload = new byte[1024];
        for (int i = 0; i < payload.length; i++) {
            payload[i] = (byte) (i % 256);
        }
        byte[] linkId = RelayFrames.newLinkId();
        RelayFrames.Outer frame = RelayFrames.decode(
                RelayFrames.linkData(linkId, payload));
        assertArrayEquals(payload, frame.payload());
        assertArrayEquals(linkId, frame.linkId());
    }

    @Test
    @DisplayName("a future protocol version is refused, not guessed at")
    void rejectsUnknownVersion() {
        byte[] raw = RelayFrames.encode(
                RelayFrames.OP_CONTROL, RelayFrames.ZERO_LINK_ID, new byte[0]);
        raw[0] = 0x02;
        RelayFrames.FramingException error = assertThrows(
                RelayFrames.FramingException.class, () -> RelayFrames.decode(raw));
        assertTrue(error.getMessage().contains("version"));
    }

    @Test
    @DisplayName("unknown opcodes are refused in both directions")
    void rejectsUnknownOpcode() {
        byte[] raw = RelayFrames.encode(
                RelayFrames.OP_CONTROL, RelayFrames.ZERO_LINK_ID, new byte[0]);
        raw[1] = 0x55;
        assertThrows(RelayFrames.FramingException.class, () -> RelayFrames.decode(raw));
    }

    @Test
    @DisplayName("truncated frames are refused")
    void rejectsTruncatedFrames() {
        assertThrows(RelayFrames.FramingException.class,
                () -> RelayFrames.decode(new byte[]{1, 2, 3}));
        assertThrows(RelayFrames.FramingException.class, () -> RelayFrames.decode(null));
        assertThrows(RelayFrames.FramingException.class, () -> RelayFrames.decode(new byte[17]));
    }

    @Test
    @DisplayName("a wrong-length link id is refused at encode time")
    void rejectsBadLinkId() {
        assertThrows(RelayFrames.FramingException.class,
                () -> RelayFrames.encode(RelayFrames.OP_LINK_DATA, new byte[8], new byte[0]));
    }

    @Test
    @DisplayName("link ids are 16 random bytes and do not repeat")
    void linkIdsAreRandom() {
        byte[] first = RelayFrames.newLinkId();
        assertEquals(16, first.length);
        assertFalse(HexFormat.of().formatHex(first)
                .equals(HexFormat.of().formatHex(RelayFrames.newLinkId())));
    }

    @Test
    @DisplayName("non-object control payloads are refused")
    void rejectsNonObjectJson() {
        assertThrows(RelayFrames.FramingException.class,
                () -> RelayFrames.parseJson("[1,2,3]".getBytes()));
        assertThrows(RelayFrames.FramingException.class,
                () -> RelayFrames.parseJson("not json".getBytes()));
    }

    @Test
    @DisplayName("link open carries the device and country the household needs")
    void linkOpenPayload() {
        byte[] linkId = RelayFrames.newLinkId();
        RelayFrames.Outer frame = RelayFrames.decode(
                RelayFrames.linkOpen(linkId, "d_abc", "US"));
        assertEquals(RelayFrames.OP_LINK_OPEN, frame.opcode());
        var payload = RelayFrames.parseJson(frame.payload());
        assertEquals("d_abc", payload.path("device_id").asText());
        assertEquals("US", payload.path("ip_country").asText());
    }

    @Test
    @DisplayName("link close carries a code the household can act on")
    void linkClosePayload() {
        RelayFrames.Outer frame = RelayFrames.decode(
                RelayFrames.linkClose(RelayFrames.newLinkId(), "CLIENT_GONE", "bye"));
        assertEquals(RelayFrames.OP_LINK_CLOSE, frame.opcode());
        assertEquals("CLIENT_GONE",
                RelayFrames.parseJson(frame.payload()).path("code").asText());
    }
}
