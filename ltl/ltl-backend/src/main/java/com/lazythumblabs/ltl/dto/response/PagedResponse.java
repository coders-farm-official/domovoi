package com.lazythumblabs.ltl.dto.response;

import java.util.List;

public class PagedResponse<T> {

    private List<T> content;
    private int page;
    private int size;
    private int totalPages;
    private long totalElements;

    public PagedResponse() {}

    public PagedResponse(List<T> content, int page, int size, int totalPages, long totalElements) {
        this.content = content;
        this.page = page;
        this.size = size;
        this.totalPages = totalPages;
        this.totalElements = totalElements;
    }

    public List<T> getContent() { return content; }
    public int getPage() { return page; }
    public int getSize() { return size; }
    public int getTotalPages() { return totalPages; }
    public long getTotalElements() { return totalElements; }
}
