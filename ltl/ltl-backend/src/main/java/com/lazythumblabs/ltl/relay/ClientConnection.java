package com.lazythumblabs.ltl.relay;

import org.slf4j.Logger;
import org.slf4j.LoggerFactory;
import org.springframework.web.socket.BinaryMessage;
import org.springframework.web.socket.CloseStatus;
import org.springframework.web.socket.WebSocketSession;

import java.io.IOException;
import java.nio.ByteBuffer;
import java.util.HexFormat;

/** One remote device's socket, bound to a link id on its household's agent. */
public class ClientConnection {

    private static final Logger logger = LoggerFactory.getLogger(ClientConnection.class);

    private final WebSocketSession session;
    private final byte[] linkId;
    private final String deviceUid;
    private final String ipCountry;
    private final Long householdId;
    private final String householdUid;
    private final Object sendLock = new Object();

    public ClientConnection(WebSocketSession session, byte[] linkId, String deviceUid,
                            String ipCountry, Long householdId, String householdUid) {
        this.session = session;
        this.linkId = linkId;
        this.deviceUid = deviceUid;
        this.ipCountry = ipCountry;
        this.householdId = householdId;
        this.householdUid = householdUid;
    }

    public WebSocketSession getSession() { return session; }
    public byte[] getLinkId() { return linkId; }
    public String linkKey() { return HexFormat.of().formatHex(linkId); }
    public String getDeviceUid() { return deviceUid; }
    public String getIpCountry() { return ipCountry; }
    public Long getHouseholdId() { return householdId; }
    public String getHouseholdUid() { return householdUid; }

    /**
     * Deliver a sealed payload to the device.
     *
     * <p>The outer header is stripped on the way out: a client socket is
     * one link, so it needs no link id, and sending one would tell the
     * device something only the relay needs to know.
     */
    public boolean send(byte[] sealedPayload) {
        synchronized (sendLock) {
            if (!session.isOpen()) {
                return false;
            }
            try {
                session.sendMessage(new BinaryMessage(ByteBuffer.wrap(sealedPayload)));
                return true;
            } catch (IOException e) {
                logger.debug("send to device {} failed: {}", deviceUid, e.getMessage());
                return false;
            }
        }
    }

    /**
     * Close with a reason the client can render.
     *
     * <p>Sent as a close status rather than a frame because the relay
     * cannot seal anything: everything it says, it says unauthenticated,
     * and clients treat these differently from sealed errors for exactly
     * that reason.
     */
    public void close(String reason) {
        try {
            session.close(new CloseStatus(4000, reason));
        } catch (Exception e) {
            logger.debug("closing client socket {} failed: {}", deviceUid, e.getMessage());
        }
    }
}
