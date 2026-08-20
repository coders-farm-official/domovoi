package com.lazythumblabs.ltl.dto.request;

import jakarta.validation.constraints.NotBlank;
import jakarta.validation.constraints.Size;

public class CheckoutRequest {

    @NotBlank
    @Size(max = 50)
    private String planCode;

    public String getPlanCode() { return planCode; }
    public void setPlanCode(String planCode) { this.planCode = planCode; }
}
