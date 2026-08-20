package com.lazythumblabs.ltl.dto.response;

import com.fasterxml.jackson.annotation.JsonInclude;

import java.time.Instant;

/**
 * The envelope every {@code /api/v1} response carries, matching the
 * shape used across Lazy Thumb Labs services: {@code data} on success,
 * {@code error} on failure, never both.
 *
 * <p>The Domovoi plugin unwraps exactly this — see
 * {@code ltl_api.LtlApiClient._unwrap}.
 */
@JsonInclude(JsonInclude.Include.NON_NULL)
public class ApiResponse<T> {

    private T data;
    private ApiError error;
    private String timestamp;

    private ApiResponse() {
        this.timestamp = Instant.now().toString();
    }

    public static <T> ApiResponse<T> success(T data) {
        ApiResponse<T> response = new ApiResponse<>();
        response.data = data;
        return response;
    }

    public static <T> ApiResponse<T> error(String code, String message) {
        ApiResponse<T> response = new ApiResponse<>();
        response.error = new ApiError(code, message);
        return response;
    }

    public static <T> ApiResponse<T> error(ApiError apiError) {
        ApiResponse<T> response = new ApiResponse<>();
        response.error = apiError;
        return response;
    }

    public T getData() { return data; }
    public ApiError getError() { return error; }
    public String getTimestamp() { return timestamp; }
}
