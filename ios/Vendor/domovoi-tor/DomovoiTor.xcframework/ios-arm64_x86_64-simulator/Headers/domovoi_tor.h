/*
 * domovoi_tor.h: the C ABI of native/domovoi-tor (away chat's Tor: Arti, an
 * embedded onion client with client authorization, as a byte pipe). The
 * iPhone app calls it through DomovoiTor.xcframework (module DomovoiTorFFI);
 * src/lib.rs is the implementation and says what each call does.
 *
 * Results: >= 0 is "ok" (dtor_start) or a file descriptor the caller owns
 * (dtor_open); a negative value is one of the DTOR_ERR_* codes below. Every
 * call may block (dtor_start for up to two minutes): never on the main thread.
 */
#ifndef DOMOVOI_TOR_H
#define DOMOVOI_TOR_H

#include <stdint.h>

#ifdef __cplusplus
extern "C" {
#endif

#define DTOR_OK 0
/* Something else failed. */
#define DTOR_ERR_FAILED (-1)
/* dtor_open before dtor_start, or after dtor_stop. */
#define DTOR_ERR_NOT_STARTED (-2)
/* The onion's descriptor wasn't found (down, or not published yet). */
#define DTOR_ERR_NOT_FOUND (-3)
/* The onion wants a client-auth key this phone doesn't hold, or holds a wrong one. */
#define DTOR_ERR_CLIENT_AUTH (-4)
/* Bootstrap or the connection took too long. */
#define DTOR_ERR_TIMEOUT (-5)
/* A panic inside Tor (caught). */
#define DTOR_ERR_PANIC (-6)

/*
 * Start a Tor client and bootstrap it (blocking, up to two minutes); one
 * already running is stopped first. `state` and `cache` are kept between
 * sessions; `auth` is the 0700 folder holding `<onion56>.auth_private`
 * (`<onion56>:descriptor:x25519:<base32 private key>`, 0600).
 * `trusted_prefix` may be NULL: Arti checks that nobody else can write the
 * folders, up the whole path, and stops checking at this folder if given.
 */
int32_t dtor_start(const char *state, const char *cache, const char *auth, const char *trusted_prefix);

/* Open one stream to `onion:port`: one end of a Unix socket pair, or DTOR_ERR_*. */
int32_t dtor_open(const char *onion, uint16_t port);

/* Drop the client (its streams end). Harmless when none runs. */
void dtor_stop(void);

#ifdef __cplusplus
}
#endif

#endif /* DOMOVOI_TOR_H */
