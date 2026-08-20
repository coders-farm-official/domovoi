package com.lazythumblabs.ltl.dto.response;

import com.lazythumblabs.ltl.entities.Device;

import java.time.LocalDateTime;

/**
 * A device as the web app sees it.
 *
 * <p>{@code approved} is a mirror of what the household's own dashboard
 * decided. It is shown so a customer can see why a device is not
 * working; it is not what grants access.
 */
public class DeviceResponse {

    private String deviceId;
    private String label;
    private String fingerprint;
    private String platform;
    private boolean approved;
    private boolean revoked;
    private LocalDateTime registeredAt;
    private LocalDateTime lastSeenAt;
    private String lastSeenCountry;

    public static DeviceResponse from(Device device) {
        DeviceResponse response = new DeviceResponse();
        response.deviceId = device.getDeviceUid();
        response.label = device.getLabel();
        response.fingerprint = device.getFingerprint();
        response.platform = device.getPlatform();
        response.approved = Boolean.TRUE.equals(device.getApproved());
        response.revoked = device.getRevokedAt() != null;
        response.registeredAt = device.getRegisteredAt();
        response.lastSeenAt = device.getLastSeenAt();
        response.lastSeenCountry = device.getLastSeenCountry();
        return response;
    }

    public String getDeviceId() { return deviceId; }
    public String getLabel() { return label; }
    public String getFingerprint() { return fingerprint; }
    public String getPlatform() { return platform; }
    public boolean isApproved() { return approved; }
    public boolean isRevoked() { return revoked; }
    public LocalDateTime getRegisteredAt() { return registeredAt; }
    public LocalDateTime getLastSeenAt() { return lastSeenAt; }
    public String getLastSeenCountry() { return lastSeenCountry; }
}
