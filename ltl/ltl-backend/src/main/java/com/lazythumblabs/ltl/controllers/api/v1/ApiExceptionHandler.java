package com.lazythumblabs.ltl.controllers.api.v1;

import com.lazythumblabs.ltl.dto.response.ApiError;
import com.lazythumblabs.ltl.dto.response.ApiResponse;
import com.lazythumblabs.ltl.services.DeviceService;
import com.lazythumblabs.ltl.services.EnrollmentService;
import com.lazythumblabs.ltl.services.StripeBillingProvider;
import org.slf4j.Logger;
import org.slf4j.LoggerFactory;
import org.springframework.http.HttpStatus;
import org.springframework.http.ResponseEntity;
import org.springframework.security.access.AccessDeniedException;
import org.springframework.web.bind.MethodArgumentNotValidException;
import org.springframework.web.bind.annotation.ExceptionHandler;
import org.springframework.web.bind.annotation.RestControllerAdvice;

import java.util.List;

@RestControllerAdvice(basePackages = "com.lazythumblabs.ltl.controllers.api.v1")
public class ApiExceptionHandler {

    private static final Logger logger = LoggerFactory.getLogger(ApiExceptionHandler.class);

    @ExceptionHandler(ResourceNotFoundException.class)
    public ResponseEntity<ApiResponse<Void>> handleNotFound(ResourceNotFoundException ex) {
        return ResponseEntity.status(HttpStatus.NOT_FOUND)
                .body(ApiResponse.error("NOT_FOUND", ex.getMessage()));
    }

    @ExceptionHandler(NotAuthenticatedException.class)
    public ResponseEntity<ApiResponse<Void>> handleUnauthenticated(NotAuthenticatedException ex) {
        return ResponseEntity.status(HttpStatus.UNAUTHORIZED)
                .body(ApiResponse.error("UNAUTHENTICATED", ex.getMessage()));
    }

    /**
     * Enrollment failures carry their own code, and the message is
     * written to be shown verbatim to whoever is holding the pairing
     * code — the Domovoi dashboard passes it straight through.
     */
    @ExceptionHandler(EnrollmentService.EnrollmentException.class)
    public ResponseEntity<ApiResponse<Void>> handleEnrollment(
            EnrollmentService.EnrollmentException ex) {
        HttpStatus status = switch (ex.getCode()) {
            case "NOT_FOUND" -> HttpStatus.NOT_FOUND;
            case "UNAUTHORIZED" -> HttpStatus.UNAUTHORIZED;
            case "RATE_LIMITED" -> HttpStatus.TOO_MANY_REQUESTS;
            case "PLAN_LIMIT" -> HttpStatus.PAYMENT_REQUIRED;
            default -> HttpStatus.BAD_REQUEST;
        };
        return ResponseEntity.status(status)
                .body(ApiResponse.error(ex.getCode(), ex.getMessage()));
    }

    @ExceptionHandler(DeviceService.DeviceException.class)
    public ResponseEntity<ApiResponse<Void>> handleDevice(DeviceService.DeviceException ex) {
        HttpStatus status = "PLAN_LIMIT".equals(ex.getCode())
                ? HttpStatus.PAYMENT_REQUIRED : HttpStatus.BAD_REQUEST;
        return ResponseEntity.status(status)
                .body(ApiResponse.error(ex.getCode(), ex.getMessage()));
    }

    @ExceptionHandler(StripeBillingProvider.BillingException.class)
    public ResponseEntity<ApiResponse<Void>> handleBilling(
            StripeBillingProvider.BillingException ex) {
        logger.warn("billing provider failure: {}", ex.getMessage());
        return ResponseEntity.status(HttpStatus.BAD_GATEWAY)
                .body(ApiResponse.error("BILLING_UNAVAILABLE",
                        "the payment provider did not respond; nothing was charged"));
    }

    @ExceptionHandler(MethodArgumentNotValidException.class)
    public ResponseEntity<ApiResponse<Void>> handleValidation(MethodArgumentNotValidException ex) {
        List<String> fieldErrors = ex.getBindingResult().getFieldErrors().stream()
                .map(error -> error.getField() + ": " + error.getDefaultMessage())
                .toList();
        return ResponseEntity.status(HttpStatus.BAD_REQUEST)
                .body(ApiResponse.error(
                        new ApiError("VALIDATION_ERROR", "that request isn't valid", fieldErrors)));
    }

    @ExceptionHandler(AccessDeniedException.class)
    public ResponseEntity<ApiResponse<Void>> handleAccessDenied(AccessDeniedException ex) {
        return ResponseEntity.status(HttpStatus.FORBIDDEN)
                .body(ApiResponse.error("FORBIDDEN", "Access denied"));
    }

    @ExceptionHandler(IllegalArgumentException.class)
    public ResponseEntity<ApiResponse<Void>> handleBadRequest(IllegalArgumentException ex) {
        return ResponseEntity.status(HttpStatus.BAD_REQUEST)
                .body(ApiResponse.error("BAD_REQUEST", ex.getMessage()));
    }

    @ExceptionHandler(Exception.class)
    public ResponseEntity<ApiResponse<Void>> handleGeneric(Exception ex) {
        logger.error("Unhandled API exception", ex);
        return ResponseEntity.status(HttpStatus.INTERNAL_SERVER_ERROR)
                .body(ApiResponse.error("INTERNAL_ERROR", "An unexpected error occurred"));
    }

    public static class ResourceNotFoundException extends RuntimeException {
        public ResourceNotFoundException(String message) {
            super(message);
        }
    }

    public static class NotAuthenticatedException extends RuntimeException {
        public NotAuthenticatedException(String message) {
            super(message);
        }
    }
}
