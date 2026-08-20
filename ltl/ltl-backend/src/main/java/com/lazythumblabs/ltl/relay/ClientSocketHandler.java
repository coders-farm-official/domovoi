package com.lazythumblabs.ltl.relay;

import com.lazythumblabs.ltl.entities.Device;
import com.lazythumblabs.ltl.entities.Household;
import com.lazythumblabs.ltl.services.DeviceService;
import com.lazythumblabs.ltl.services.Entitlement;
import com.lazythumblabs.ltl.services.EntitlementService;
import com.lazythumblabs.ltl.services.HouseholdService;
import com.lazythumblabs.ltl.services.UsageService;
import org.slf4j.Logger;
import org.slf4j.LoggerFactory;
import org.springframework.beans.factory.annotation.Autowired;
import org.springframework.stereotype.Component;
import org.springframework.web.socket.BinaryMessage;
import org.springframework.web.socket.CloseStatus;
import org.springframework.web.socket.WebSocketSession;
import org.springframework.web.socket.handler.BinaryWebSocketHandler;

import java.util.Optional;

/**
 * The device side of the relay: {@code WSS /relay/v1/client}.
 *
 * <p>What this handler checks is deliberately narrow: that the household
 * exists, that its subscription is live, that it has an agent connected,
 * and that the device is not revoked <em>here</em>. It does not, and
 * cannot, decide whether the device may enter the house — that is the
 * household's decision, made against its own database during a handshake
 * this relay cannot read.
 *
 * <p>So a device that gets past every check in this file still reaches
 * nothing unless a human approved it on the Domovoi dashboard. That is
 * the property the whole design exists to preserve.
 */
@Component
public class ClientSocketHandler extends BinaryWebSocketHandler {

    private static final Logger logger = LoggerFactory.getLogger(ClientSocketHandler.class);
    private static final String ATTR_CLIENT = "ltl.client";

    @Autowired
    private RelayRegistry registry;

    @Autowired
    private HouseholdService householdService;

    @Autowired
    private DeviceService deviceService;

    @Autowired
    private EntitlementService entitlementService;

    @Autowired
    private UsageService usageService;

    @Override
    public void afterConnectionEstablished(WebSocketSession session) throws Exception {
        String householdUid = (String) session.getAttributes()
                .get(RelayHandshakeInterceptor.ATTR_HOUSEHOLD);
        String deviceUid = (String) session.getAttributes()
                .get(RelayHandshakeInterceptor.ATTR_DEVICE);
        String country = (String) session.getAttributes()
                .get(RelayHandshakeInterceptor.ATTR_COUNTRY);

        if (householdUid == null || deviceUid == null) {
            session.close(new CloseStatus(4400, RelayFrames.ERR_PROTOCOL));
            return;
        }

        Optional<Household> maybeHousehold = householdService.findOwnedByUid(householdUid);
        if (maybeHousehold.isEmpty()) {
            session.close(new CloseStatus(4404, RelayFrames.ERR_PROTOCOL));
            return;
        }
        Household household = maybeHousehold.get();

        Optional<Device> maybeDevice = deviceService.findByUid(deviceUid);
        if (maybeDevice.isEmpty()
                || !maybeDevice.get().getHousehold().getId().equals(household.getId())
                || maybeDevice.get().getRevokedAt() != null) {
            session.close(new CloseStatus(4403, RelayFrames.ERR_UNAUTHORIZED));
            return;
        }

        Entitlement entitlement = entitlementService.resolve(household);
        if (!entitlement.active()) {
            session.close(new CloseStatus(4402, entitlement.reason()));
            return;
        }
        if (!entitlement.withinQuota()) {
            session.close(new CloseStatus(4402, RelayFrames.ERR_QUOTA_EXCEEDED));
            return;
        }

        Optional<AgentConnection> maybeAgent = registry.findReady(householdUid);
        if (maybeAgent.isEmpty()) {
            session.close(new CloseStatus(4404, RelayFrames.ERR_HOUSEHOLD_OFFLINE));
            return;
        }
        AgentConnection agent = maybeAgent.get();

        ClientConnection client = new ClientConnection(
                session, RelayFrames.newLinkId(), deviceUid, country,
                household.getId(), householdUid);
        session.getAttributes().put(ATTR_CLIENT, client);
        agent.addLink(client);

        if (!agent.send(RelayFrames.linkOpen(client.getLinkId(), deviceUid, country))) {
            agent.removeLink(client.linkKey());
            session.close(new CloseStatus(4404, RelayFrames.ERR_HOUSEHOLD_OFFLINE));
            return;
        }

        deviceService.markSeen(deviceUid, country);
        logger.debug("device {} opened a link to household {}", deviceUid, householdUid);
    }

    /**
     * Wrap a sealed payload and hand it to the household's agent.
     *
     * <p>The payload is opaque. The only thing learned from it here is
     * its length, which is what the customer is billed for.
     */
    @Override
    protected void handleBinaryMessage(WebSocketSession session, BinaryMessage message) {
        ClientConnection client = (ClientConnection) session.getAttributes().get(ATTR_CLIENT);
        if (client == null) {
            return;
        }
        Optional<AgentConnection> maybeAgent = registry.findReady(client.getHouseholdUid());
        if (maybeAgent.isEmpty()) {
            client.close(RelayFrames.ERR_HOUSEHOLD_OFFLINE);
            return;
        }
        byte[] payload = message.getPayload().array();
        usageService.record(client.getHouseholdId(), payload.length);
        if (!maybeAgent.get().send(RelayFrames.linkData(client.getLinkId(), payload))) {
            client.close(RelayFrames.ERR_HOUSEHOLD_OFFLINE);
        }
    }

    @Override
    public void afterConnectionClosed(WebSocketSession session, CloseStatus status) {
        ClientConnection client = (ClientConnection) session.getAttributes().remove(ATTR_CLIENT);
        if (client == null) {
            return;
        }
        registry.findReady(client.getHouseholdUid()).ifPresent(agent -> {
            agent.removeLink(client.linkKey());
            // Tell the household the device went away, so it can release
            // whatever the link was holding open rather than waiting for
            // an idle timeout.
            agent.send(RelayFrames.linkClose(client.getLinkId(), "CLIENT_GONE",
                    "the device disconnected"));
        });
        logger.debug("device {} closed its link ({})", client.getDeviceUid(), status.getCode());
    }
}
