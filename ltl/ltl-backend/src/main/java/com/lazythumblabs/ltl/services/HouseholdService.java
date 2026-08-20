package com.lazythumblabs.ltl.services;

import com.lazythumblabs.ltl.entities.Household;
import com.lazythumblabs.ltl.entities.User;
import com.lazythumblabs.ltl.repositories.HouseholdRepository;
import com.lazythumblabs.ltl.util.CryptoUtil;
import org.slf4j.Logger;
import org.slf4j.LoggerFactory;
import org.springframework.beans.factory.annotation.Autowired;
import org.springframework.stereotype.Service;
import org.springframework.transaction.annotation.Transactional;

import java.time.LocalDateTime;
import java.util.List;
import java.util.Optional;

@Service
public class HouseholdService {

    private static final Logger logger = LoggerFactory.getLogger(HouseholdService.class);

    @Autowired
    private HouseholdRepository householdRepository;

    public List<Household> listFor(User user) {
        return householdRepository.findByUserIdOrderByCreatedAtAsc(user.getId());
    }

    /**
     * Look up a household the caller actually owns.
     *
     * <p>Ownership is part of the lookup, not a separate check a caller
     * could forget: passing another account's household id yields empty
     * rather than a row.
     */
    public Optional<Household> findOwned(User user, String householdUid) {
        return householdRepository.findByHouseholdUid(householdUid)
                .filter(household -> household.getUser().getId().equals(user.getId()));
    }

    /**
     * Look a household up by its public id, with no ownership filter.
     *
     * <p>For the relay, which is acting on behalf of the household
     * itself rather than a signed-in person. Anything reached from a web
     * request must use {@link #findOwned} instead, so that a household
     * id from one account can never address another's.
     */
    public Optional<Household> findOwnedByUid(String householdUid) {
        return householdRepository.findByHouseholdUid(householdUid);
    }

    /** The relay's agent-authentication lookup, keyed on the token hash. */
    public Optional<Household> findByRelayToken(String bearerToken) {
        if (bearerToken == null || bearerToken.isBlank()) {
            return Optional.empty();
        }
        return householdRepository.findByRelayTokenHash(CryptoUtil.sha256Hex(bearerToken));
    }

    /**
     * Issue a fresh relay token and return it. Shown once, stored only
     * as a hash — the old token stops working the moment this commits.
     */
    @Transactional
    public String rotateRelayToken(Household household) {
        String token = CryptoUtil.randomToken();
        household.setRelayTokenHash(CryptoUtil.sha256Hex(token));
        householdRepository.save(household);
        logger.info("rotated the relay token for household {}", household.getHouseholdUid());
        return token;
    }

    @Transactional
    public void markOnline(Household household, String agentVersion) {
        household.setOnline(true);
        household.setLastSeenAt(LocalDateTime.now());
        if (agentVersion != null) {
            household.setAgentVersion(agentVersion);
        }
        householdRepository.save(household);
    }

    @Transactional
    public void markOffline(Long householdId) {
        householdRepository.findById(householdId).ifPresent(household -> {
            household.setOnline(false);
            household.setLastSeenAt(LocalDateTime.now());
            householdRepository.save(household);
        });
    }

    /**
     * Unpair a household from this account.
     *
     * <p>Deletes the row, which cascades to its devices and usage. The
     * Domovoi server itself is untouched: it keeps working on the LAN and
     * simply stops being able to dial out, which is the correct blast
     * radius for "I no longer want remote access".
     */
    @Transactional
    public void delete(Household household) {
        logger.info("unpairing household {}", household.getHouseholdUid());
        householdRepository.delete(household);
    }

    /**
     * Every household is offline at startup until its agent reconnects.
     *
     * <p>Without this, a crash would leave rows claiming to be online
     * forever and the web app would tell customers their house is
     * reachable when it is not.
     */
    @Transactional
    public void markAllOffline() {
        householdRepository.findAll().forEach(household -> {
            if (Boolean.TRUE.equals(household.getOnline())) {
                household.setOnline(false);
                householdRepository.save(household);
            }
        });
    }
}
