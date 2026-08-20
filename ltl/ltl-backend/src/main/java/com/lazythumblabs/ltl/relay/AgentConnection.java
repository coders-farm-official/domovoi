package com.lazythumblabs.ltl.relay;

import org.slf4j.Logger;
import org.slf4j.LoggerFactory;
import org.springframework.web.socket.BinaryMessage;
import org.springframework.web.socket.WebSocketSession;

import java.io.IOException;
import java.nio.ByteBuffer;
import java.util.Map;
import java.util.concurrent.ConcurrentHashMap;

/**
 * One household's held-open socket, and the client links multiplexed
 * over it.
 *
 * <p>Sends are serialized on this object. Spring's WebSocket sessions
 * are explicitly not safe for concurrent {@code sendMessage} calls, and
 * several client links write to one agent socket at the same time — an
 * interleaved partial frame would desynchronize the connection in a way
 * neither end could recover from.
 */
public class AgentConnection {

    private static final Logger logger = LoggerFactory.getLogger(AgentConnection.class);

    private final WebSocketSession session;
    private final Long householdId;
    private final String householdUid;
    private final byte[] challenge;
    private final Object sendLock = new Object();

    private volatile boolean authenticated;
    private volatile String agentVersion;

    /** linkKey (hex of the link id) → the client on the other side. */
    private final Map<String, ClientConnection> links = new ConcurrentHashMap<>();

    public AgentConnection(WebSocketSession session, Long householdId, String householdUid,
                           byte[] challenge) {
        this.session = session;
        this.householdId = householdId;
        this.householdUid = householdUid;
        this.challenge = challenge;
    }

    public WebSocketSession getSession() { return session; }
    public Long getHouseholdId() { return householdId; }
    public String getHouseholdUid() { return householdUid; }
    public byte[] getChallenge() { return challenge; }

    public boolean isAuthenticated() { return authenticated; }
    public void setAuthenticated(boolean authenticated) { this.authenticated = authenticated; }

    public String getAgentVersion() { return agentVersion; }
    public void setAgentVersion(String agentVersion) { this.agentVersion = agentVersion; }

    public int linkCount() { return links.size(); }

    // ── links ───────────────────────────────────────────────────────────

    public void addLink(ClientConnection client) {
        links.put(client.linkKey(), client);
    }

    public ClientConnection removeLink(String linkKey) {
        return links.remove(linkKey);
    }

    public ClientConnection getLink(String linkKey) {
        return links.get(linkKey);
    }

    public Iterable<ClientConnection> links() {
        return links.values();
    }

    // ── sending ─────────────────────────────────────────────────────────

    /**
     * Send one frame. Returns false if the socket has gone; callers use
     * that to tear the link down rather than to retry, because a frame
     * that did not arrive cannot be resent without breaking the
     * receiver's counter ordering.
     */
    public boolean send(byte[] frame) {
        synchronized (sendLock) {
            if (!session.isOpen()) {
                return false;
            }
            try {
                session.sendMessage(new BinaryMessage(ByteBuffer.wrap(frame)));
                return true;
            } catch (IOException e) {
                logger.debug("send to household {} failed: {}", householdUid, e.getMessage());
                return false;
            }
        }
    }

    public void close(String reason) {
        try {
            session.close(new org.springframework.web.socket.CloseStatus(1000, reason));
        } catch (Exception e) {
            logger.debug("closing agent socket for {} failed: {}", householdUid, e.getMessage());
        }
    }
}
