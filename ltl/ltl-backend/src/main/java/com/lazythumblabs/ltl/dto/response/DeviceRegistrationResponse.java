package com.lazythumblabs.ltl.dto.response;

/**
 * The answer to registering a device: the device is on file, and now a
 * human has to approve it on the household's own dashboard.
 *
 * <p>{@code nextStep} is a sentence rather than a status code because
 * this is the one moment where the product's security model needs
 * explaining to the person in front of it.
 */
public class DeviceRegistrationResponse {

    private final DeviceResponse device;
    private final String householdFingerprint;
    private final String nextStep;

    public DeviceRegistrationResponse(DeviceResponse device, String householdFingerprint) {
        this.device = device;
        this.householdFingerprint = householdFingerprint;
        this.nextStep = "Open Remote Access on your Domovoi dashboard, check that the "
                + "fingerprint matches, and approve this device. Until then it cannot "
                + "reach your house.";
    }

    public DeviceResponse getDevice() { return device; }
    public String getHouseholdFingerprint() { return householdFingerprint; }
    public String getNextStep() { return nextStep; }
}
