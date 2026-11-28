# Vendored crates

## sysinfo-0.38.4

[sysinfo](https://github.com/GuillaumeGomez/sysinfo) 0.38.4 (MIT, by
Guillaume Gomez; `LICENSE` in the folder, unchanged), which Arti's
`tor-memquota` 0.48.0 depends on (`^0.38.4`, feature `system`), used through
`[patch.crates-io]` in `../Cargo.toml`. 0.38.4 doesn't build for iOS:
`Process::open_files()` calls `libc::proc_pidinfo` with `PROC_PIDLISTFDS`,
which the `libc` crate declares for macOS only.

The files are the crates.io package's (`Cargo.toml` as published,
`LICENSE`, `README.md`, `src/`, `md_doc/`), with two changes:

- `src/unix/apple/process.rs`: on iOS, `open_files()` returns `None`
  (nothing in Arti asks for it); macOS keeps the original code:

  ```diff
   impl ProcessInner {
  +    #[cfg(target_os = "ios")]
  +    pub(crate) fn open_files(&self) -> Option<usize> {
  +        None
  +    }
  +
  +    #[cfg(not(target_os = "ios"))]
       pub(crate) fn open_files(&self) -> Option<usize> {
  ```

- `Cargo.toml`: the `[[example]]`, `[[test]]` and `[[bench]]` targets are
  taken out, since their files (`examples/`, `tests/`, `benches/`) are not
  vendored. Nothing the library builds from is left out.

Android's library builds the same code (the change is iOS-only), though its
bytes changed when this went in: Cargo names a path crate differently from
a registry one, so `android/native/domovoi-tor/SHA256SUMS` was written again
then.

**Upstream:** fixed in sysinfo 0.40.0 ("iOS: Make `Process::open_files`
return `None`", PR #1679, merged 2026-06-18). `tor-memquota` 0.48.0 still
asks for `^0.38.4`, which can't take 0.40. When an Arti release moves to a
sysinfo with the fix, drop this folder and the `[patch.crates-io]` entry,
then rebuild both platforms and their notices.
