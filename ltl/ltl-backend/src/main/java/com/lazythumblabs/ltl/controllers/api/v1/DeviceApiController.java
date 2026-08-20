package com.lazythumblabs.ltl.controllers.api.v1;

import com.lazythumblabs.ltl.dto.request.RegisterDeviceRequest;
import com.lazythumblabs.ltl.dto.response.ApiResponse;
import com.lazythumblabs.ltl.dto.response.DeviceRegistrationResponse;
import com.lazythumblabs.ltl.dto.response.DeviceResponse;
import com.lazythumblabs.ltl.entities.Device;
import com.lazythumblabs.ltl.entities.Household;
import com.lazythumblabs.ltl.services.DeviceService;
import jakarta.validation.Valid;
import org.springframework.beans.factory.annotation.Autowired;
import org.springframework.http.ResponseEntity;
import org.springframework.web.bind.annotation.*;

import java.util.List;
import java.util.Map;

/**
 * Client devices.
 *
 * <p>Registering here gets a device a route, not entry. The household's
 * own dashboard decides which public key it will complete a handshake
 * with, so nothing in this controller grants access to anybody's house —
 * which is why the registration response says so in as many words.
 */
@RestController
@RequestMapping("/api/v1/devices")
public class DeviceApiController extends BaseApiController {

    @Autowired
    private DeviceService deviceService;

    @GetMapping
    public ResponseEntity<ApiResponse<List<DeviceResponse>>> list(
            @RequestParam String householdId) {
        Household household = requireOwnedHousehold(householdId);
        List<DeviceResponse> devices = deviceService.listFor(household)
                .stream().map(DeviceResponse::from).toList();
        return ResponseEntity.ok(ApiResponse.success(devices));
    }

    @PostMapping
    public ResponseEntity<ApiResponse<DeviceRegistrationResponse>> register(
            @Valid @RequestBody RegisterDeviceRequest request) {
        Household household = requireOwnedHousehold(request.getHouseholdId());
        Device device = deviceService.register(household, request);
        return ResponseEntity.ok(ApiResponse.success(new DeviceRegistrationResponse(
                DeviceResponse.from(device), household.getFingerprint())));
    }

    /**
     * Revoke a device at the relay.
     *
     * <p>This stops it opening a link. It does <em>not</em> un-approve it
     * on the household, which is the authoritative side — the web app
     * tells the user to revoke there too, because that is the step that
     * actually matters.
     */
    @DeleteMapping("/{deviceId}")
    public ResponseEntity<ApiResponse<Map<String, Boolean>>> revoke(
            @PathVariable String deviceId) {
        Device device = deviceService.findByUid(deviceId)
                .orElseThrow(() -> new ApiExceptionHandler.ResourceNotFoundException(
                        "No such device"));
        // Ownership check by way of the household lookup, so a device id
        // from another account reads as "not found".
        requireOwnedHousehold(device.getHousehold().getHouseholdUid());
        deviceService.revoke(device);
        return ResponseEntity.ok(ApiResponse.success(Map.of("revoked", true)));
    }
}
