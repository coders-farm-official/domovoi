import DomovoiKit
import SwiftUI

/// Which add flow is open: a printer the server found, or one somebody is
/// typing the address of.
///
/// `AddPrinterDialog` (`PrintersPanel.kt:488-543`) is one composable with an
/// `address` field that is there or not; here the two are one sheet with one
/// enum, for ``FileSheet``'s reason — several `.sheet` modifiers on one view
/// do not reliably coexist, and the panel already has an `.alert` of its own.
enum PrintingAddSheet: Identifiable {

    case found(PrintingFound)
    case byAddress

    /// The case **and** the printer, so opening the sheet for a second found
    /// row replaces its contents rather than keeping the first row's:
    /// `.sheet(item:)` rebuilds on a changed id and not otherwise.
    var id: String {
        switch self {
        case .found(let row): return "found:\(row.id)"
        case .byAddress: return "address"
        }
    }
}

/// Adding a printer: its address when it has to be typed, a name nobody has
/// to give, and a room it is the default in.
///
/// ### What is not here, and why there is no control for it
///
/// **No "test this address first".** `POST /api/printers` either adds the
/// printer or answers with a sentence saying why it could not
/// (`printers.py:146`), and a separate probe button would be a second request
/// that proves nothing the add does not. The test page offered afterwards is
/// the real check, and it is the one Android offers too.
struct PrintingAddView: View {

    let which: PrintingAddSheet
    let panel: PrintersPanelModel

    @State private var address = ""
    @State private var name = ""
    @State private var roomID: String?

    var body: some View {
        NavigationStack {
            ScrollView {
                VStack(alignment: .leading, spacing: 16) {
                    if typesAddress {
                        field(
                            PrintingStrings.addressLabel,
                            PrintingStrings.addressPlaceholder,
                            text: $address,
                            limit: PrintingStrings.addressFieldLimit,
                            keyboard: .URL
                        )
                    } else if case .found(let row) = which {
                        VStack(alignment: .leading, spacing: 2) {
                            Text(row.address)
                                .font(.footnote.monospaced())
                                .fixedSize(horizontal: false, vertical: true)
                            if !row.location.isEmpty {
                                Text(row.location)
                                    .font(.caption)
                                    .foregroundStyle(Color.fgMuted)
                                    .fixedSize(horizontal: false, vertical: true)
                            }
                        }
                    }
                    field(
                        PrintingStrings.nameLabel,
                        PrintingStrings.namePlaceholder,
                        text: $name,
                        limit: PrintingStrings.nameFieldLimit,
                        keyboard: .default
                    )
                    if !panel.rooms.isEmpty {
                        PrintingMenuRow(
                            title: PrintingStrings.roomLabel,
                            chosen: chosenRoomLabel,
                            options: [
                                PrintingMenuRow.Option(label: PrintingStrings.noRoom) { roomID = nil }
                            ] + panel.rooms.map { room in
                                PrintingMenuRow.Option(label: room.label) { roomID = room.id }
                            }
                        )
                    }
                    Button(PrintingStrings.add) { add() }
                        .buttonStyle(BrandFillButtonStyle())
                        .disabled(!canAdd)
                }
                .padding(16)
                .frame(maxWidth: .infinity, alignment: .leading)
            }
            .navigationTitle(title)
            .navigationBarTitleDisplayMode(.inline)
            .toolbar {
                ToolbarItem(placement: .cancellationAction) {
                    Button(PrintingStrings.cancel) { panel.cancelAdd() }
                }
            }
        }
        // The model clears `addingFound`/`addingByAddress` when the add lands,
        // and the sheet's binding follows that — so there is nothing to
        // dismiss by hand and no second source of truth about whether it is
        // open.
    }

    private var typesAddress: Bool {
        if case .byAddress = which { return true }
        return false
    }

    private var title: String {
        switch which {
        case .byAddress: return PrintingStrings.addByAddressTitle
        case .found(let row): return PrintingStrings.addTitle(for: row.makeAndModel)
        }
    }

    private var chosenRoomLabel: String {
        panel.rooms.first { $0.id == roomID }?.label ?? PrintingStrings.noRoom
    }

    /// A typed add needs an address; a found one already has its `key`.
    private var canAdd: Bool {
        guard panel.busy == nil else { return false }
        return typesAddress
            ? !address.trimmingCharacters(in: .whitespacesAndNewlines).isEmpty
            : true
    }

    private func add() {
        let typedName = name.trimmingCharacters(in: .whitespacesAndNewlines)
        switch which {
        case .byAddress:
            panel.add(
                address: address.trimmingCharacters(in: .whitespacesAndNewlines),
                name: typedName,
                roomID: roomID
            )
        case .found(let row):
            panel.add(row, name: typedName, roomID: roomID)
        }
    }

    /// A single-line field with a border.
    ///
    /// **`axis:` is deliberately not set.** A vertical field accepts a newline
    /// from the Return key, and neither an address nor a printer's name may
    /// carry one; ``PhonesAndBrowsersPanel`` keeps a vertical field for the
    /// server address because a whole URL has to be readable at an
    /// accessibility size, and strips newlines as they arrive — these two are
    /// short enough that `.roundedBorder`'s own 34-point control is both
    /// large enough for the audit and honest about its Return key.
    private func field(
        _ label: String,
        _ placeholder: String,
        text: Binding<String>,
        limit: Int,
        keyboard: UIKeyboardType
    ) -> some View {
        VStack(alignment: .leading, spacing: 4) {
            Text(label)
                .font(.caption)
                .foregroundStyle(Color.fgMuted)
                .fixedSize(horizontal: false, vertical: true)
            TextField(placeholder, text: text)
                .textFieldStyle(.roundedBorder)
                .textInputAutocapitalization(.never)
                .autocorrectionDisabled()
                .keyboardType(keyboard)
                .submitLabel(.done)
                .accessibilityLabel(label)
                .onChange(of: text.wrappedValue) { _, typed in
                    // The field's own cap, as Android's is
                    // (`PrintersPanel.kt:509,519`): a 300-character address
                    // is a paste nobody meant.
                    if typed.count > limit {
                        text.wrappedValue = String(typed.prefix(limit))
                    }
                }
        }
    }
}
