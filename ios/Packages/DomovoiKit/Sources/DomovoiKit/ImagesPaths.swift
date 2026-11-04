import Foundation

/// Every route this screen asks for, and the one body it sends, escaped once,
/// here.
///
/// Seven calls, all of them `/api/plugins/imagegen/…` — `IG_API` at
/// `ImagesScreen.kt:76`. They are a type for ``VideoPath``'s reason, and
/// sharper: a generated file's **name** travels as a path segment, and a
/// screen assembling that string by hand gets it wrong once and then fetches
/// a different file than the one on screen, or none at all.
///
/// ### All seven are now verified against the plugin's own code
///
/// They were transcribed from Android's client, field by field, because the
/// plugin is not in this repository (`plugins/` holds `radio/` only and the
/// router is mounted dynamically at `web/backend/plugin_host.py:531`). On
/// 2026-11-03 Kamron said to check the plugin out beside the app and verify
/// them (answers 6 and 25), and the repository is at
/// `domovoi-project/domovoi-plugin-imagegen`. **Every path, method, body
/// field and response field below carries its `web.py` line.** Nothing was
/// added: no parameter Android does not send, no route Android does not call.
/// The whole pass is written up in
/// `design-notes/ios/batch-2026-11-03/screens/REPORT.md`.
///
/// Engine install, start, stop and model downloads **do** exist on the plugin
/// (`web.py:253-266`, `:453`) and still get no route here: Android's client
/// calls none of them, and the dashboard's Images page owns them
/// (`ImagesScreen.kt:70-74`).
///
/// ### Three of the seven need an admin session, and a phone has none
///
/// This is the one thing verification changed, and it is not a shape.
///
/// The four **reads** — `/models`, `/jobs`, `/history`, `/file/{name}` — are
/// open: no dependency on the route, and `enforce_route_tier` lets a `GET`
/// through unless the endpoint is `@device_endpoint`
/// (`domovoi/webkit.py:347-351`). They work from this app exactly as ported.
///
/// The three **writes** are `dependencies=ADMIN_ONLY`
/// (`web.py:149`, applied at `:339`, `:360`, `:651`), which is
/// `Depends(admin_required)` — and `admin_required` classifies a request with
/// `check_admin_request` (`domovoi/admin_auth.py:1245-1274`), which consults
/// a Bearer session and the dashboard cookie and **never looks at
/// `X-Device-Token`**. `DomovoiClient` sends only `X-Device-Token`
/// (`DeviceToken.swift:15`; grepped — the app holds no admin credential at
/// all, as `AudiobookModels.swift:444` already records for the audiobook
/// delete). So from this phone, and from Android's, `generate`, `cancel` and
/// the history delete answer **401 `admin session required`** on any house
/// whose admin password exists. It is the plugin's stated posture, not an
/// oversight: its module docstring says every mutating route wears the gate
/// (`web.py:15-21`), and it is in every shipped version from 1.0.1 on
/// (1.0.0 had `/generate` ungated).
///
/// The controls are **not removed in this batch** and the three paths stay
/// here. The honest fix is a decision above a builder: device-tier the three
/// mutations in the plugin (a repo this batch must not edit), or take the
/// buttons out of both apps — which is the shape Kamron chose for the
/// identical finding on room drop-in's `start` (answer 5, ledger #22). It is
/// the first item of this unit's REPORT.md and DECISIONS.md, and until it is
/// answered the toast a failed generate already shows carries the server's
/// own words (``ImagesStrings/generateFailed(_:)``).
public enum ImagesPaths {

    /// `ImagesScreen.kt:76`, and the mount the dashboard gives a plugin
    /// router: `/api/plugins/{slug}` with `slug = imagegen`
    /// (`web/backend/plugin_host.py:531`; the slug is the manifest's,
    /// `domovoi-plugin.toml` `[plugin] slug`).
    public static let root = "/api/plugins/imagegen"

    /// The one push type all three reads listen for — `ImagesScreen.kt:138`,
    /// `:141`, `:144`. One type, three reads: Android subscribes to nothing
    /// narrower and filters client-side, which is what ``ApiRead`` does
    /// (`ApiRead.swift:248-252`).
    ///
    /// **Verified**, and in two halves, because the string is assembled by
    /// the server: the plugin declares `realtime_channel = "imagegen.jobs"`
    /// (`domovoi-plugin.toml` `[[realtime]]`, with
    /// `notify_channel = "plugin_imagegen_jobs_changed"` beside it, which is
    /// what `notify_jobs` signals at `web.py:166-170`), and the dashboard
    /// broadcasts a channel as `f"{channel}.changed"`
    /// (`web/backend/realtime.py:375`). So `imagegen.jobs.changed` is the
    /// type on the wire, exactly.
    public static let events: Set<String> = ["imagegen.jobs.changed"]

    // MARK: - the four reads (all open)

    /// `ImagesScreen.kt:139` → ``ImagesGenState``.
    ///
    /// **Verified: `GET` at `web.py:270`, no gate.** It answers
    /// `{**state(), "models": [...]}` (`:288`) — the whole engine state
    /// payload (`web.py:244-250` over the plugin's core `/state`,
    /// `core.py:71`, which is `EngineManager.state_payload()`,
    /// `engine.py:177-205`) with a `models` array alongside it. So
    /// ``ImagesGenState``'s `installed` and `engine_online` are real top-level
    /// fields of this very response, and the array is the curated catalog
    /// with `installed`/`partial`/`default_size` computed per entry
    /// (`:270-288`).
    public static let models = "\(root)/models"

    /// `ImagesScreen.kt:142` → ``ImagesJobList``, whose key is `jobs`.
    ///
    /// **Verified: `GET` at `web.py:322`, no gate, `{"jobs": [...]}` at
    /// `:337`.** The rows are the live queue — `pending`/`running`, plus a
    /// `pull` that finished in the last 30 seconds and a `generate` that
    /// failed or was cancelled in the last 5 minutes (`:326-333`).
    public static let jobs = "\(root)/jobs"

    /// `ImagesScreen.kt:145` → ``ImagesHistoryList``, whose key is
    /// `history`. **A different key over the same row type** — see
    /// ``ImagesJobList``.
    ///
    /// **Verified: `GET` at `web.py:614`, no gate,
    /// `{"history": [...], "more": <Bool>}` at `:649`.** Done generations,
    /// newest by `completed_at`, each one's images existence-checked so a
    /// generation whose files are gone drops out (`:631-648`).
    ///
    /// Two facts the server has and this app does not use, both left as they
    /// are because Android does not use them either:
    /// * **`limit` and `offset`** (`:616-617`), `limit` defaulting to 24 and
    ///   capped at 100. Asked with no parameters, as Android asks it, so this
    ///   is the newest 24 generations and there is no paging.
    /// * **`more`** (`:649`), which is exactly the flag a "show earlier"
    ///   control would need. Adding one would be a screen Android does not
    ///   have; it is logged in DECISIONS rather than built.
    public static let history = "\(root)/history"

    // MARK: - the three writes (every one admin-only on the server)

    /// `ImagesScreen.kt:162`. The reply is ignored; `jobs` is re-read.
    ///
    /// **Verified: `POST` at `web.py:360`, and it is
    /// `dependencies=ADMIN_ONLY`** — see this type's own doc comment. The
    /// reply is `{"job": <row>}` (`:396`), which the app does not read, and
    /// the route 409s for a model that is not installed (`:368`) and 503s
    /// with the engine not reachable (`:373`) — both of which arrive as the
    /// server's own words in ``ImagesStrings/generateFailed(_:)``.
    public static let generate = "\(root)/generate"

    /// `ImagesScreen.kt:350`.
    ///
    /// **Verified: `POST …/jobs/{job_id}/cancel` at `web.py:339`,
    /// `dependencies=ADMIN_ONLY`.** `job_id` is an `int` path parameter and
    /// the row's `id` is a `BIGINT` identity column
    /// (`migrations/V001__imagegen_schema.sql`), which is why `Int64` here
    /// and not `Int`.
    public static func cancel(job id: Int64) -> String { "\(root)/jobs/\(id)/cancel" }

    /// `ImagesScreen.kt:174`.
    ///
    /// **Verified: `DELETE …/history/{job_id}` at `web.py:651`,
    /// `dependencies=ADMIN_ONLY`.** It deletes the row **and the image
    /// files** (`:653-684`) and 404s for an id that is not a `generate` row,
    /// which is what the confirmation's wording is about
    /// (``ImagesStrings/deleteBody(images:)``).
    public static func deleteHistory(job id: Int64) -> String { "\(root)/history/\(id)" }

    // MARK: - the bytes of one generated picture

    /// `filePath(name)` — `ImagesScreen.kt:125`.
    ///
    /// **`Uri.encode` with no allowed set escapes `/` as well**, so the name
    /// is one opaque path segment on the wire. So this uses
    /// ``VideoPath/esc(_:)`` (unreserved characters only) and **not**
    /// ``FilePath/escPath(_:)``, which deliberately lets `/` through for a
    /// route that takes a whole relative path (`FilePath.swift:29-32`).
    /// Getting that the wrong way round is a quiet traversal bug in one
    /// direction and a quiet 404 in the other, which is why the two escapes
    /// are named and tested rather than inlined.
    ///
    /// **Verified: `GET …/file/{name}` at `web.py:699`, no gate**, serving
    /// the file with an image media type from `_IMAGE_TYPES` and a day of
    /// `Cache-Control` (`:706-712`). Two things the verification settles:
    ///
    /// * **The escape is the right one, and it is also belt and braces.** The
    ///   names the plugin writes cannot contain `/` — a generated name is
    ///   `"{stamp}-{slug(prompt)}-{job_id}[-i]{ext}"` (`:430-435`) and
    ///   `_slug` keeps only `[A-Za-z0-9 _-]` and turns spaces into hyphens
    ///   (`:105-107`). So a slash never has to survive the wire; escaping one
    ///   anyway is what keeps a hand-made or future name from reaching the
    ///   route as two segments.
    /// * **A name that escapes the folder 404s rather than serving
    ///   anything** — `_safe_output` resolves against the output directory
    ///   and returns nil for a path outside it (`:751-761`). The containment
    ///   is the server's; this escape is about the *route matching*, not
    ///   about trusting the name.
    ///
    /// One note correcting this app's own comment: ``ImagesTile`` calls this
    /// "a device-tier route". It is **open** — the bytes come through
    /// ``VideoPosterStore`` for the store's own reasons (a lazy row owning no
    /// fetch), not because the request needs the token.
    public static func file(_ name: String) -> String {
        "\(root)/file/\(VideoPath.esc(name))"
    }

    // MARK: - the one body the app sends

    /// `POST …/generate`'s body — `ImagesScreen.kt:162-165`.
    ///
    /// **Exactly two keys.** No width, height, steps or seed: the app never
    /// sends them, and the job's `params` come back from the server.
    ///
    /// **Verified against `GenerateBody` (`web.py:132-139`)**, and the two
    /// keys are the two the route requires: `model` (1-100 characters) and
    /// `prompt` (1-4000). The five it also accepts — `negative` (default
    /// `""`), `width`, `height` (256-2048), `steps` (1-60) and `seed` — are
    /// every one **optional**, and the route fills each from the model's own
    /// workflow when it is absent (`:380-388`, with the seed from
    /// `time.time_ns()`). So this body is complete and the server's defaults
    /// are what a phone gets, which is what Android gets too. They stay out:
    /// Android sends none of them, and a size picker is a screen neither app
    /// has.
    ///
    /// A prompt longer than 4000 characters is a 422 whose `detail` names the
    /// field, which reaches the person through
    /// ``ImagesStrings/generateFailed(_:)`` (Kamron's answer 44: a 422's
    /// field list is not stripped).
    ///
    /// Through `JSONEncoder` rather than string interpolation, because a
    /// prompt is free text: it holds quotation marks, newlines and emoji by
    /// nature, and hand-written JSON is how one of those comes to break the
    /// request.
    public static func generateBody(model: String, prompt: String) -> Data {
        let encoder = JSONEncoder()
        // Deterministic, so a test can say what left the phone byte for byte.
        encoder.outputFormatting = [.sortedKeys]
        return (try? encoder.encode(GenerateBody(model: model, prompt: prompt)))
            ?? Data(#"{"model":"","prompt":""}"#.utf8)
    }

    /// `put("model", m.id)` / `put("prompt", prompt.trim())` — the trimming
    /// is Android's, at `:164`.
    private struct GenerateBody: Encodable {
        let model: String
        let prompt: String

        init(model: String, prompt: String) {
            self.model = model
            self.prompt = prompt.trimmingCharacters(in: .whitespacesAndNewlines)
        }
    }
}
