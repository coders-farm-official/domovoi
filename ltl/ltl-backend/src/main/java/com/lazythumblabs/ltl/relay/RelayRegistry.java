package com.lazythumblabs.ltl.relay;

import org.slf4j.Logger;
import org.slf4j.LoggerFactory;
import org.springframework.stereotype.Component;

import java.util.Collection;
import java.util.Map;
import java.util.Optional;
import java.util.concurrent.ConcurrentHashMap;

/**
 * Which households currently have an agent connected, and where.
 *
 * <p>In-process by design. A second relay instance would need this in
 * Redis or a routing layer that pins a household to a node — worth doing
 * when there is a second instance, and not before: a distributed
 * registry serving one process is pure cost.
 */
@Component
public class RelayRegistry {

    private static final Logger logger = LoggerFactory.getLogger(RelayRegistry.class);

    private final Map<String, AgentConnection> agents = new ConcurrentHashMap<>();

    /**
     * Register an agent, displacing any earlier one for the same
     * household.
     *
     * <p>Last connection wins: a server that restarted without a clean
     * close would otherwise be locked out by its own stale socket until
     * a heartbeat timeout, which is exactly when a customer is most
     * likely to be watching.
     */
    public void register(AgentConnection agent) {
        AgentConnection previous = agents.put(agent.getHouseholdUid(), agent);
        if (previous != null && previous != agent) {
            logger.info("household {} reconnected; dropping the previous agent socket",
                    agent.getHouseholdUid());
            previous.close("replaced by a newer connection");
        }
    }

    /** Remove an agent, but only if it is still the registered one. */
    public void unregister(AgentConnection agent) {
        agents.remove(agent.getHouseholdUid(), agent);
    }

    public Optional<AgentConnection> find(String householdUid) {
        return Optional.ofNullable(agents.get(householdUid));
    }

    /** Only an authenticated agent can carry client traffic. */
    public Optional<AgentConnection> findReady(String householdUid) {
        return find(householdUid).filter(AgentConnection::isAuthenticated);
    }

    public Collection<AgentConnection> all() {
        return agents.values();
    }

    public int size() {
        return agents.size();
    }
}
