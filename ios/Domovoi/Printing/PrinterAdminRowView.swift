import DomovoiKit
import SwiftUI

/// Rename and Remove on a printer's card, as admin on this phone. The port
/// of `PrinterAdminRow` (`PrinterAdmin.kt`): rename inline with no confirm
/// (as the dashboard), remove after the dashboard's question.
struct PrinterAdminRowView: View {

    let printer: Printer
    let panel: PrintersPanelModel
    let zone: PrinterAdminModel

    @State private var renaming: String?
    @State private var askingRemove = false

    var body: some View {
        Group {
            if let typed = renaming {
                VStack(alignment: .leading, spacing: 6) {
                    TextField(PrinterAdminWords.name, text: Binding(
                        get: { typed },
                        set: { renaming = String($0.prefix(60)) }
                    ))
                    .textFieldStyle(.roundedBorder)
                    .autocorrectionDisabled()
                    .accessibilityLabel(PrinterAdminWords.name)
                    HStack(spacing: 8) {
                        Button(PrinterAdminWords.save) { save(typed) }
                            .buttonStyle(BrandFillButtonStyle())
                        Button(PrinterAdminWords.cancel) { renaming = nil }
                            .buttonStyle(InkOutlineButtonStyle(ink: .primary))
                    }
                }
            } else {
                HStack(spacing: 8) {
                    Button(PrinterAdminWords.rename) { renaming = printer.name }
                        .buttonStyle(InkOutlineButtonStyle())
                    Button(PrinterAdminWords.remove) { askingRemove = true }
                        .buttonStyle(InkOutlineButtonStyle(ink: .errInk))
                }
            }
        }
        .font(.subheadline)
        .disabled(zone.busy)
        .alert(Text(verbatim: PrinterAdminWords.removeTitle(printer.name)), isPresented: $askingRemove) {
            Button(PrinterAdminWords.remove, role: .destructive) { remove() }
            Button(PrinterAdminWords.cancel, role: .cancel) {}
        } message: {
            Text(verbatim: PrinterAdminWords.removeBody(canScan: printer.canScan))
        }
    }

    private func save(_ typed: String) {
        guard let name = printerRenameTo(current: printer.name, draft: typed) else {
            renaming = nil
            return
        }
        Task { @MainActor in
            let out = await zone.rename(printer, to: name)
            if out.ok { renaming = nil }
            panel.adminChanged(said: out.said)
        }
    }

    private func remove() {
        Task { @MainActor in
            let out = await zone.remove(printer)
            panel.adminChanged(said: out.said)
        }
    }
}
