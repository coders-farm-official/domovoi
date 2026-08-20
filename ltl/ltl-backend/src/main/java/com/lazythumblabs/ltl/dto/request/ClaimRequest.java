package com.lazythumblabs.ltl.dto.request;

import jakarta.validation.constraints.NotBlank;
import jakarta.validation.constraints.Size;

/**
 * A signed-in user claiming a household with the eight words their
 * Domovoi dashboard showed them.
 *
 * <p>The words arrive here in the clear and are hashed server-side to
 * match the pending row. They are never stored, never logged, and never
 * echoed back.
 */
public class ClaimRequest {

    @NotBlank
    @Size(max = 200)
    private String code;

    /** Optional friendly name; defaults to the hostname the server reported. */
    @Size(max = 255)
    private String name;

    public String getCode() { return code; }
    public void setCode(String code) { this.code = code; }

    public String getName() { return name; }
    public void setName(String name) { this.name = name; }
}
