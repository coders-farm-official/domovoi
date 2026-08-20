package com.lazythumblabs.ltl.dto.request;

import jakarta.validation.constraints.NotBlank;
import jakarta.validation.constraints.Size;

/**
 * A client registering itself against a household.
 *
 * <p>Only a public key is sent. The private half stays in the browser's
 * IndexedDB as a non-extractable WebCrypto key, or in the Android
 * Keystore — LTL could not exfiltrate it if it wanted to.
 */
public class RegisterDeviceRequest {

    @NotBlank
    @Size(max = 64)
    private String householdId;

    @NotBlank
    @Size(max = 120)
    private String label;

    @NotBlank
    @Size(max = 255)
    private String publicKey;

    @Size(max = 50)
    private String platform;

    public String getHouseholdId() { return householdId; }
    public void setHouseholdId(String householdId) { this.householdId = householdId; }

    public String getLabel() { return label; }
    public void setLabel(String label) { this.label = label; }

    public String getPublicKey() { return publicKey; }
    public void setPublicKey(String publicKey) { this.publicKey = publicKey; }

    public String getPlatform() { return platform; }
    public void setPlatform(String platform) { this.platform = platform; }
}
