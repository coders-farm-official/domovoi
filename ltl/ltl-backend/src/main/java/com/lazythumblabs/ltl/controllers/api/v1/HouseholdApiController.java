package com.lazythumblabs.ltl.controllers.api.v1;

import com.lazythumblabs.ltl.dto.request.ClaimRequest;
import com.lazythumblabs.ltl.dto.response.ApiResponse;
import com.lazythumblabs.ltl.dto.response.HouseholdResponse;
import com.lazythumblabs.ltl.dto.response.UsageResponse;
import com.lazythumblabs.ltl.entities.Household;
import com.lazythumblabs.ltl.services.Entitlement;
import com.lazythumblabs.ltl.services.EntitlementService;
import com.lazythumblabs.ltl.services.EnrollmentService;
import jakarta.validation.Valid;
import org.springframework.beans.factory.annotation.Autowired;
import org.springframework.http.ResponseEntity;
import org.springframework.web.bind.annotation.*;

import java.util.List;
import java.util.Map;

@RestController
@RequestMapping("/api/v1/households")
public class HouseholdApiController extends BaseApiController {

    @Autowired
    private EnrollmentService enrollmentService;

    @Autowired
    private EntitlementService entitlementService;

    @GetMapping
    public ResponseEntity<ApiResponse<List<HouseholdResponse>>> list() {
        List<HouseholdResponse> households = householdService.listFor(getAuthenticatedUser())
                .stream().map(HouseholdResponse::from).toList();
        return ResponseEntity.ok(ApiResponse.success(households));
    }

    /** Claim a Domovoi server with the eight words its dashboard showed. */
    @PostMapping("/claim")
    public ResponseEntity<ApiResponse<HouseholdResponse>> claim(
            @Valid @RequestBody ClaimRequest request) {
        Household household = enrollmentService.claim(
                getAuthenticatedUser(), request.getCode(), request.getName());
        return ResponseEntity.ok(ApiResponse.success(HouseholdResponse.from(household)));
    }

    @GetMapping("/{householdId}")
    public ResponseEntity<ApiResponse<HouseholdResponse>> get(@PathVariable String householdId) {
        return ResponseEntity.ok(
                ApiResponse.success(HouseholdResponse.from(requireOwnedHousehold(householdId))));
    }

    @GetMapping("/{householdId}/usage")
    public ResponseEntity<ApiResponse<UsageResponse>> usage(@PathVariable String householdId) {
        Household household = requireOwnedHousehold(householdId);
        Entitlement entitlement = entitlementService.resolve(household);
        return ResponseEntity.ok(ApiResponse.success(new UsageResponse(
                household.getHouseholdUid(), entitlement.bytesUsed(),
                entitlement.bytesLimit(), entitlement.periodEnd())));
    }

    /**
     * Rotate the relay token.
     *
     * <p>Called by the Domovoi dashboard on the household's behalf,
     * authenticating with the current token. Returned once and stored
     * only as a hash — and deliberately not returned to a browser
     * session, so a stolen web session cannot walk away with a
     * household's relay credential.
     */
    @PostMapping("/{householdId}/relay-token")
    public ResponseEntity<ApiResponse<Map<String, String>>> rotateToken(
            @PathVariable String householdId,
            @RequestHeader(value = "Authorization", required = false) String authorization) {
        String presented = authorization != null && authorization.startsWith("Bearer ")
                ? authorization.substring(7).trim() : "";
        Household household = householdService.findByRelayToken(presented)
                .filter(candidate -> candidate.getHouseholdUid().equals(householdId))
                .orElseThrow(() -> new ApiExceptionHandler.NotAuthenticatedException(
                        "rotation requires the household's current relay token"));
        String fresh = householdService.rotateRelayToken(household);
        return ResponseEntity.ok(ApiResponse.success(Map.of("relayToken", fresh)));
    }

    /**
     * Unpair a household.
     *
     * <p>Worth being plain about the blast radius, which the web app
     * repeats to the user: the Domovoi server itself is untouched and
     * keeps working on the LAN. It simply stops being reachable from
     * outside.
     */
    @DeleteMapping("/{householdId}")
    public ResponseEntity<ApiResponse<Map<String, Boolean>>> unpair(
            @PathVariable String householdId) {
        householdService.delete(requireOwnedHousehold(householdId));
        return ResponseEntity.ok(ApiResponse.success(Map.of("unpaired", true)));
    }
}
