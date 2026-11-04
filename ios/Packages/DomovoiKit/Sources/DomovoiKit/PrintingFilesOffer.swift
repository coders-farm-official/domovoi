import Foundation
import Observation

/// What the Files screen needs to know about printers: one read, and two
/// predicates over it.
///
/// `FilesScreen.kt:297` reads `/api/printers` once per Files screen and asks
/// `printOffer(...)` per row (`:779`) and `hasScanner && library ==
/// core:documents && canWrite` for the header's `scan` button (`:302,539`).
///
/// Separate from ``PrintersPanelModel`` on purpose: Files needs a read and
/// two questions, not a panel with a minute timer, a look, an add flow and a
/// per-card job list. Building the panel's model to answer a row menu would
/// have Files making discovery requests nobody asked for.
@MainActor
@Observable
public final class PrintingFilesOffer {

    public let printers: ApiRead<PrintersResponse>

    public init(in container: AppContainer) {
        let io = PrintingIo(in: container)
        self.printers = ApiRead(
            in: container,
            eventTypes: PrintersPanelModel.listEvents
        ) {
            try await io.printers()
        }
    }

    /// Nil until the read answers — which is what makes both predicates
    /// answer "nothing" rather than guess while it has not.
    public var data: PrintersResponse? { printers.data }

    public func refresh() { printers.refresh() }

    public func stop() { printers.stop() }

    /// What one row's menu offers: ``PrintingOffer/print``, the server's
    /// "save it as PDF first" sentence, or nothing at all.
    public func offer(for entry: FileEntry) -> PrintingOffer {
        printingOffer(name: entry.name, isDir: entry.isDir, printers: data)
    }

    /// Whether the header offers `scan`. See
    /// ``printingOffersScan(printers:libraryID:canWrite:)`` for why
    /// `canWrite` is half the gate.
    public func offersScan(libraryID: String?, canWrite: Bool) -> Bool {
        printingOffersScan(printers: data, libraryID: libraryID, canWrite: canWrite)
    }
}
