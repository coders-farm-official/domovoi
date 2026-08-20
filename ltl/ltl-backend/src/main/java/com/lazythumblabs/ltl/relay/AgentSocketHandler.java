package com.lazythumblabs.ltl.relay;

import com.fasterxml.jackson.databind.JsonNode;
import com.fasterxml.jackson.databind.node.ObjectNode;
import com.lazythumblabs.ltl.entities.Household;
import com.lazythumblabs.ltl.services.DeviceService;
import com.lazythumblabs.ltl.services.Entitlement;
import com.lazythumblabs.ltl.services.EntitlementService;
import com.lazythumblabs.ltl.services.HouseholdService;
import com.lazythumblabs.ltl.services.UsageService;
import com.lazythumblabs.ltl.util.CryptoUtil;
import org.slf4j.Logger;
import org.slf4j.LoggerFactory;
import org.springframework.beans.factory.annotation.Autowired;
import org.springframework.stereotype.Component;
import org.springframework.web.socket.BinaryMessage;
import org.springframework.web.socket.CloseStatus;
import org.springframework.web.socket.WebSocketSession;
import org.springframework.web.socket.handler.BinaryWebSocketHandler;

import java.security.SecureRandom;
import java.util.Optional;

/**
 * The household side of the relay: {@code WSS /relay/v1/agent}.
 *
 * <p>Everything this handler does with a {@code LINK_DATA} payload is
 * copy it to the other socket. It does not parse one, cannot decrypt
 * one, and has no key that would let it — see
 * {@code ltl/docs/PROTOCOL.md} for why the split is drawn there.
 */
@Component
public class AgentSocketHandler extends BinaryWebSocketHandler {

    private static final Logger logger = LoggerFactory.getLogger(AgentSocketHandler.class);
    private static final SecureRandom RANDOM = new SecureRandom();
    private static final String ATTR_AGENT = "ltl.agent";

    @Autowired
    private RelayRegistry registry;

    @Autowired
    private HouseholdService householdService;

    @Autowired
    private EntitlementService entitlementService;

    @Autowired
    private DeviceService deviceService;

    @Autowired
    private UsageService usageService;

    @Override
    public void afterConnectionEstablished(WebSocketSession session) throws Exception {
        String token = (String) session.getAttributes()
                .get(RelayHandshakeInterceptor.ATTR_AUTHORIZATION);

        Optional<Household> maybeHousehold = householdService.findByRelayToken(token);
        if (maybeHousehold.isEmpty()) {
            // The token alone is not enough to connect, but a bad one is
            // enough to be turned away — no point issuing a challenge to
            // a caller we already cannot place.
            session.close(new CloseStatus(4401, RelayFrames.ERR_UNAUTHORIZED));
            return;
        }
        Household household = maybeHousehold.get();

        Entitlement entitlement = entitlementService.resolve(household);
        if (!entitlement.active()) {
            session.close(new CloseStatus(4403, entitlement.reason()));
            return;
        }

        byte[] challenge = new byte[32];
        RANDOM.nextBytes(challenge);

        AgentConnection agent = new AgentConnection(
                session, household.getId(), household.getHouseholdUid(), challenge);
        session.getAttributes().put(ATTR_AGENT, agent);

        // The token got them this far. The signature is what proves they
        // hold the household's private key, so a leaked token alone is
        // useless — PROTOCOL.md §8.
        ObjectNode payload = RelayFrames.object()
                .put("t", RelayFrames.C_CHALLENGE)
                .put("nonce", CryptoUtil.encodeBase64Url(challenge));
        session.sendMessage(new BinaryMessage(RelayFrames.control(payload)));
    }

    @Override
    protected void handleBinaryMessage(WebSocketSession session, BinaryMessage message) {
        AgentConnection agent = (AgentConnection) session.getAttributes().get(ATTR_AGENT);
        if (agent == null) {
            closeQuietly(session, 4400, RelayFrames.ERR_PROTOCOL);
            return;
        }

        RelayFrames.Outer frame;
        try {
            frame = RelayFrames.decode(message.getPayload().array());
        } catch (RelayFrames.FramingException e) {
            logger.info("household {} sent an unusable frame: {}",
                    agent.getHouseholdUid(), e.getMessage());
            closeQuietly(session, 4400, RelayFrames.ERR_PROTOCOL);
            return;
        }

        if (!agent.isAuthenticated()) {
            handleHello(agent, frame);
            return;
        }

        switch (frame.opcode()) {
            case RelayFrames.OP_CONTROL -> handleControl(agent, frame);
            case RelayFrames.OP_LINK_DATA -> forwardToClient(agent, frame);
            case RelayFrames.OP_LINK_CLOSE -> closeLink(agent, frame);
            default -> logger.debug("ignoring opcode {} from an agent", frame.opcode());
        }
    }

    // ── authentication ──────────────────────────────────────────────────

    private void handleHello(AgentConnection agent, RelayFrames.Outer frame) {
        if (frame.opcode() != RelayFrames.OP_CONTROL) {
            closeQuietly(agent.getSession(), 4400, RelayFrames.ERR_PROTOCOL);
            return;
        }
        JsonNode hello;
        try {
            hello = RelayFrames.parseJson(frame.payload());
        } catch (RelayFrames.FramingException e) {
            closeQuietly(agent.getSession(), 4400, RelayFrames.ERR_PROTOCOL);
            return;
        }
        if (!RelayFrames.C_HELLO.equals(hello.path("t").asText())) {
            closeQuietly(agent.getSession(), 4400, RelayFrames.ERR_PROTOCOL);
            return;
        }

        Optional<Household> maybeHousehold =
                householdService.findOwnedByUid(agent.getHouseholdUid());
        if (maybeHousehold.isEmpty()) {
            closeQuietly(agent.getSession(), 4401, RelayFrames.ERR_UNAUTHORIZED);
            return;
        }
        Household household = maybeHousehold.get();

        boolean signatureOk = CryptoUtil.verifyChallenge(
                household.getSigPublicKey(), agent.getChallenge(),
                household.getHouseholdUid(), hello.path("sig").asText(""));
        if (!signatureOk) {
            logger.warn("household {} failed the challenge signature",
                    agent.getHouseholdUid());
            closeQuietly(agent.getSession(), 4401, RelayFrames.ERR_UNAUTHORIZED);
            return;
        }

        agent.setAgentVersion(hello.path("agent_version").asText(null));
        agent.setAuthenticated(true);
        registry.register(agent);
        householdService.markOnline(household, agent.getAgentVersion());

        Entitlement entitlement = entitlementService.resolve(household);
        ObjectNode ok = RelayFrames.object()
                .put("t", RelayFrames.C_HELLO_OK)
                .put("server_time", java.time.Instant.now().toString())
                .put("heartbeat_sec", 30)
                .put("plan", entitlement.planCode())
                .put("used_bytes", entitlement.bytesUsed())
                .put("limit_bytes", entitlement.bytesLimit());
        if (entitlement.periodEnd() != null) {
            ok.put("period_end", entitlement.periodEnd().toString());
        }
        agent.send(RelayFrames.control(ok));

        logger.info("household {} is online (agent {})",
                agent.getHouseholdUid(), agent.getAgentVersion());
    }

    // ── steady state ────────────────────────────────────────────────────

    private void handleControl(AgentConnection agent, RelayFrames.Outer frame) {
        JsonNode message;
        try {
            message = RelayFrames.parseJson(frame.payload());
        } catch (RelayFrames.FramingException e) {
            return;
        }
        String verb = message.path("t").asText();
        if (RelayFrames.C_HEARTBEAT.equals(verb)) {
            return;
        }
        if ("device_state".equals(verb)) {
            // A report, not an instruction: the household has already
            // approved or revoked on its own dashboard, and this only
            // stops the LTL web app from showing a stale state.
            householdService.findOwnedByUid(agent.getHouseholdUid()).ifPresent(household ->
                    deviceService.applyHouseholdState(
                            household,
                            message.path("device_id").asText(""),
                            message.path("status").asText("")));
        }
    }

    /**
     * Copy a sealed payload from the agent to the right client link.
     *
     * <p>The bytes are not inspected, only counted — metering is the
     * whole of what the relay learns from traffic it carries.
     */
    private void forwardToClient(AgentConnection agent, RelayFrames.Outer frame) {
        ClientConnection client = agent.getLink(frame.linkKey());
        if (client == null) {
            return;
        }
        usageService.record(agent.getHouseholdId(), frame.payload().length);
        if (!client.send(frame.payload())) {
            agent.removeLink(frame.linkKey());
            client.close("relay send failed");
        }
    }

    private void closeLink(AgentConnection agent, RelayFrames.Outer frame) {
        ClientConnection client = agent.removeLink(frame.linkKey());
        if (client != null) {
            client.close("closed by the household");
        }
    }

    @Override
    public void afterConnectionClosed(WebSocketSession session, CloseStatus status) {
        AgentConnection agent = (AgentConnection) session.getAttributes().remove(ATTR_AGENT);
        if (agent == null) {
            return;
        }
        registry.unregister(agent);
        householdService.markOffline(agent.getHouseholdId());
        // Every client of a household that just went away is now talking
        // to nothing. Closing them explicitly gives each device a clear
        // reason instead of a socket that silently stops answering.
        for (ClientConnection client : agent.links()) {
            client.close(RelayFrames.ERR_HOUSEHOLD_OFFLINE);
        }
        logger.info("household {} disconnected ({})", agent.getHouseholdUid(), status.getCode());
    }

    private void closeQuietly(WebSocketSession session, int code, String reason) {
        try {
            session.close(new CloseStatus(code, reason));
        } catch (Exception e) {
            logger.debug("close failed: {}", e.getMessage());
        }
    }
}
