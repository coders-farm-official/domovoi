# Vendored packages

## onnxruntime-swift-package-manager (1.24.2)

Microsoft's Swift package for ONNX Runtime, at tag `1.24.2` (commit
`b7fb7f7dea8a2469e6335d95a61b8f36d0dc83b2`), MIT (`LICENSE` in the folder).
The GitHub repository it came from, `microsoft/onnxruntime-swift-package-manager`,
stopped answering on 2026-11-17, which broke every fresh package resolve of
`ios/Packages/DomovoiKit`. The files here are that tag's, unchanged, except
that the `.github`, `.pipelines` and `.config` folders (its own CI) are left
out.

The library itself is not in this folder: `Package.swift` still downloads the
CocoaPods `onnxruntime-c` archive from `download.onnxruntime.ai` and checks it
against the SHA-256 written in that file
(`f7100a992d2a8135168c8afd831e6a58b465349101982aa58b3e11d36e600b54` for
`pod-archive-onnxruntime-c-1.24.2.zip`). To move to a newer ONNX Runtime,
replace this folder with that version's files (and their checksum) from a
source you trust, and keep this note current.
