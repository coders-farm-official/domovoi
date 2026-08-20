package com.lazythumblabs.ltl.services;

import com.lazythumblabs.ltl.dto.request.EnrollRequest;
import com.lazythumblabs.ltl.dto.response.EnrollmentResponse;
import com.lazythumblabs.ltl.dto.response.EnrollmentStatusResponse;
import com.lazythumblabs.ltl.entities.EnrollmentAttempt;
import com.lazythumblabs.ltl.entities.Household;
import com.lazythumblabs.ltl.entities.PendingEnrollment;
import com.lazythumblabs.ltl.entities.User;
import com.lazythumblabs.ltl.repositories.EnrollmentAttemptRepository;
import com.lazythumblabs.ltl.repositories.HouseholdRepository;
import com.lazythumblabs.ltl.repositories.PendingEnrollmentRepository;
import com.lazythumblabs.ltl.util.CryptoUtil;
import org.slf4j.Logger;
import org.slf4j.LoggerFactory;
import org.springframework.beans.factory.annotation.Autowired;
import org.springframework.beans.factory.annotation.Value;
import org.springframework.scheduling.annotation.Scheduled;
import org.springframework.stereotype.Service;
import org.springframework.transaction.annotation.Transactional;

import java.nio.charset.StandardCharsets;
import java.security.MessageDigest;
import java.time.LocalDateTime;
import java.util.HexFormat;
import java.util.Locale;
import java.util.Optional;
import java.util.regex.Pattern;

/**
 * Pairing: the two halves of binding a Domovoi server to an account.
 *
 * <p>The home server mints an eight-word code and sends only its
 * <em>hash</em>. That ordering is what makes typing eight words into a
 * website safe: LTL never holds a value that would let it impersonate
 * the household during its own pairing window, and a dump of
 * {@code pending_enrollments} cannot be replayed into a claim.
 */
@Service
public class EnrollmentService {

    private static final Logger logger = LoggerFactory.getLogger(EnrollmentService.class);

    /** Must match {@code pairing.CODE_HASH_LABEL} in the plugin. */
    private static final String CODE_HASH_LABEL = "ltl-remote/v1 pairing:";

    private static final Pattern SEPARATORS = Pattern.compile("[\\s\\-_.,]+");
    private static final int CODE_WORDS = 8;

    @Autowired
    private PendingEnrollmentRepository enrollmentRepository;

    @Autowired
    private HouseholdRepository householdRepository;

    @Autowired
    private EnrollmentAttemptRepository attemptRepository;

    @Autowired
    private EntitlementService entitlementService;

    @Value("${ltl.enrollment.ttl-minutes:15}")
    private int ttlMinutes;

    @Value("${ltl.enrollment.max-per-ip-per-hour:20}")
    private int maxPerIpPerHour;

    /** A refusal the API turns into a 4xx with this message. */
    public static class EnrollmentException extends RuntimeException {
        private final String code;

        public EnrollmentException(String code, String message) {
            super(message);
            this.code = code;
        }

        public String getCode() {
            return code;
        }
    }

    // ── the home server's side ──────────────────────────────────────────

    @Transactional
    public EnrollmentResponse register(EnrollRequest request, String sourceIp) {
        enforceRateLimit(sourceIp);

        // Reject unusable keys here, at enrollment, rather than at a
        // customer's first connection attempt weeks later.
        if (!CryptoUtil.isValidPublicKey(request.getDhPublicKey())
                || !CryptoUtil.isValidPublicKey(request.getSigPublicKey())) {
            throw new EnrollmentException("INVALID_KEY",
                    "the public keys are not valid P-256 points");
        }

        // Recompute the fingerprint rather than trusting the one sent.
        // The string a customer is asked to compare must be one LTL
        // derived from the keys it actually holds, or it would be
        // attacker-chosen and the comparison would prove nothing.
        String fingerprint = CryptoUtil.fingerprint(
                request.getDhPublicKey(), request.getSigPublicKey());
        if (!fingerprint.equals(request.getFingerprint())) {
            logger.info("enrollment fingerprint mismatch from {} — using the derived value",
                    sourceIp);
        }

        // A repeat enrollment of the same code hash replaces the old
        // window: a server that restarted mid-pairing should be able to
        // re-register rather than being locked out by its own leftovers.
        enrollmentRepository.findByCodeHash(request.getCodeHash())
                .filter(existing -> PendingEnrollment.PENDING.equals(existing.getStatus()))
                .ifPresent(enrollmentRepository::delete);

        String pollToken = CryptoUtil.randomToken();
        PendingEnrollment enrollment = new PendingEnrollment();
        enrollment.setEnrollmentUid(CryptoUtil.randomUid("e_"));
        enrollment.setCodeHash(request.getCodeHash());
        enrollment.setPollTokenHash(CryptoUtil.sha256Hex(pollToken));
        enrollment.setDhPublicKey(request.getDhPublicKey());
        enrollment.setSigPublicKey(request.getSigPublicKey());
        enrollment.setFingerprint(fingerprint);
        enrollment.setHostname(request.getHostname());
        enrollment.setSourceIp(sourceIp);
        enrollment.setExpiresAt(LocalDateTime.now().plusMinutes(ttlMinutes));
        enrollmentRepository.save(enrollment);

        return new EnrollmentResponse(
                enrollment.getEnrollmentUid(), pollToken,
                enrollment.getExpiresAt().toString());
    }

    /**
     * The agent's poll.
     *
     * <p>On the first poll after a claim this returns the relay token —
     * the only time it is ever sent — and clears it from the database in
     * the same transaction.
     */
    @Transactional
    public EnrollmentStatusResponse poll(String enrollmentUid, String pollToken) {
        PendingEnrollment enrollment = enrollmentRepository.findByEnrollmentUid(enrollmentUid)
                .orElseThrow(() -> new EnrollmentException("NOT_FOUND", "no such enrollment"));

        if (!CryptoUtil.constantTimeEquals(
                enrollment.getPollTokenHash(), CryptoUtil.sha256Hex(pollToken))) {
            throw new EnrollmentException("UNAUTHORIZED", "that token does not match");
        }

        if (PendingEnrollment.CLAIMED.equals(enrollment.getStatus())) {
            String relayToken = enrollment.getClaimedSecret();
            if (relayToken == null) {
                // Already handed over once. Re-issuing would mean two
                // agents holding valid tokens for one household.
                throw new EnrollmentException("ALREADY_COLLECTED",
                        "this enrollment's credentials were already collected");
            }
            enrollment.setClaimedSecret(null);
            enrollmentRepository.save(enrollment);
            Household household = enrollment.getHousehold();
            return EnrollmentStatusResponse.claimed(
                    household.getHouseholdUid(), relayToken,
                    household.getUser().getEmail());
        }

        if (enrollment.isExpired()) {
            throw new EnrollmentException("EXPIRED", "this pairing code has expired");
        }
        return EnrollmentStatusResponse.pending();
    }

    // ── the customer's side ─────────────────────────────────────────────

    /**
     * Claim a household with the eight words shown on its dashboard.
     *
     * <p>The words are normalized the same way the plugin normalizes
     * them before hashing, so whitespace and capitalization differences
     * between the two humans involved do not fail a pairing.
     */
    @Transactional
    public Household claim(User user, String typedCode, String name) {
        String codeHash = hashCode(typedCode);

        PendingEnrollment enrollment = enrollmentRepository.findByCodeHash(codeHash)
                .orElseThrow(() -> new EnrollmentException("NOT_FOUND",
                        "that code doesn't match a server waiting to be paired"));

        if (PendingEnrollment.CLAIMED.equals(enrollment.getStatus())) {
            throw new EnrollmentException("ALREADY_CLAIMED",
                    "that code has already been used");
        }
        if (enrollment.isExpired()) {
            throw new EnrollmentException("EXPIRED",
                    "that code has expired — generate a new one on your dashboard");
        }

        enforceHouseholdLimit(user);

        String relayToken = CryptoUtil.randomToken();
        Household household = new Household();
        household.setUser(user);
        household.setHouseholdUid(CryptoUtil.randomUid("h_"));
        household.setName(pickName(name, enrollment));
        household.setHostname(enrollment.getHostname());
        household.setDhPublicKey(enrollment.getDhPublicKey());
        household.setSigPublicKey(enrollment.getSigPublicKey());
        household.setFingerprint(enrollment.getFingerprint());
        household.setRelayTokenHash(CryptoUtil.sha256Hex(relayToken));
        householdRepository.save(household);

        enrollment.setStatus(PendingEnrollment.CLAIMED);
        enrollment.setHousehold(household);
        enrollment.setClaimedAt(LocalDateTime.now());
        // Parked for the agent's next poll, then cleared. The two halves
        // of pairing are asynchronous — the person typing the code and
        // the server waiting for it are not in the same request.
        enrollment.setClaimedSecret(relayToken);
        enrollmentRepository.save(enrollment);

        logger.info("account {} claimed household {}", user.getId(), household.getHouseholdUid());
        return household;
    }

    private String pickName(String requested, PendingEnrollment enrollment) {
        if (requested != null && !requested.isBlank()) {
            return requested.trim();
        }
        if (enrollment.getHostname() != null && !enrollment.getHostname().isBlank()) {
            return enrollment.getHostname();
        }
        return "My house";
    }

    /**
     * Plan limit on how many Domovoi servers one account may pair.
     *
     * <p>The number comes from the {@code plans} row, so the limit
     * enforced here and the limit advertised on the pricing page cannot
     * drift apart.
     */
    private void enforceHouseholdLimit(User user) {
        long existing = householdRepository.countByUserId(user.getId());
        int allowed = Math.max(1, entitlementService.planFor(user.getId()).getHouseholdLimit());
        if (existing >= allowed) {
            throw new EnrollmentException("PLAN_LIMIT",
                    "your plan allows " + allowed + " household"
                            + (allowed == 1 ? "" : "s") + "; upgrade to pair another");
        }
    }

    // ── hygiene ─────────────────────────────────────────────────────────

    private void enforceRateLimit(String sourceIp) {
        LocalDateTime since = LocalDateTime.now().minusHours(1);
        long recent = attemptRepository.countBySourceIpAndAttemptedAtAfter(sourceIp, since);
        if (recent >= maxPerIpPerHour) {
            throw new EnrollmentException("RATE_LIMITED",
                    "too many pairing attempts from this address; try again later");
        }
        EnrollmentAttempt attempt = new EnrollmentAttempt();
        attempt.setSourceIp(sourceIp);
        attemptRepository.save(attempt);
    }

    /**
     * Hash a typed code the same way the plugin does.
     *
     * <p>Normalization first — lowercase, collapse separators — because
     * a person retyping eight words off a screen uses spaces instead of
     * hyphens and capitalizes the first word, and none of that should
     * fail a pairing.
     */
    static String hashCode(String typed) {
        if (typed == null) {
            throw new EnrollmentException("INVALID_CODE", "enter the eight words from your dashboard");
        }
        String[] words = SEPARATORS.split(typed.trim().toLowerCase(Locale.ROOT));
        StringBuilder canonical = new StringBuilder();
        int count = 0;
        for (String word : words) {
            if (word.isEmpty()) {
                continue;
            }
            if (count > 0) {
                canonical.append('-');
            }
            canonical.append(word);
            count++;
        }
        if (count != CODE_WORDS) {
            throw new EnrollmentException("INVALID_CODE",
                    "a pairing code is " + CODE_WORDS + " words");
        }
        try {
            byte[] input = (CODE_HASH_LABEL + canonical).getBytes(StandardCharsets.UTF_8);
            return HexFormat.of().formatHex(
                    MessageDigest.getInstance("SHA-256").digest(input));
        } catch (Exception e) {
            throw new IllegalStateException("SHA-256 is unavailable", e);
        }
    }

    /** Expire stale windows and forget old rate-limit rows. */
    @Scheduled(fixedDelay = 300_000)
    @Transactional
    public void sweep() {
        int expired = enrollmentRepository.expireOlderThan(LocalDateTime.now());
        enrollmentRepository.deleteOlderThan(LocalDateTime.now().minusDays(7));
        attemptRepository.deleteOlderThan(LocalDateTime.now().minusDays(1));
        if (expired > 0) {
            logger.info("expired {} unclaimed pairing windows", expired);
        }
    }

    public Optional<PendingEnrollment> findByUid(String enrollmentUid) {
        return enrollmentRepository.findByEnrollmentUid(enrollmentUid);
    }
}
