//! Away chat's Tor (Domovoi 1.5.0): an embedded Arti onion client with
//! restricted discovery (Tor client authorization), used as a byte pipe.
//!
//! The apps speak away chat's own protocol (the `DAC1` preface, Noise IK,
//! framed messages) themselves; this library only opens streams to the
//! house's away onion. Three calls:
//!
//! * [`start`]: build a Tor client with state and cache directories (kept
//!   between sessions) and the folder holding the phone's client-auth key,
//!   `<onion56>.auth_private` (`<onion56>:descriptor:x25519:<base32 key>`,
//!   C Tor's format, read through Arti's stable C-Tor client keystore), and
//!   bootstrap it. One client per chat-screen session.
//! * [`open`]: connect to `onion:port` and return one end of a Unix socket
//!   pair; a task copies bytes between the other end and the Tor stream
//!   until either side closes.
//! * [`stop`]: drop the client and its runtime.
//!
//! The C ABI (`dtor_*`) is the iPhone app's; the JNI functions
//! (`Java_com_domovoi_app_away_ArtiTor_*`) are the Android app's. Nothing
//! here logs what passes through it.

use std::os::fd::{IntoRawFd, RawFd};
use std::panic::{catch_unwind, AssertUnwindSafe};
use std::path::Path;
use std::str::FromStr;
use std::sync::Mutex;
use std::time::Duration;

use arti_client::config::{BoolOrAuto, TorClientConfigBuilder};
use arti_client::{StreamPrefs, TorClient};
use arti_client::{ErrorKind, HasKind};
use tor_keymgr::config::CTorClientKeystoreConfigBuilder;
use tor_keymgr::KeystoreId;

/// Results as the apps read them: `>= 0` is a file descriptor (or "ok").
pub const OK: i32 = 0;
/// Something else failed (see [`last_error`]).
pub const ERR_FAILED: i32 = -1;
/// [`open`] before [`start`], or after [`stop`].
pub const ERR_NOT_STARTED: i32 = -2;
/// The onion service's descriptor wasn't found (down, or not yet published).
pub const ERR_NOT_FOUND: i32 = -3;
/// The onion asks for a client-auth key this phone doesn't hold, or holds a wrong one.
pub const ERR_CLIENT_AUTH: i32 = -4;
/// Bootstrap or the connection took too long.
pub const ERR_TIMEOUT: i32 = -5;
/// A panic inside Tor (caught).
pub const ERR_PANIC: i32 = -6;

const BOOTSTRAP_TIMEOUT: Duration = Duration::from_secs(120);
const CONNECT_TIMEOUT: Duration = Duration::from_secs(120);

struct Session {
    rt: tokio::runtime::Runtime,
    client: std::sync::Arc<TorClient<tor_rtcompat::PreferredRuntime>>,
}

static SESSION: Mutex<Option<Session>> = Mutex::new(None);
static LAST_ERROR: Mutex<String> = Mutex::new(String::new());

fn set_error(e: impl std::fmt::Display) {
    if let Ok(mut l) = LAST_ERROR.lock() {
        *l = e.to_string();
    }
}

/// The last failure, in Arti's words (no keys, no traffic).
pub fn last_error() -> String {
    LAST_ERROR.lock().map(|l| l.clone()).unwrap_or_default()
}

fn config(state: &Path, cache: &Path, auth: &Path, trusted_prefix: Option<&Path>) -> Result<arti_client::TorClientConfig, String> {
    let mut b = TorClientConfigBuilder::from_directories(state, cache);
    let mut ks = CTorClientKeystoreConfigBuilder::default();
    ks.id(KeystoreId::from_str("domovoi-away").map_err(|e| e.to_string())?);
    ks.path(auth.to_path_buf());
    b.storage().keystore().ctor().clients().access().push(ks);
    if let Some(p) = trusted_prefix {
        // Arti checks that no one else can write to its folders, up the
        // whole path. On Android the app's own folder is the app's alone
        // (0700, its own uid), but the system's folders above it
        // (/data/user/0, 0771, the system group) fail that check, so the
        // check stops at the app's folder: everything under it is still
        // checked. Not dangerously_trust_everyone().
        b.storage().permissions().ignore_prefix(p.to_path_buf());
    }
    b.build().map_err(|e| e.to_string())
}

/// Start a Tor client and bootstrap it (blocking, up to two minutes).
/// A client already running is stopped first.
pub fn start(state: &Path, cache: &Path, auth: &Path, trusted_prefix: Option<&Path>) -> i32 {
    stop();
    let _ = rustls::crypto::ring::default_provider().install_default();
    let cfg = match config(state, cache, auth, trusted_prefix) {
        Ok(c) => c,
        Err(e) => {
            set_error(e);
            return ERR_FAILED;
        }
    };
    let rt = match tokio::runtime::Builder::new_multi_thread().worker_threads(2).enable_all().build() {
        Ok(rt) => rt,
        Err(e) => {
            set_error(e);
            return ERR_FAILED;
        }
    };
    let made = rt.block_on(async {
        tokio::time::timeout(BOOTSTRAP_TIMEOUT, TorClient::create_bootstrapped(cfg)).await
    });
    match made {
        Ok(Ok(client)) => {
            if let Ok(mut s) = SESSION.lock() {
                *s = Some(Session { rt, client });
                OK
            } else {
                ERR_FAILED
            }
        }
        Ok(Err(e)) => {
            set_error(&e);
            ERR_FAILED
        }
        Err(_) => {
            set_error("Tor didn't bootstrap in time");
            ERR_TIMEOUT
        }
    }
}

fn kind_code(kind: ErrorKind) -> i32 {
    match kind {
        ErrorKind::OnionServiceNotFound | ErrorKind::OnionServiceNotRunning => ERR_NOT_FOUND,
        ErrorKind::OnionServiceMissingClientAuth | ErrorKind::OnionServiceWrongClientAuth => ERR_CLIENT_AUTH,
        _ => ERR_FAILED,
    }
}

/// Open one stream to `onion:port`; returns a file descriptor (one end of
/// a Unix socket pair) the caller owns, or a negative `ERR_*`.
pub fn open(onion: &str, port: u16) -> i32 {
    let guard = match SESSION.lock() {
        Ok(g) => g,
        Err(_) => return ERR_FAILED,
    };
    let Some(s) = guard.as_ref() else { return ERR_NOT_STARTED };
    let client = s.client.clone();
    let handle = s.rt.handle().clone();
    drop(guard);
    let mut prefs = StreamPrefs::new();
    prefs.connect_to_onion_services(BoolOrAuto::Explicit(true));
    let connected = handle.block_on(async {
        tokio::time::timeout(CONNECT_TIMEOUT, client.connect_with_prefs((onion, port), &prefs)).await
    });
    let mut stream = match connected {
        Ok(Ok(st)) => st,
        Ok(Err(e)) => {
            set_error(&e);
            return kind_code(e.kind());
        }
        Err(_) => {
            set_error("the onion didn't answer in time");
            return ERR_TIMEOUT;
        }
    };
    let (ours, theirs) = match std::os::unix::net::UnixStream::pair() {
        Ok(p) => p,
        Err(e) => {
            set_error(e);
            return ERR_FAILED;
        }
    };
    if let Err(e) = ours.set_nonblocking(true) {
        set_error(e);
        return ERR_FAILED;
    }
    handle.spawn(async move {
        if let Ok(mut local) = tokio::net::UnixStream::from_std(ours) {
            let _ = tokio::io::copy_bidirectional(&mut local, &mut stream).await;
        }
    });
    let fd: RawFd = theirs.into_raw_fd();
    fd
}

/// Drop the client and its runtime (open streams end).
pub fn stop() {
    let taken = SESSION.lock().ok().and_then(|mut s| s.take());
    if let Some(s) = taken {
        drop(s.client);
        s.rt.shutdown_timeout(Duration::from_secs(2));
    }
}

fn guarded(f: impl FnOnce() -> i32) -> i32 {
    match catch_unwind(AssertUnwindSafe(f)) {
        Ok(v) => v,
        Err(_) => {
            set_error("panic in Tor");
            ERR_PANIC
        }
    }
}

// ── C ABI (the iPhone app) ────────────────────────────────────────────────

unsafe fn c_str<'a>(p: *const std::ffi::c_char) -> Option<&'a str> {
    if p.is_null() {
        None
    } else {
        std::ffi::CStr::from_ptr(p).to_str().ok()
    }
}

/// `trusted_prefix` may be null.
///
/// # Safety
/// Each pointer is null or a NUL-terminated UTF-8 string.
#[no_mangle]
pub unsafe extern "C" fn dtor_start(
    state: *const std::ffi::c_char,
    cache: *const std::ffi::c_char,
    auth: *const std::ffi::c_char,
    trusted_prefix: *const std::ffi::c_char,
) -> i32 {
    guarded(|| match (c_str(state), c_str(cache), c_str(auth)) {
        (Some(s), Some(c), Some(a)) => start(Path::new(s), Path::new(c), Path::new(a), c_str(trusted_prefix).map(Path::new)),
        _ => ERR_FAILED,
    })
}

/// # Safety
/// `onion` is a NUL-terminated UTF-8 string.
#[no_mangle]
pub unsafe extern "C" fn dtor_open(onion: *const std::ffi::c_char, port: u16) -> i32 {
    guarded(|| match c_str(onion) {
        Some(o) => open(o, port),
        None => ERR_FAILED,
    })
}

#[no_mangle]
pub extern "C" fn dtor_stop() {
    let _ = guarded(|| {
        stop();
        OK
    });
}

// ── JNI (the Android app: com.domovoi.app.away.ArtiTor) ───────────────────

#[cfg(target_os = "android")]
mod android {
    use super::*;
    use jni::objects::{JClass, JString};
    use jni::sys::{jint, jstring};
    use jni::JNIEnv;

    fn string(env: &mut JNIEnv, s: &JString) -> Option<String> {
        if s.is_null() {
            return None;
        }
        env.get_string(s).ok().map(|j| j.into())
    }

    #[no_mangle]
    pub extern "system" fn Java_com_domovoi_app_away_ArtiTor_nativeStart(
        mut env: JNIEnv,
        _class: JClass,
        state: JString,
        cache: JString,
        auth: JString,
        trusted_prefix: JString,
    ) -> jint {
        let (s, c, a, p) = (string(&mut env, &state), string(&mut env, &cache), string(&mut env, &auth), string(&mut env, &trusted_prefix));
        guarded(|| match (s, c, a) {
            (Some(s), Some(c), Some(a)) => start(Path::new(&s), Path::new(&c), Path::new(&a), p.as_deref().map(Path::new)),
            _ => ERR_FAILED,
        })
    }

    #[no_mangle]
    pub extern "system" fn Java_com_domovoi_app_away_ArtiTor_nativeOpen(mut env: JNIEnv, _class: JClass, onion: JString, port: jint) -> jint {
        let o = string(&mut env, &onion);
        guarded(|| match (o, u16::try_from(port)) {
            (Some(o), Ok(p)) => open(&o, p),
            _ => ERR_FAILED,
        })
    }

    #[no_mangle]
    pub extern "system" fn Java_com_domovoi_app_away_ArtiTor_nativeStop(_env: JNIEnv, _class: JClass) {
        let _ = guarded(|| {
            stop();
            OK
        });
    }

    #[no_mangle]
    pub extern "system" fn Java_com_domovoi_app_away_ArtiTor_nativeLastError(env: JNIEnv, _class: JClass) -> jstring {
        env.new_string(last_error()).map(|s| s.into_raw()).unwrap_or(std::ptr::null_mut())
    }
}
