package com.lazythumblabs.ltl.controllers.api.v1;

import com.lazythumblabs.ltl.dto.request.EnrollRequest;
import com.lazythumblabs.ltl.dto.response.ApiResponse;
import com.lazythumblabs.ltl.dto.response.EnrollmentResponse;
import com.lazythumblabs.ltl.dto.response.EnrollmentStatusResponse;
import com.lazythumblabs.ltl.services.EnrollmentService;
import jakarta.servlet.http.HttpServletRequest;
import jakarta.validation.Valid;
import org.springframework.beans.factory.annotation.Autowired;
import org.springframework.http.ResponseEntity;
import org.springframework.web.bind.annotation.*;

/**
 * The unauthenticated half of pairing, called by a Domovoi server that
 * does not yet belong to anybody.
 *
 * <p>There is no session here by design — a server opening a pairing
 * window has no account to authenticate as. What protects these
 * endpoints instead: the code hash is 64 bits of entropy the caller
 * chose and LTL never learns, enrollments expire in minutes, the poll
 * token is checked in constant time, and registration is rate limited
 * per source address.
 */
@RestController
@RequestMapping("/api/v1/enroll")
public class EnrollmentApiController {

    @Autowired
    private EnrollmentService enrollmentService;

    /** Open a pairing window. Returns the id and token used to poll it. */
    @PostMapping
    public ResponseEntity<ApiResponse<EnrollmentResponse>> register(
            @Valid @RequestBody EnrollRequest request, HttpServletRequest httpRequest) {
        EnrollmentResponse response =
                enrollmentService.register(request, clientIp(httpRequest));
        return ResponseEntity.ok(ApiResponse.success(response));
    }

    /**
     * Poll a pairing window.
     *
     * <p>Returns the relay token exactly once, on the first poll after a
     * claim, and clears it in the same transaction.
     */
    @GetMapping("/{enrollmentId}")
    public ResponseEntity<ApiResponse<EnrollmentStatusResponse>> poll(
            @PathVariable String enrollmentId,
            @RequestHeader(value = "Authorization", required = false) String authorization) {
        String token = authorization != null && authorization.startsWith("Bearer ")
                ? authorization.substring(7).trim()
                : "";
        return ResponseEntity.ok(
                ApiResponse.success(enrollmentService.poll(enrollmentId, token)));
    }

    /**
     * The caller's address, for rate limiting.
     *
     * <p>Trusts {@code X-Forwarded-For} because this service runs behind
     * a reverse proxy that sets it. Deployed without one, the header
     * would be caller-controlled and the rate limit would be worth
     * nothing — which is a deployment requirement, noted in
     * {@code deploy/README.md}.
     */
    private String clientIp(HttpServletRequest request) {
        String forwarded = request.getHeader("X-Forwarded-For");
        if (forwarded != null && !forwarded.isBlank()) {
            int comma = forwarded.indexOf(',');
            return (comma > 0 ? forwarded.substring(0, comma) : forwarded).trim();
        }
        return request.getRemoteAddr();
    }
}
