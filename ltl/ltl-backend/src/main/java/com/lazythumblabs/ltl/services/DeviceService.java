package com.lazythumblabs.ltl.services;

import com.lazythumblabs.ltl.dto.request.RegisterDeviceRequest;
import com.lazythumblabs.ltl.entities.Device;
import com.lazythumblabs.ltl.entities.Household;
import com.lazythumblabs.ltl.repositories.DeviceRepository;
import com.lazythumblabs.ltl.util.CryptoUtil;
import org.slf4j.Logger;
import org.slf4j.LoggerFactory;
import org.springframework.beans.factory.annotation.Autowired;
import org.springframework.stereotype.Service;
import org.springframework.transaction.annotation.Transactional;

import java.time.LocalDateTime;
import java.util.List;
import java.util.Optional;

/**
 * Client devices.
 *
 * <p>Registering a device here does not grant it anything. The household
 * decides, on its own dashboard and against its own database, which
 * public key it will complete a handshake with — so the {@code approved}
 * column in this service is a mirror for display, and flipping it grants
 * no access whatsoever.
 */
@Service
public class DeviceService {

    private static final Logger logger = LoggerFactory.getLogger(DeviceService.class);

    @Autowired
    private DeviceRepository deviceRepository;

    @Autowired
    private EntitlementService entitlementService;

    public static class DeviceException extends RuntimeException {
        private final String code;

        public DeviceException(String code, String message) {
            super(message);
            this.code = code;
        }

        public String getCode() {
            return code;
        }
    }

    public List<Device> listFor(Household household) {
        return deviceRepository.findByHouseholdIdOrderByRegisteredAtDesc(household.getId());
    }

    public Optional<Device> findByUid(String deviceUid) {
        return deviceRepository.findByDeviceUid(deviceUid);
    }

    @Transactional
    public Device register(Household household, RegisterDeviceRequest request) {
        if (!CryptoUtil.isValidPublicKey(request.getPublicKey())) {
            throw new DeviceException("INVALID_KEY",
                    "that is not a valid P-256 public key");
        }

        int deviceLimit = entitlementService.resolve(household).deviceLimit();
        long active = deviceRepository.countByHouseholdIdAndRevokedAtIsNull(household.getId());
        if (active >= deviceLimit) {
            throw new DeviceException("PLAN_LIMIT",
                    "your plan allows " + deviceLimit + " devices; revoke one or upgrade");
        }

        Device device = new Device();
        device.setHousehold(household);
        device.setDeviceUid(CryptoUtil.randomUid("d_"));
        device.setLabel(request.getLabel().trim());
        device.setPublicKey(request.getPublicKey());
        device.setFingerprint(CryptoUtil.deviceFingerprint(request.getPublicKey()));
        device.setPlatform(request.getPlatform());
        device.setApproved(false);
        device.setRegisteredAt(LocalDateTime.now());
        deviceRepository.save(device);

        logger.info("device {} registered for household {} (awaiting local approval)",
                device.getDeviceUid(), household.getHouseholdUid());
        return device;
    }

    /**
     * Record what the household told us about a device.
     *
     * <p>Arrives as a {@code device_state} control frame from the agent
     * after an admin approves or revokes on the Domovoi dashboard. It is
     * a report, not an instruction — the household has already acted, and
     * this only keeps the LTL web app from showing a stale state.
     */
    @Transactional
    public void applyHouseholdState(Household household, String deviceUid, String status) {
        deviceRepository.findByDeviceUid(deviceUid)
                .filter(device -> device.getHousehold().getId().equals(household.getId()))
                .ifPresent(device -> {
                    switch (status) {
                        case "approved" -> {
                            device.setApproved(true);
                            device.setRevokedAt(null);
                        }
                        case "revoked" -> {
                            device.setApproved(false);
                            device.setRevokedAt(LocalDateTime.now());
                        }
                        default -> device.setApproved(false);
                    }
                    deviceRepository.save(device);
                });
    }

    @Transactional
    public void markSeen(String deviceUid, String ipCountry) {
        deviceRepository.findByDeviceUid(deviceUid).ifPresent(device -> {
            device.setLastSeenAt(LocalDateTime.now());
            if (ipCountry != null) {
                device.setLastSeenCountry(ipCountry);
            }
            deviceRepository.save(device);
        });
    }

    /**
     * Forget a device on the LTL side.
     *
     * <p>Worth being clear about what this does and does not do: it stops
     * the device from opening a link through the relay. It does not
     * un-approve it on the household, which is the authoritative side —
     * revoking there is the step that actually matters, and the web app
     * says so.
     */
    @Transactional
    public void revoke(Device device) {
        device.setRevokedAt(LocalDateTime.now());
        device.setApproved(false);
        deviceRepository.save(device);
        logger.info("device {} revoked at the relay", device.getDeviceUid());
    }
}
