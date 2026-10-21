"""A help entry for every error code the dashboard can show.

An error the dashboard shows carries a code: a refusal header
(``X-Domovoi-Refusal: ai-off`` / ``internet-off``), a chat reply's
``stats.error_code``, a picture upload's or a chat voice turn's ``code``,
the plugin installer's ``{error: {code}}``, a music name's ``{code}``. "Ask
Domovoi about this" on an error toast opens help prefilled with that code;
these entries are what help then finds, indexed with the manual
(:func:`domovoi.help_desk.index.error_passages`), each pointing at the
manual page (``piece``) that explains the area.

``domovoi/tests/test_help_desk_coverage.py`` scans the code for every code
those places can send and fails when one has no entry here: a new error
without help text fails the tests.
"""

from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class ErrorHelp:
    title: str
    text: str
    #: The manual piece (``#manual/<piece>``) for the area.
    piece: str


def _e(title: str, text: str, piece: str) -> ErrorHelp:
    return ErrorHelp(title, text, piece)


_PLUGIN_ZIP = (
    " The file isn't a plugin Domovoi can install as it is. Download it again from the "
    "plugin's own page, or ask its author for a fixed release."
)
_PLUGIN_AUTHOR = (
    " This is a problem in the plugin itself, not in your Domovoi: tell the plugin's author, "
    "with the message the dashboard showed."
)
_PLUGIN_RETRY = " Try the install again from Plugins; if it keeps failing, use Report a problem."

ERROR_CODES: dict[str, ErrorHelp] = {
    # ── refusals every page can meet ─────────────────────────────────────
    "ai-off": _e(
        "AI features are turned off",
        "The household turned AI chat and questions off (Settings → Models → AI chat and "
        "questions). Chat and picture reading stay off until an admin turns it on; voice "
        "commands, the manual and its search keep working.",
        "models",
    ),
    "internet-off": _e(
        "The internet is turned off for Domovoi",
        "This Domovoi is set to never use the internet (Settings → Internet), so anything that "
        "would go online is refused. An admin can change the answer to Yes or Sometimes.",
        "internet",
    ),
    # ── chat replies (stats.error_code) ──────────────────────────────────
    "model_missing": _e(
        "The chat model isn't installed",
        "The model that answers chat isn't on this server yet. An admin installs it from "
        "Settings → Models.",
        "models",
    ),
    "model_unreachable": _e(
        "The model service isn't answering",
        "Domovoi couldn't reach Ollama, the service that runs its models. Restarting the server "
        "usually brings it back (Settings → Configuration → Restart).",
        "troubleshooting",
    ),
    "model_timeout": _e(
        "The model took too long",
        "The answer didn't start in time; a model that was asleep can take a minute to load on "
        "a server without a graphics card. Send the message again.",
        "models",
    ),
    "model_memory": _e(
        "Not enough memory for the model",
        "The server ran out of memory loading the model. Close other heavy programs on it, or an "
        "admin can pick a smaller model in Settings → Models.",
        "models",
    ),
    "model_error": _e(
        "The model failed",
        "The model service returned an error for this message. Send it again; if it keeps "
        "happening, check Settings → Models and use Report a problem.",
        "models",
    ),
    "empty_reply": _e(
        "The model gave an empty answer",
        "The model finished without saying anything. Send the message again, or ask it in other "
        "words.",
        "chat",
    ),
    "vision_model_missing": _e(
        "Pictures need the vision model",
        "Reading pictures and screenshots needs the vision model, which isn't installed (or none "
        "is set). An admin adds it in Settings → Models.",
        "models",
    ),
    "vision_unsupported": _e(
        "This model can't read pictures",
        "The model chosen for pictures can't take images. An admin picks a vision model in "
        "Settings → Models.",
        "models",
    ),
    # ── chat picture uploads ─────────────────────────────────────────────
    "unsupported_format": _e(
        "That picture format isn't supported",
        "Chat reads JPEG, PNG, GIF, WebP and BMP pictures. A phone's HEIC photo can be shared as "
        "JPEG instead (most phones offer \"most compatible\").",
        "chat",
    ),
    "too_many_pixels": _e(
        "That picture is too large",
        "The picture has more pixels than chat takes. Send a smaller copy or a screenshot of the "
        "part that matters.",
        "chat",
    ),
    "file_too_large": _e(
        "That file is too large",
        "Chat takes pictures up to 20 MB. Send a smaller copy.",
        "chat",
    ),
    # ── chat documents and try again ─────────────────────────────────────
    "unreadable": _e(
        "That document couldn't be read",
        "Domovoi couldn't read the file you attached: it may be damaged, password-protected, "
        "scanned pages with no text, or a format chat doesn't take (old .doc and .rtf files: "
        "save them as .docx or PDF first). The message says which.",
        "chat",
    ),
    "missing": _e(
        "The attached document is gone",
        "The document a chat was asked about is no longer on this Domovoi (it was removed or the "
        "attachment expired). Attach it again to ask more about it.",
        "chat",
    ),
    "not_found": _e(
        "Domovoi couldn't find that in the document",
        "The passages Domovoi found don't answer the question, so it says so instead of guessing. "
        "Ask with words the document uses, or attach the right file.",
        "chat",
    ),
    "already_retried": _e(
        "That reply is already being tried again",
        "Another try again on the same reply is running, or has already replaced it. Wait for it "
        "to finish; the chat shows the new reply.",
        "chat",
    ),
    # ── the heads-up before shaky results (documents plan D3) ────────────
    "already_answered": _e(
        "That heads-up was answered already",
        "An option was already picked on that card, here or on another device; the answer is under it.",
        "good-and-not",
    ),
    "stale": _e(
        "A newer message followed that heads-up",
        "A card answers only while it is the chat's last message. Ask the question again.",
        "good-and-not",
    ),
    "bad_option": _e(
        "That isn't one of the card's options",
        "Pick one of the buttons on the card. Reload the chat if they look out of date.",
        "good-and-not",
    ),
    "not_a_card": _e(
        "That message is no heads-up card",
        "Only a heads-up card has options to pick. Reload the chat.",
        "good-and-not",
    ),
    # ── the app's chat mic ───────────────────────────────────────────────
    "core_timeout": _e(
        "Domovoi took too long to answer",
        "The voice turn didn't finish in time; a model loading for the first time can be slow. "
        "Try again.",
        "phone-chat",
    ),
    "core_unreachable": _e(
        "Domovoi's voice service isn't answering",
        "The Domovoi server's voice service (the core) isn't answering; it may be restarting. "
        "Wait a minute and try again, or restart the server.",
        "restart",
    ),
    # ── music names ("when I say X I mean Y") ────────────────────────────
    "alias_taken": _e(
        "That name already means something else",
        "The name is already set for another artist, album or song. Replace it, or pick another "
        "name.",
        "music",
    ),
    "empty": _e("The name is empty", "Type or say the name you want to use.", "music"),
    "too_long": _e("The name is too long", "Pick a shorter name.", "music"),
    "nothing_to_say": _e(
        "The name has nothing to say",
        "A name needs letters or numbers that can be spoken.",
        "music",
    ),
    "retry": _e(
        "Someone else changed that name at the same moment",
        "Two changes to the same name met. Try it again.",
        "music",
    ),
    "already_its_name": _e(
        "That's already its name",
        "The library already finds it by that name, so there is nothing to add.",
        "music",
    ),
    # ── the plugin installer ({error: {code}}) ───────────────────────────
    "bad_request": _e("The install request was incomplete",
                      "The dashboard sent an install request the server couldn't use." + _PLUGIN_RETRY, "plugins"),
    "bundled_plugin": _e("That plugin comes with Domovoi",
                         "A bundled plugin (like Radio) updates with Domovoi itself, not from Plugins.", "plugins"),
    "confirm_in_progress": _e("An install is already running",
                              "Another install or upgrade of this plugin is still going. Wait for it to finish.",
                              "plugins"),
    "dev_mode": _e("The plugin is in development mode",
                   "This plugin was registered from a folder for development, so it can't be changed "
                   "from Plugins.", "plugins"),
    "dev_registration": _e("The plugin is a development registration",
                           "A plugin registered from a folder is managed from that folder, not installed "
                           "over.", "plugins"),
    "downgrade_across_migrations": _e(
        "That version is older and changed its data",
        "Going back to that version would leave the plugin's data newer than its code, so Domovoi "
        "won't do it.", "plugins"),
    "downgrade_requires_force": _e("That version is older",
                                   "Installing an older version needs the admin to confirm it.", "plugins"),
    "endpoint_tier_conflict": _e("The plugin's routes disagree",
                                 "The plugin declares a route on two access levels." + _PLUGIN_AUTHOR, "plugins"),
    "github_download_failed": _e("The download from GitHub failed",
                                 "Domovoi couldn't download the plugin's release. Check the internet setting "
                                 "and try again.", "plugins"),
    "github_repo_unavailable": _e("That GitHub page isn't available",
                                  "The repository wasn't found, is private, or has no release to install.",
                                  "plugins"),
    "github_url_invalid": _e("That isn't a GitHub plugin address",
                             "Paste the plugin's GitHub page address (github.com/owner/name).", "plugins"),
    "install_dir_exists": _e("The plugin's folder is already there",
                             "A folder from an earlier install is in the way." + _PLUGIN_RETRY, "plugins"),
    "install_failed": _e("The install failed",
                         "Something went wrong while installing; nothing was left half-installed." + _PLUGIN_RETRY,
                         "plugins"),
    "internet_off": _e("Installing from the internet is turned off",
                       "This Domovoi is set to never use the internet (Settings → Internet). Install the "
                       "plugin from a zip file instead.", "plugins"),
    "layout_invalid": _e("The plugin's files aren't laid out right", "The plugin's folders are wrong." + _PLUGIN_ZIP,
                         "plugins"),
    "lockfile_unreadable": _e("The plugin's requirements list can't be read",
                              "Its requirements.lock is broken." + _PLUGIN_AUTHOR, "plugins"),
    "manifest_invalid": _e("The plugin's manifest is invalid",
                           "Its domovoi-plugin.toml breaks the rules, or targets another Domovoi version."
                           + _PLUGIN_AUTHOR, "plugins"),
    "manifest_missing": _e("The plugin has no manifest",
                           "There's no domovoi-plugin.toml in it." + _PLUGIN_ZIP, "plugins"),
    "migration_drift": _e("The plugin's data changes don't match",
                          "Its database migrations changed after they were installed." + _PLUGIN_AUTHOR, "plugins"),
    "migration_failed": _e("The plugin's data setup failed",
                           "Its database migration failed; the install was undone." + _PLUGIN_AUTHOR, "plugins"),
    "migration_lint": _e("The plugin's data setup isn't allowed",
                         "Its migrations break Domovoi's rules." + _PLUGIN_AUTHOR, "plugins"),
    "not_installed": _e("That plugin isn't installed",
                        "There's nothing by that name to change; reload Plugins.", "plugins"),
    "open_endpoint_scan_failed": _e("The plugin's open routes couldn't be checked",
                                    "Domovoi couldn't check which of its routes need no sign-in." + _PLUGIN_AUTHOR,
                                    "plugins"),
    "orphan_schema": _e("Old plugin data is in the way",
                        "Data from an earlier copy of this plugin is still in the database. An admin removes "
                        "it before installing again.", "plugins"),
    "pip_install_failed": _e("The plugin's requirements didn't install",
                             "Its Python packages failed to install (no internet, or a package that won't "
                             "build here)." + _PLUGIN_RETRY, "plugins"),
    "plugin_file_too_large": _e("A file in the plugin is too large", "One of its files is over the limit." + _PLUGIN_ZIP,
                                "plugins"),
    "requirements_conflict": _e("The plugin needs other versions of Domovoi's packages",
                                "Its requirements clash with what Domovoi uses." + _PLUGIN_AUTHOR, "plugins"),
    "requirements_sdist": _e("The plugin needs a package that would be built here",
                             "Domovoi installs only prebuilt packages." + _PLUGIN_AUTHOR, "plugins"),
    "requirements_unresolvable": _e("The plugin's requirements can't be met",
                                    "No set of packages satisfies them." + _PLUGIN_AUTHOR, "plugins"),
    "slug_exists": _e("A plugin with that name is installed",
                      "Upgrade it instead, or uninstall it first.", "plugins"),
    "staged_id_unknown": _e("The install expired",
                            "The checked plugin waiting for your confirmation is gone (a restart, or too long). "
                            "Start the install again.", "plugins"),
    "staging_modified": _e("The plugin changed while waiting",
                           "Its files changed between the check and the confirm. Start the install again.",
                           "plugins"),
    "upgrade_failed": _e("The upgrade failed",
                         "The upgrade was undone and the old version kept." + _PLUGIN_RETRY, "plugins"),
    "upgrade_missing": _e("There's nothing to upgrade",
                          "That plugin isn't installed, so it can't be upgraded.", "plugins"),
    "upgrade_same_version": _e("That version is already installed",
                               "The file is the version you already have.", "plugins"),
    "upgrade_slug_mismatch": _e("That's a different plugin",
                                "The file you picked is another plugin, not an upgrade of this one.", "plugins"),
    "web_import_hygiene": _e("The plugin's dashboard code imports what it may not",
                             "Its web part reaches into Domovoi's core." + _PLUGIN_AUTHOR, "plugins"),
    "zip_bad_layout": _e("The zip isn't laid out like a plugin", "The folders inside are wrong." + _PLUGIN_ZIP,
                         "plugins"),
    "zip_bad_path": _e("The zip has an unsafe path", "A file inside points outside the plugin." + _PLUGIN_ZIP,
                       "plugins"),
    "zip_case_collision": _e("The zip has two files that differ only in capitals",
                             "They would overwrite each other on Windows." + _PLUGIN_ZIP, "plugins"),
    "zip_invalid": _e("That isn't a zip file Domovoi can read", "The file is damaged or not a zip." + _PLUGIN_ZIP,
                      "plugins"),
    "zip_reserved_name": _e("The zip has a file name Windows reserves",
                            "A name like CON or NUL can't be written on Windows." + _PLUGIN_ZIP, "plugins"),
    "zip_symlink": _e("The zip contains a link", "Plugins may not contain symbolic links." + _PLUGIN_ZIP, "plugins"),
    "zip_too_large": _e("The zip is too large", "It is over the size a plugin may be." + _PLUGIN_ZIP, "plugins"),
    "zip_too_many_entries": _e("The zip has too many files", "It is over the number a plugin may have." + _PLUGIN_ZIP,
                               "plugins"),
}


def lookup(code: str | None) -> ErrorHelp | None:
    return ERROR_CODES.get((code or "").strip())
