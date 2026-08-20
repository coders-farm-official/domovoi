package com.lazythumblabs.ltl.dto.response;

import com.fasterxml.jackson.annotation.JsonInclude;

import java.util.List;

@JsonInclude(JsonInclude.Include.NON_NULL)
public class ApiError {

    private String code;
    private String message;
    private List<String> fieldErrors;

    public ApiError() {}

    public ApiError(String code, String message) {
        this.code = code;
        this.message = message;
    }

    public ApiError(String code, String message, List<String> fieldErrors) {
        this.code = code;
        this.message = message;
        this.fieldErrors = fieldErrors;
    }

    public String getCode() { return code; }
    public String getMessage() { return message; }
    public List<String> getFieldErrors() { return fieldErrors; }
}
