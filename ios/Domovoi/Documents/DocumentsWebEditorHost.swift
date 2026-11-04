import DomovoiKit
import SwiftUI
import UIKit
import WebKit

/// The household's formatted documents (`.html`, `.htm`) and Word files
/// (`.docx`), in the **dashboard's own editor page**, hosted in a
/// `WKWebView`.
///
/// `android/.../ui/screens/documents/WebEditorHost.kt`, whose own header
/// (`:120-141`) is the design: "fonts, pictures, co-editing, formulas,
/// autosave, the 'someone else is editing' notice and the stale-save choice
/// all come with the page". Owner answer 21 asked for it on the iPhone too.
///
/// ### Why the page and not a native editor
///
/// Entry 21's own words, which are the reason this file is a host and not an
/// editor: a native one "would have to round-trip the server's sanitised HTML
/// through `AttributedString` and would quietly drop what it cannot
/// represent, which is the no-misleading-controls rule in a place where the
/// loss is invisible until somebody reopens the file on a laptop." A `.docx`
/// holds tables, pictures, styles, comments and tracked changes;
/// `AttributedString` holds a small subset of them, and the dropping would be
/// silent, permanent and discovered elsewhere. So the editor that already
/// handles all of it — `web/static/doc_editor.jsx`, the one the dashboard
/// uses — is the editor this app opens, which is also how the two stay the
/// same editor as it changes.
///
/// ### What is handed over, and what is not
///
/// The household token, once, exactly as Android hands it
/// (`WebEditorHost.kt:128-141`):
///
/// * **only after the active server has proved itself on this network.**
///   ``DomovoiClient/tokenForDownload(_:)`` is the rule — the active server
///   only, not room mode, not a refused token, nothing while the server says
///   wait, the identity gate consulted — and its doc comment
///   (`DomovoiClient.swift:806-831`) says the caller must
///   `await prepareIdentity(forAddress:)` first, which
///   ``DocumentsWebEditorModel/prepare(in:)`` does. Nothing, or a throw, and
///   the editor **does not open at all**.
/// * **only to that server's own origin.** Two places, because iOS gives two
///   and Android gives three — see "The one real gap" below.
/// * **once**, into the page's memory, through
///   ``DocumentsWebEditorModel/bridgeScript(token:deviceID:dark:)``: the
///   bridge answers `deviceToken()` a single time and empty after that, as
///   Android's `AtomicReference.getAndSet(null)` does. The page keeps it in
///   memory only and never in storage (`doc_page.jsx:33-40`), and this host's
///   data store is non-persistent, so nothing it kept outlives the editor
///   either way.
///
/// **Never in the URL.** `doc_page.jsx:42` accepts a `?device_token=` for a
/// browser that holds no token of its own; that branch is exactly what this
/// app must not take, and ``DocumentsPath/documentPage(rel:)`` says so. The
/// one place in this app that puts a token in a URL is ``VideoStream``, which
/// has no header-carrying seam at all.
///
/// ### The one real gap, said plainly
///
/// Android re-checks **every request the page makes**
/// (`EditorWebClient.shouldInterceptRequest`, `WebEditorHost.kt:290-299`):
/// not the active server, plain http where the policy refuses it, or a server
/// that no longer proves itself is answered with a 403 and never leaves the
/// phone. `WKWebView` has no equivalent hook. `WKURLSchemeHandler` serves
/// custom schemes only and may not take `http`/`https`, and there is no
/// `shouldInterceptRequest`: a subresource request from the page cannot be
/// seen, let alone refused.
///
/// So the rule is kept at the two seams iOS does give, and the difference is
/// a difference and not a translation:
///
/// 1. **Navigation** is refused off-origin —
///    ``DocumentsWebEditorCoordinator/webView(_:decidePolicyFor:decisionHandler:)``
///    is `shouldOverrideUrlLoading` (`:283-288`), and it covers sub-frames as
///    well as the main one. **The main frame takes http(s) on this server and
///    nothing else**, exactly as Android's does: a `data:` or `blob:`
///    document is refused there and allowed only in a sub-frame, which is the
///    seam `:293` is about. A refused link also *says so*
///    (``DocumentsStrings/editorStayedHere``), where Android's returns true
///    and leaves the tap looking broken.
/// 2. **The session ends** the moment the condition that admitted it stops
///    holding: the active server changes, the pinned identity does, or the
///    server refuses the token this phone holds (an admin blocking this
///    device while the editor is open is that third one, and round 2's
///    review found it going unnoticed).
///    Android refuses the next request; this refuses every further request at
///    once, by taking the page down and saying why
///    (``DocumentsWebEditorModel/recheck(_:)``). For the case the intercept
///    was written about — "a different house is on that address now" — that
///    is the stricter answer. For a page that is already the household's own
///    page fetching its own origin, there is nothing else the intercept was
///    stopping. A proof that has merely **not come back yet** (`.unproven`)
///    is not one of the conditions, and ``DocumentsWebEditorModel/recheck(_:)``
///    says at length why not: ending the session there costs unsaved text
///    for a probe that is still in flight.
///
/// Three things of Android's `lockDown` (`:263-274`) need nothing here:
/// `file:`/`content:` access is not something a remote page can ask
/// `WKWebView` for, mixed content is blocked by default, and there is no
/// Safe Browsing to turn off — so none of the addresses this opens is sent
/// to Google, which is what that line was about.
///
/// ### What is not, and why there is no control for it
///
/// * **Nothing, in fact — and the picture button is the correction.** This
///   paragraph used to say "no photo picker", because Android's
///   `onShowFileChooser` (`:230-245`) hands its WebView the phone's gallery
///   and nothing here ports it, and because the usage string was said to be
///   missing from `project.yml`. Round 2's review (S1) found **both halves
///   wrong**: `WKWebView` needs no delegate at all for an
///   `<input type="file">` — it presents the system picker itself, which is
///   exactly why there is no `onShowFileChooser` to port — and
///   `NSPhotoLibraryUsageDescription` (`project.yml:172`),
///   `NSCameraUsageDescription` (`:168`) and
///   `NSPhotoLibraryAddUsageDescription` (`:178`) have all been in the plist
///   since before this batch. So the page's picture button works on the
///   phone, this port drops no Android capability, and the shared change this
///   unit asked Kamron for was a change already made. **It has no automated
///   case**: a UI case cannot drive the system photo picker, and the first
///   proof will be Kamron's own.
/// * **No Save and no dirty mark in this host's own chrome.** The page owns
///   the document's state and its autosave and draws both itself; a second
///   Save up here would be a button whose state this host cannot know.
///
/// `saveFile` and `print` **are** on the bridge, and this paragraph used to
/// say they were not — that they belonged to the transfers unit's share seam
/// and the Printing unit's sheet, and that a bridge method answering them
/// here would be a misleading control. That argument was the wrong way round
/// and the code was corrected (commit "the editor page's Download menu works
/// on the phone") before this comment was: `deliverFile`
/// (`doc_editor.jsx:1739`) and `printDocument` (`:1714`) fall back to an
/// `<a download>` click and `window.print()`, and a `WKWebView` ignores
/// **both**, silently. The Download menu that does nothing is the misleading
/// control; the two bridge methods are what stop it, and
/// ``DocumentsWebEditorModel/hand(name:mime:base64:)`` and
/// ``DocumentsWebEditorModel/printPage(html:title:)`` say how.
struct DocumentsWebEditorView: View {

    let entry: FileEntry
    let files: FilesModel
    let model: ShellModel
    let onClose: () -> Void

    @State private var host: DocumentsWebEditorModel?
    @Environment(\.colorScheme) private var colorScheme

    var body: some View {
        NavigationStack {
            VStack(alignment: .leading, spacing: 0) {
                DocumentsEditorPill(
                    pill: DocumentsStrings.pillDocument,
                    // The page owns the document's dirty state and its
                    // autosave, so this host has nothing true to say about
                    // either — and a word it could not keep up to date is
                    // worse than no word ([[no-misleading-controls]]).
                    dirty: false,
                    saving: false
                )
                .padding(.horizontal, 16)
                .padding(.top, 8)

                content
            }
            .frame(maxWidth: .infinity, maxHeight: .infinity, alignment: .topLeading)
            .navigationTitle(entry.displayTitle)
            .navigationBarTitleDisplayMode(.inline)
            .toolbar {
                ToolbarItem(placement: .cancellationAction) {
                    Button { host?.closeRequested(onClose) } label: {
                        DocumentsEditorBarLabel(word: DocumentsStrings.close)
                    }
                    .buttonStyle(DocumentsEditorBarButtonStyle())
                }
                // No `save` in the bar, and that is the page's doing rather
                // than an omission: `doc_editor.jsx` saves on its own
                // schedule and draws its own control, so a second one up here
                // would be a button whose state this host could not know.
            }
        }
        .task {
            if host == nil {
                let built = DocumentsWebEditorModel(
                    entry: entry,
                    dark: colorScheme == .dark,
                    onSaved: { files.entries.reload(fresh: true) },
                    onRefusedNavigation: { model.showToast(DocumentsStrings.editorStayedHere) }
                )
                host = built
                await built.prepare(in: model)
            }
        }
        // The second half of the origin rule (see the type's note): the
        // session ends when what admitted it stops being true. `inputs` is
        // `Equatable` and observable, so this fires on a changed server
        // address, on a changed identity and on a changed `trouble` — and on
        // an `.unproven` probe too, which
        // ``DocumentsWebEditorRules/sessionEnd(activeServerURL:pageServerURL:identityChanged:trouble:)``
        // deliberately does nothing about.
        .onChange(of: model.inputs) { _, now in host?.recheck(now) }
        // And on a network change, because the gate's proof is per network
        // and `inputs` can be identical across one.
        .onChange(of: model.networkGeneration) { _, _ in host?.recheck(model.inputs) }
        // Download → Word / Markdown, which the page hands back as bytes
        // (``DocumentsWebEditorModel/hand(name:mime:base64:)``). **One
        // `.sheet` on this view and no other**: several on one view do not
        // reliably coexist in SwiftUI, and `FilesView` is where this app
        // found that out.
        .sheet(item: handingBinding) { file in
            DocumentsWebFileShare(file: file)
        }
    }

    /// The model's `handing` as a binding, so the sheet can clear it on its
    /// own dismissal. A manual `Binding` because the model arrives through
    /// `@State` as an optional — there is nothing to project from until
    /// ``body``'s `task` has built it.
    private var handingBinding: Binding<DocumentsWebEditorFile?> {
        Binding(get: { host?.handing }, set: { host?.handing = $0 })
    }

    @ViewBuilder
    private var content: some View {
        switch host?.state ?? .preparing {
        case .preparing:
            ProgressView()
                .frame(maxWidth: .infinity, maxHeight: .infinity)
                .accessibilityLabel(entry.displayTitle)
        case .refused(let said):
            VStack(alignment: .leading, spacing: 10) {
                Text(said)
                    .font(.subheadline)
                    .fixedSize(horizontal: false, vertical: true)
                if files.offersSave(entry) {
                    // The same fallback the sheet editor offers when the
                    // server will not open a file: the bytes onto the phone,
                    // which is a thing that still works.
                    HomeLink(FileStrings.verbSave) {
                        onClose()
                        files.saveToDevice(entry)
                    }
                }
                Spacer(minLength: 0)
            }
            .padding(16)
            .frame(maxWidth: .infinity, alignment: .leading)
        case .ready(let handoff):
            if let host {
                DocumentsWebEditorHost(handoff: handoff, host: host, onClose: onClose)
                    // The page is the whole editor: it scrolls itself, it
                    // handles every gesture, and it must have the window.
                    .frame(maxWidth: .infinity, maxHeight: .infinity)
                    // **No `accessibilityLabel` here.** The file's name is on
                    // the navigation bar already, and a label on a container
                    // whose children carry their own is how a `WKWebView`
                    // becomes one opaque element: VoiceOver would read the
                    // file name and nothing in the document, and the audit
                    // would measure a rectangle instead of the page. The
                    // `ProgressView` above is labelled because it has nothing
                    // of its own to say.
            }
        }
    }
}

/// A file the editor page built and handed back, on its way to the share
/// sheet — a `.docx` or a `.md` from its Download menu.
///
/// `id` carries the byte count as well as the name: Download → Word twice
/// over, after an edit in between, is two different files with one name, and
/// `.sheet(item:)` rebuilds on a changed id and not otherwise.
struct DocumentsWebEditorFile: Identifiable, Equatable {
    let name: String
    let bytes: Data
    var id: String { "\(name)#\(bytes.count)" }
}

/// The handed-back file, written down and then shared.
///
/// `ChatMoreFileShareSheet` (`ChatMoreSheets.swift:388`) is the same view for
/// the same reason, and the reason is in ``FileShareSheet``: the activity
/// controller takes a **url**, and bytes handed over as `Data` arrive
/// unnamed.
struct DocumentsWebFileShare: View {

    let file: DocumentsWebEditorFile

    @State private var url: URL?

    var body: some View {
        Group {
            if let url {
                FileShareSheet(url: url)
            } else {
                ProgressView()
                    .frame(maxWidth: .infinity, maxHeight: .infinity)
                    .accessibilityLabel(file.name)
            }
        }
        .task {
            guard url == nil else { return }
            let manager = FileManager.default
            let dir = manager.temporaryDirectory
                .appendingPathComponent("docs-share", isDirectory: true)
            try? manager.createDirectory(at: dir, withIntermediateDirectories: true)
            // Emptied first, as Android empties its own share folder
            // (`WebEditorHost.kt:423`): yesterday's copy of somebody's
            // contract has no reason to still be on the phone, and the
            // share sheet has already been handed the one it wanted.
            if let old = try? manager.contentsOfDirectory(at: dir, includingPropertiesForKeys: nil) {
                for one in old { try? manager.removeItem(at: one) }
            }
            let target = dir.appendingPathComponent(file.name)
            guard (try? file.bytes.write(to: target, options: .atomic)) != nil else { return }
            url = target
        }
    }
}

/// What the host needs before it can open the page: the server it is allowed
/// to talk to, the page's address, and the token handed over once.
struct DocumentsWebEditorHandoff: Equatable {
    let base: ServerURL
    let page: URL
    let token: String
    let deviceID: String?
    let dark: Bool
    /// The server address this was minted against, so
    /// ``DocumentsWebEditorModel/recheck(_:)`` can tell a change from a
    /// redraw.
    let serverURL: String
}

/// Getting the page open, keeping it on the house, and taking it down when
/// the house changes — `WebEditorHost.kt`'s `LaunchedEffect`, `BackHandler`
/// and `DisposableEffect`, as a model.
///
/// A model rather than `@State` in the view, for the reason every editor in
/// this item keeps its work off the view: a cover that goes away mid-save
/// must not take the page's own save with it, and a rule that only exists
/// inside a `body` cannot be read by anything.
@MainActor
@Observable
final class DocumentsWebEditorModel {

    enum State: Equatable {
        case preparing
        /// The sentence to draw, in words. Nothing opens.
        case refused(String)
        case ready(DocumentsWebEditorHandoff)
    }

    private(set) var state: State = .preparing

    /// A file the page has handed back, on its way to the share sheet.
    /// Cleared by the sheet's own dismissal.
    var handing: DocumentsWebEditorFile?

    let entry: FileEntry
    private let dark: Bool
    private let onSaved: @MainActor () -> Void
    let onRefusedNavigation: @MainActor () -> Void

    /// The live `WKWebView`, so Close can ask the page to save first and a
    /// recheck can stop it loading. `weak`: the view hierarchy owns it, and a
    /// model that outlived a dismissed cover must not keep a web process
    /// alive with it.
    weak var webView: WKWebView?

    /// Whether the token has already left the bridge. The rule itself is
    /// ``DocumentsWebEditorRules/OneShotToken``, in DomovoiKit, so that a
    /// package test can hold it: everything in this file is compiled by a
    /// train and by nothing else.
    private(set) var oneShot = DocumentsWebEditorRules.OneShotToken()

    init(
        entry: FileEntry,
        dark: Bool,
        onSaved: @MainActor @escaping () -> Void,
        onRefusedNavigation: @MainActor @escaping () -> Void
    ) {
        self.entry = entry
        self.dark = dark
        self.onSaved = onSaved
        self.onRefusedNavigation = onRefusedNavigation
    }

    // MARK: - opening

    /// `WebEditorHost.kt:153-171`, step for step.
    ///
    /// The order matters and is Android's: the address first, the cleartext
    /// rule second, the token last — so a phone on a public address over
    /// plain http is refused before anything is asked about its token.
    func prepare(in model: ShellModel) async {
        let client = model.container.client
        let address = model.container.store.serverURL
        guard let base = TokenScope.baseOf(address) else {
            state = .refused(DocumentsStrings.editorNoServer)
            return
        }
        guard CleartextPolicy.permits(base) else {
            state = .refused(CleartextPolicy.refusalMessage(base.host))
            return
        }
        guard let page = URL(string: client.absolute(DocumentsPath.documentPage(rel: entry.rel))) else {
            state = .refused(DocumentsStrings.editorNoServer)
            return
        }
        // The proof BEFORE the synchronous question, which
        // `tokenForDownload`'s own doc comment requires: without it the first
        // call after a network change throws `identityHeld`, the right answer
        // to "is this proved?" and the wrong one to "may this open?".
        await client.prepareIdentity(forAddress: address, wsUpgrade: false)
        // Captured before the await and compared after it: a cover dismissed
        // and reopened on another server while this was in flight must not
        // get the first server's handoff ([[read-after-await-wrong-key]]).
        //
        // It is a **refusal and not a bare return**: `prepare(in:)` is called
        // once, from the view's `task`, so leaving `state` at `.preparing`
        // leaves a spinner nothing can clear — ``recheck(_:)`` only acts on
        // `.ready`. The sentence is the one ``recheck(_:)`` gives for a server
        // that moved, because that is what happened.
        guard model.container.store.serverURL == address else {
            state = .refused(DocumentsStrings.editorNoServer)
            return
        }
        let token: String?
        do {
            token = try client.tokenForDownload(page)
        } catch {
            // `FilesModel.why(_:)` says exactly this
            // (`FilesModel.swift:606`) and is DomovoiKit-internal, so the app
            // target cannot call it — train C101 failed on that one line. The
            // whole of its body is the two terms here, and the first was
            // already asked, so this is the same answer inline rather than a
            // shared change to `FilesModel.swift`, which this unit does not own.
            state = .refused(DocumentsStrings.editorNotReached((error as? DomovoiError)?.message
                ?? error.localizedDescription))
            return
        }
        guard let token, !token.isEmpty else {
            state = .refused(DocumentsStrings.editorNotPaired)
            return
        }
        state = .ready(DocumentsWebEditorHandoff(
            base: base,
            page: page,
            token: token,
            deviceID: model.deviceID,
            dark: dark,
            serverURL: address
        ))
    }

    // MARK: - the bridge

    /// `window.DomovoiAppBridge`, injected at document start into the main
    /// frame only — `EditorBridge` (`WebEditorHost.kt:312-380`).
    ///
    /// ### Why it is an injected script and not a message handler
    ///
    /// `doc_page.jsx:36` calls `app.deviceToken()` and uses the **return
    /// value**, synchronously, before the editor renders. Android's
    /// `@JavascriptInterface` is synchronous; `WKScriptMessageHandler` is
    /// not — a `postMessage` answers a `Promise`, so a bridge built on one
    /// would hand the page `undefined` and the editor would load with no
    /// token and 401 on its first read. So the three values the page *reads*
    /// are injected, and the one thing it *asks for* — `close()` — is the
    /// message handler, because nothing waits on its answer.
    ///
    /// `deviceToken()` answers **once**, and once per *host* rather than once
    /// per document — which is Android's `AtomicReference.getAndSet(null)`
    /// (`:328`), one reference living beside the WebView for as long as it
    /// does. The closure's `t = ''` is only half of that: a `WKUserScript`
    /// re-runs `.atDocumentStart` for **every** main-frame document, with a
    /// fresh `var t`, so a reload or any same-origin navigation would hand the
    /// token over again and `location.reload()` would be the whole of the
    /// defeat. (It did, until this round's review.)
    ///
    /// So the hand-over is recorded on the host: a token that actually left
    /// the bridge posts `tokenTaken`, and
    /// ``DocumentsWebEditorModel/tokenWasHandedOver()`` swaps the injected
    /// script for a token-less one, which every later document gets instead.
    /// A token the page never read is still there for the next document, as
    /// Android's un-taken `AtomicReference` is.
    ///
    /// Every value goes in as a JSON literal rather than a quoted string: a
    /// token is opaque bytes from the server, and building JavaScript by
    /// concatenating one is how an injected script becomes an injection.
    static func bridgeScript(token: String, deviceID: String?, dark: Bool) -> String {
        """
        (function () {
          var t = \(jsString(token));
          window.DomovoiAppBridge = {
            deviceToken: function () {
              var v = t; t = '';
              if (v) { post({ name: 'tokenTaken' }); }
              return v;
            },
            deviceId: function () { return \(jsString(deviceID ?? "")); },
            darkTheme: function () { return \(dark ? "true" : "false"); },
            close: function () { post({ name: 'close' }); },
            saveFile: function (name, mime, base64) {
              if (!base64 || base64.length > \(Self.maxShareBase64)) { return; }
              post({ name: 'saveFile', file: name, mime: mime, data: base64 });
            },
            print: function (html, title) {
              if (!html || html.length > \(Self.maxPrintCharacters)) { return; }
              post({ name: 'print', html: html, title: title });
            }
          };
          function post(what) {
            try { window.webkit.messageHandlers.\(Self.bridgeName).postMessage(what); }
            catch (e) { /* no host: the page falls back to the browser's way */ }
          }
        })();
        """
    }

    /// `MAX_SHARE_BYTES` (`WebEditorHost.kt:378`) and the base64 length that
    /// carries it, which is the length the bridge actually sees — Android
    /// checks the same arithmetic on the same side (`:337`).
    static let maxShareBytes = 64 * 1024 * 1024
    static let maxShareBase64 = maxShareBytes / 3 * 4 + 4
    /// `MAX_PRINT_CHARS` (`:379`).
    static let maxPrintCharacters = 48 * 1024 * 1024

    /// The page read the token: from here on every document this view loads
    /// gets a bridge with an empty one.
    ///
    /// This is the host-side half of the one-shot — Android's
    /// `AtomicReference` survives a navigation and an injected `var t` does
    /// not. The bridge is still injected, and still answers `deviceId()`,
    /// `darkTheme()`, `close()`, `saveFile` and `print`: a reloaded page that
    /// lost its Download menu as well would be a second bug, and `doc_page.jsx`
    /// reads an empty token as "not paired", which is the truth.
    ///
    /// `webView.configuration` hands back a copy of the configuration but the
    /// **same** `userContentController` object, which is what
    /// ``DocumentsWebEditorHost/dismantleUIView(_:coordinator:)`` already
    /// relies on to break the handler's retain cycle.
    ///
    /// What goes into the replacement script is
    /// ``DocumentsWebEditorRules/OneShotToken/toInject(_:)``'s answer and not
    /// a literal `""`, so the rule the package test holds is the rule this
    /// line obeys.
    func tokenWasHandedOver() {
        guard case .ready(let handoff) = state,
              let controller = webView?.configuration.userContentController else { return }
        oneShot.wasTaken()
        controller.removeAllUserScripts()
        controller.addUserScript(WKUserScript(
            source: Self.bridgeScript(
                token: oneShot.toInject(handoff.token),
                deviceID: handoff.deviceID,
                dark: handoff.dark
            ),
            injectionTime: .atDocumentStart,
            forMainFrameOnly: true
        ))
    }

    // MARK: - the page handing something back

    /// `saveFile(name, mime, base64)` (`:335`) — Download → Word or Markdown.
    ///
    /// **This is not an extra.** `deliverFile` (`doc_editor.jsx:1739`) uses
    /// the bridge when `typeof host.saveFile === 'function'` and otherwise
    /// clicks an `<a download>`; in a `WKWebView` with no
    /// `WKDownloadDelegate` that click does nothing at all, silently. So a
    /// bridge without this method is a Download menu that looks like it
    /// works — [[no-misleading-controls]] in the page this app chose to
    /// host, which is this app's problem whoever drew the button.
    ///
    /// The bytes land in a file because that is what a share sheet takes
    /// (``FileShareSheet``, `FileViewers.swift:212`): handed `Data`, every
    /// activity gets an unnamed blob, and the name is the point.
    func hand(name: String, mime: String, base64: String) {
        guard base64.count <= Self.maxShareBase64,
              let bytes = Data(base64Encoded: base64, options: .ignoreUnknownCharacters),
              !bytes.isEmpty else { return }
        handing = DocumentsWebEditorFile(name: Self.shareName(name, mime: mime), bytes: bytes)
    }

    /// `print(html, title)` (`:344`) — Download → PDF.
    ///
    /// Android hands the page to the print service; iOS has
    /// `UIPrintInteractionController`, whose sheet offers AirPrint **and**
    /// Save to Files, which is where the PDF comes from. `printDocument`
    /// (`doc_editor.jsx:1714`) falls back to `window.print()` in a hidden
    /// iframe, and `window.print()` is a no-op in a `WKWebView` — the same
    /// silent nothing as the download, for the same reason.
    ///
    /// The html is the **page the editor built** (`printablePage`), already
    /// styled and with its pictures in it, so a markup formatter is the right
    /// one: it lays out exactly that document rather than re-rendering the
    /// view.
    ///
    /// ### Two presentations, because the app ships for both devices
    ///
    /// `present(animated:completionHandler:)` is the **iPhone-only** form: on
    /// an iPad it returns false and nothing appears, which is Download → PDF
    /// silently doing nothing on a device `project.yml:54` builds for
    /// (`TARGETED_DEVICE_FAMILY: "1,2"`). `isPrintingAvailable` does not catch
    /// it — printing *is* available on an iPad — so the idiom is asked
    /// directly and the popover form is used there. This was the
    /// control-that-cannot-do-what-it-says case in the very method written to
    /// avoid one.
    ///
    /// Presented from the middle of the key window: the control is the page's
    /// own menu item inside a `WKWebView` and this host does not know where it
    /// was drawn, so there is no anchor to borrow. With no key window at all —
    /// a scene mid-transition — nothing is presented, as
    /// ``PhonePhotosSource/chooseMore()`` does for the same reason.
    func printPage(html: String, title: String) {
        guard !html.isEmpty, html.count <= Self.maxPrintCharacters else { return }
        // Belt and braces: the framework is always there on iOS, and a
        // version of it that says no is a version where there is nothing to
        // present rather than something to apologise for.
        guard UIPrintInteractionController.isPrintingAvailable else { return }
        let info = UIPrintInfo.printInfo()
        info.outputType = .general
        info.jobName = Self.shareName(title, mime: "")
        let controller = UIPrintInteractionController.shared
        controller.printInfo = info
        controller.printFormatter = UIMarkupTextPrintFormatter(markupText: html)
        guard UIDevice.current.userInterfaceIdiom == .pad else {
            _ = controller.present(animated: true, completionHandler: nil)
            return
        }
        guard let window = UIApplication.shared.connectedScenes
            .compactMap({ $0 as? UIWindowScene })
            .flatMap(\.windows)
            .first(where: \.isKeyWindow) else { return }
        let middle = CGRect(x: window.bounds.midX, y: window.bounds.midY, width: 1, height: 1)
        _ = controller.present(from: middle, in: window, animated: true, completionHandler: nil)
    }

    /// `safeFileName` (`:416`) with `shareMime` (`:410`) folded in.
    ///
    /// The extension does the work `mime` does on Android: a share sheet has
    /// no MIME type of its own — every activity reads the file's own
    /// extension — so a name without one gets the one the page asked for.
    static func shareName(_ name: String, mime: String) -> String {
        let refused: Set<Character> = ["\\", "/", ":", "*", "?", "\"", "<", ">", "|"]
        var cleaned = String(name.map { (character: Character) -> Character in
            let control = (character.unicodeScalars.first?.value ?? 32) <= 0x1f
            return control || refused.contains(character) ? "_" : character
        })
        cleaned = cleaned.trimmingCharacters(in: .whitespacesAndNewlines)
        while cleaned.hasPrefix(".") { cleaned.removeFirst() }
        cleaned = String(cleaned.prefix(120))
        if cleaned.isEmpty { cleaned = "Document" }
        guard !mime.isEmpty, !cleaned.contains(".") else { return cleaned }
        return cleaned + (mime == "text/markdown" ? ".md" : ".docx")
    }

    /// A JavaScript string literal for `text`, through `JSONSerialization` so
    /// the escaping is a library's and not a guess. `JSONSerialization`
    /// cannot encode a bare string on its own, so it goes in an array and the
    /// brackets come off — and a failure answers `""`, which makes the page
    /// unpaired rather than the script malformed.
    static func jsString(_ text: String) -> String {
        guard let data = try? JSONSerialization.data(withJSONObject: [text]),
              let wrapped = String(data: data, encoding: .utf8),
              wrapped.count >= 2 else { return "\"\"" }
        return String(wrapped.dropFirst().dropLast())
    }

    static let bridgeName = "domovoiEditor"

    // MARK: - closing

    /// Close, the way Android's Back does (`WebEditorHost.kt:180-187`): ask
    /// the page to take the close — it saves what is pending first — and only
    /// close here if it says it did not.
    ///
    /// `window.DomovoiDocPage.back()` answers `1` when the page took over.
    /// The script is `WebEditorKind.Document.backScript` (`:82`) verbatim,
    /// because the page's side of this contract is the page's.
    func closeRequested(_ close: @escaping () -> Void) {
        guard let view = webView, case .ready = state else {
            close()
            return
        }
        // The page is what reloads the folder: an autosave can land at any
        // time while the editor is open, so the row's size and time are
        // re-read on the way out rather than per save.
        onSaved()
        Task { @MainActor in
            // The `async` form rather than the completion handler: the
            // handler's closure is `@Sendable` in the iOS 18 SDK and `close`
            // is not, and the one known trap of the `async` form — it was
            // written to return a non-optional and traps on a JavaScript
            // `undefined` — cannot fire here, because Android's script
            // (ported verbatim) returns `1` or `0` on every path.
            let answer = try? await view.evaluateJavaScript(Self.backScript)
            // A JavaScript `1` arrives as an `NSNumber`; Android compares the
            // JSON text `"1"`. Both shapes are accepted, and anything else —
            // a page that threw, which is a thrown error here — means the
            // page did not take the close.
            let took = (answer as? NSNumber)?.intValue == 1 || (answer as? String) == "1"
            if !took { close() }
        }
    }

    static let backScript =
        "(function(){if(window.DomovoiDocPage){window.DomovoiDocPage.back();return 1}return 0})()"

    // MARK: - the session's own conditions

    /// End the session when what admitted it stops holding — the second half
    /// of Android's per-request re-check, which `WKWebView` gives no hook
    /// for. See the view's note.
    ///
    /// **Which conditions end it is ``DocumentsWebEditorRules/sessionEnd(activeServerURL:pageServerURL:identityChanged:trouble:)``,
    /// in DomovoiKit**, with the argument for each of the three it answers
    /// and for the two it deliberately does not (`.unproven`, and a wait the
    /// server has asked for). The three it answers: the active server moved,
    /// the identity changed (`.mismatch`), or the server refused the token
    /// this phone holds.
    ///
    /// Round 2's review (S2) found this guard reading
    /// `moved || inputs.identityChanged` under a comment claiming that "a
    /// token the server refuses arrives as a `trouble` inside ``inputs``,
    /// which this already watches by equality" — the view does watch, and
    /// `recheck` then dropped the trouble on the floor. An admin blocking
    /// this device mid-edit left a live editor whose every save came back 401
    /// as the page's own error instead of the app's refusal.
    ///
    /// Room mode is still not here and still does not need to be: it is a
    /// different screen, and it cannot begin while this cover is up.
    func recheck(_ inputs: AppContainer.ShellInputs) {
        guard case .ready(let handoff) = state else { return }
        guard let ending = DocumentsWebEditorRules.sessionEnd(
            activeServerURL: inputs.serverURL,
            pageServerURL: handoff.serverURL,
            identityChanged: inputs.identityChanged,
            trouble: inputs.trouble
        ) else { return }
        webView?.stopLoading()
        // Blank it before the sentence replaces it: a page left rendered for
        // a frame is a page that can still be typed into, and its next save
        // would go to whatever is on that address now.
        webView?.loadHTMLString("", baseURL: nil)
        // A refused token gets the same sentence as a changed identity: in
        // both the house will not take what this phone holds, which is what
        // ``DocumentsStrings/editorNotPaired`` says. It is Android's own
        // wording for the state (`WebEditorHost.kt:166`), so there is no new
        // sentence to record.
        state = .refused(
            ending == .serverMoved
                ? DocumentsStrings.editorNoServer
                : DocumentsStrings.editorNotPaired
        )
    }
}

/// The `WKWebView` itself.
///
/// `AndroidView` (`WebEditorHost.kt:213-254`), with the settings that have an
/// iOS equivalent and without the three that do not (see the view's note).
struct DocumentsWebEditorHost: UIViewRepresentable {

    let handoff: DocumentsWebEditorHandoff
    let host: DocumentsWebEditorModel
    let onClose: () -> Void

    func makeCoordinator() -> DocumentsWebEditorCoordinator {
        DocumentsWebEditorCoordinator(host: host, base: handoff.base, onClose: onClose)
    }

    func makeUIView(context: Context) -> WKWebView {
        let configuration = WKWebViewConfiguration()
        // Android wipes `WebStorage` when the editor closes (`:198`). A
        // non-persistent store is the same promise kept earlier: nothing the
        // page writes is ever on disk, so there is nothing to wipe and
        // nothing a later page served from the same address can read.
        configuration.websiteDataStore = .nonPersistent()
        configuration.defaultWebpagePreferences.allowsContentJavaScript = true
        // `setSupportMultipleWindows(false)` and
        // `javaScriptCanOpenWindowsAutomatically = false` (`:268-269`): the
        // editor is one page, and a window this host does not draw is a
        // window with no origin rule on it.
        configuration.preferences.javaScriptCanOpenWindowsAutomatically = false
        configuration.userContentController.addUserScript(WKUserScript(
            // `oneShot.toInject(_:)` and not `handoff.token`: a cover that is
            // re-made (a rotation that rebuilds the representable, say) after
            // the page has already read the token must not mint a second
            // hand-over. The rule is in DomovoiKit, where a package test can
            // break it (``DocumentsWebEditorRules/OneShotToken``).
            source: DocumentsWebEditorModel.bridgeScript(
                token: host.oneShot.toInject(handoff.token),
                deviceID: handoff.deviceID,
                dark: handoff.dark
            ),
            injectionTime: .atDocumentStart,
            // Main frame only, as Android's `addJavascriptInterface` is: an
            // iframe the page embeds has no business holding the token.
            forMainFrameOnly: true
        ))
        configuration.userContentController.add(
            context.coordinator, name: DocumentsWebEditorModel.bridgeName
        )

        let webView = WKWebView(frame: .zero, configuration: configuration)
        webView.navigationDelegate = context.coordinator
        webView.allowsLinkPreview = false
        webView.allowsBackForwardNavigationGestures = false
        webView.isOpaque = false
        webView.backgroundColor = .clear
        webView.scrollView.backgroundColor = .clear
        host.webView = webView

        // The token on the app's **own** request, in the header every other
        // request of this app carries it in — never in the address
        // (``DocumentsPath/documentPage(rel:)``). This is the one request the
        // app makes; everything after it is the page's, with the token the
        // bridge handed over.
        var request = URLRequest(url: handoff.page)
        request.setValue(handoff.token, forHTTPHeaderField: deviceTokenHeader)
        request.setValue("DomovoiApp", forHTTPHeaderField: "X-Requested-With")
        webView.load(request)
        return webView
    }

    func updateUIView(_ webView: WKWebView, context: Context) {
        // Nothing: the page is loaded once, and reloading it on a redraw
        // would throw away whatever the person had typed.
    }

    static func dismantleUIView(_ webView: WKWebView, coordinator: DocumentsWebEditorCoordinator) {
        // `onDispose` (`WebEditorHost.kt:189-200`). The handler is retained
        // by the content controller, so removing it is what breaks the cycle
        // — a web process left alive holding the household token is the thing
        // this is about, not tidiness.
        webView.stopLoading()
        webView.navigationDelegate = nil
        webView.configuration.userContentController.removeScriptMessageHandler(
            forName: DocumentsWebEditorModel.bridgeName
        )
        webView.configuration.userContentController.removeAllUserScripts()
    }
}

/// The origin lock, and the page's one request of the app.
///
/// `EditorWebClient` (`WebEditorHost.kt:281-303`) and `EditorBridge.close`
/// (`:331`).
///
/// **Deliberately not annotated with an actor**, exactly as
/// ``DocumentsTextBody/Coordinator`` is not (`:384-389`): `WKNavigationDelegate`
/// and `WKScriptMessageHandler` are isolated to the main actor in the iOS 18
/// SDK, so these witnesses are inferred onto it, and writing `@MainActor` here
/// as well would be a second claim about an SDK this machine cannot compile
/// against (NIGHT-RULES §5).
final class DocumentsWebEditorCoordinator: NSObject, WKNavigationDelegate, WKScriptMessageHandler {

    private let host: DocumentsWebEditorModel
    private let base: ServerURL
    private let onClose: () -> Void

    init(host: DocumentsWebEditorModel, base: ServerURL, onClose: @escaping () -> Void) {
        self.host = host
        self.base = base
        self.onClose = onClose
    }

    /// `shouldOverrideUrlLoading` (`:283-288`): the editor's own pages and the
    /// server's downloads stay; anything else is refused rather than opened
    /// in here.
    ///
    /// **The rule itself is ``DocumentsWebEditorRules/navigation(to:base:mainFrame:)``,
    /// in DomovoiKit.** It was here, in a file `pkgtest.sh` cannot compile, so
    /// the one thing answer 21 asks for by name — "only to that server's own
    /// origin" — was guarded by nothing but a train (round 2's review, note 1:
    /// the origin lock could not even be sabotaged). What stays here is the
    /// `WebKit` half: pulling the address and the target frame off a
    /// `WKNavigationAction`, which is not a value DomovoiKit can hold, and
    /// turning the answer into a `WKNavigationActionPolicy`.
    ///
    /// `targetFrame?.isMainFrame ?? true` — no target frame is a
    /// `window.open` / `target="_blank"`, which this host draws no view for
    /// (`setSupportMultipleWindows(false)`, `:268`) and which gets the
    /// **strict** rule rather than the lenient one. An off-origin one still
    /// says so, which is what the origin-lock case taps.
    func webView(
        _ webView: WKWebView,
        decidePolicyFor navigationAction: WKNavigationAction,
        decisionHandler: @escaping (WKNavigationActionPolicy) -> Void
    ) {
        switch DocumentsWebEditorRules.navigation(
            to: navigationAction.request.url,
            base: base,
            mainFrame: navigationAction.targetFrame?.isMainFrame ?? true
        ) {
        case .allow:
            decisionHandler(.allow)
        case .refuseQuietly:
            decisionHandler(.cancel)
        case .refuseAndSay:
            decisionHandler(.cancel)
            // Android refuses silently; a tap that does nothing and says
            // nothing reads as a broken app, so this one says it.
            host.onRefusedNavigation()
        }
    }

    /// The four things the page asks the app for: `close()` (`:331`),
    /// `saveFile` (`:335`), `print` (`:344`) and the one message that is this
    /// port's own — `tokenTaken`, which is how the one-shot survives a
    /// navigation (see ``DocumentsWebEditorModel/bridgeScript(token:deviceID:dark:)``).
    ///
    /// Nothing else is accepted. An unknown `name` is a page doing something
    /// this host was not built for, and acting on it would be acting on
    /// whatever the page says — which is the rule for everything that
    /// arrives from inside a web view.
    ///
    /// **The main frame only.** A handler added to the content controller is
    /// reachable from every frame, including the `data:`/`blob:` iframes the
    /// navigation policy allows — and the bridge is deliberately *not*
    /// injected into those (`forMainFrameOnly: true`), because an iframe the
    /// page embeds has no business holding the token. `message.frameInfo.isMainFrame`
    /// is the one property that makes those two halves agree; an earlier
    /// version of this comment said it was "not something this host can pin
    /// to the main frame", which was simply wrong, and left `saveFile`,
    /// `print` and `close` drivable from an embedded frame.
    ///
    /// The arguments are still read defensively and the methods still refuse
    /// what they cannot use (an empty body, an over-long one, bytes that are
    /// not base64): the main frame is the page, and the page is the server's,
    /// not this app's.
    func userContentController(
        _ userContentController: WKUserContentController,
        didReceive message: WKScriptMessage
    ) {
        guard message.name == DocumentsWebEditorModel.bridgeName,
              message.frameInfo.isMainFrame,
              let body = message.body as? [String: Any],
              let name = body["name"] as? String else { return }
        switch name {
        case "close":
            onClose()
        case "tokenTaken":
            host.tokenWasHandedOver()
        case "saveFile":
            host.hand(
                name: (body["file"] as? String) ?? "",
                mime: (body["mime"] as? String) ?? "",
                base64: (body["data"] as? String) ?? ""
            )
        case "print":
            host.printPage(
                html: (body["html"] as? String) ?? "",
                title: (body["title"] as? String) ?? ""
            )
        default:
            return
        }
    }
}
