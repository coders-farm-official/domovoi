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
/// **All seven are plain `api.*` calls in Android** — the household-token
/// device tier that every other screen in this app uses, with no admin
/// session anywhere, which is why this screen is portable at all. Nothing on
/// it is blocked by the app's lack of an admin session; the gaps are platform
/// gaps (DECISIONS).
///
/// **None of these routes can be verified against server code in this
/// repository**: the Image Generation plugin ships separately
/// (`domovoi-plugin-imagegen`) and mounts its own router under
/// `/api/plugins/{slug}` (`web/backend/plugin_host.py:531`);
/// `docs/API_REFERENCE.md:1625-1630` says so and gives no table. Each path
/// below therefore cites the Android line it was transcribed from, and
/// nothing is here that Android does not call. Engine install, start, stop
/// and model downloads have no route in Android's client and get none here:
/// the dashboard's Images page owns them (`ImagesScreen.kt:70-74`).
public enum ImagesPaths {

    /// `ImagesScreen.kt:76`.
    public static let root = "/api/plugins/imagegen"

    /// The one push type all three reads listen for — `ImagesScreen.kt:138`,
    /// `:141`, `:144`. One type, three reads: Android subscribes to nothing
    /// narrower and filters client-side, which is what ``ApiRead`` does
    /// (`ApiRead.swift:248-252`).
    public static let events: Set<String> = ["imagegen.jobs.changed"]

    // MARK: - the three reads

    /// `ImagesScreen.kt:139` → ``ImagesGenState``.
    public static let models = "\(root)/models"

    /// `ImagesScreen.kt:142` → ``ImagesJobList``, whose key is `jobs`.
    public static let jobs = "\(root)/jobs"

    /// `ImagesScreen.kt:145` → ``ImagesHistoryList``, whose key is
    /// `history`. **A different key over the same row type** — see
    /// ``ImagesJobList``.
    public static let history = "\(root)/history"

    // MARK: - the three writes

    /// `ImagesScreen.kt:162`. The reply is ignored; `jobs` is re-read.
    public static let generate = "\(root)/generate"

    /// `ImagesScreen.kt:350`.
    public static func cancel(job id: Int64) -> String { "\(root)/jobs/\(id)/cancel" }

    /// `ImagesScreen.kt:174`.
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
    public static func file(_ name: String) -> String {
        "\(root)/file/\(VideoPath.esc(name))"
    }

    // MARK: - the one body the app sends

    /// `POST …/generate`'s body — `ImagesScreen.kt:162-165`.
    ///
    /// **Exactly two keys.** No width, height, steps or seed: the app never
    /// sends them, the job's `params` come back from the server, and a body
    /// with fields the plugin's route was not read to accept would be
    /// invented server behaviour (NIGHT-RULES §6).
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
