import DomovoiKit
import SwiftUI

/// One container incident — `IncidentRow` (`ContainersScreen.kt:349`).
///
/// What the guard found, the monospaced line that identifies it, and — on the
/// one holding the freeze — what it is holding back.
///
/// **Controls only with admin.** Acknowledging and clearing are
/// `require_admin_mutation` and `require_admin_security` at both hops
/// (`web/backend/api/containers.py:133`, `:146`). Without admin on this phone
/// the row has none, and ``HealthPanelStrings/adminClearsThese`` under the
/// Containers part says where they are. With it (1.5.0), ``admin`` carries
/// the row's admin half: what was expected and found, who acknowledged or
/// cleared it, and acknowledge or clear where the server offers them.
struct HealthIncidentRowView: View {

    let incident: HealthIncident

    /// ``HealthPanelModel/nowMs``, so `opened 7m ago` moves with the screen's
    /// own thirty-second tick rather than with a clock this view read itself.
    let nowMs: Int64

    /// The admin half, while this phone holds admin and has read it.
    var admin: HealthIncidentAdminRow?

    @State private var clearing = false
    @State private var note = ""

    /// `toneOf(inc)` (`:342`): a closed incident is **grey**, however bad it
    /// was — the dot grades what is happening now, and the meta line says
    /// when it stopped.
    private var tone: HealthTone {
        guard incident.isOpen else { return .idle }
        return incident.isRed ? .err : .warn
    }

    /// `inc.summary ?: inc.label ?: kindWords(inc.kind)` (`:358`) — the
    /// server's own sentence first, because it names the container and what it
    /// did; the kind's two words only when the server sent neither.
    private var headline: String {
        incident.summary ?? incident.label ?? healthKindWords(incident.kind)
    }

    var body: some View {
        HStack(alignment: .firstTextBaseline, spacing: 10) {
            // `live = inc.open` is Android's pulse. ``ToneDot`` records that
            // nothing in this app animates yet and says why; the flag is
            // passed rather than dropped so the day it does, this row is
            // already asking for it.
            ToneDot(tone: tone, live: incident.isOpen)
            VStack(alignment: .leading, spacing: 3) {
                Text(headline)
                    .font(.subheadline)
                    // `fg` when open, `fgMuted` when not (`:361`).
                    .foregroundStyle(incident.isOpen ? Color.primary : Color.fgMuted)
                    .fixedSize(horizontal: false, vertical: true)
                    .multilineTextAlignment(.leading)
                // ``healthIncidentMeta(_:rel:)`` builds the whole line —
                // `#41 · tampering · different image · domovoi-searxng ·
                // opened 7m ago` — so the order, the separators and the
                // `cleared`/`resolved` choice are a package test and not a
                // layout.
                Text(healthIncidentMeta(incident) { relTime($0, nowMs: nowMs) })
                    .font(.footnote.monospaced())
                    .foregroundStyle(Color.fgMuted)
                    .fixedSize(horizontal: false, vertical: true)
                    .multilineTextAlignment(.leading)
                if incident.freezes {
                    // Android's `colors.err`, which as **text** is
                    // ``Color/errInk`` here: `.red` is 3.55:1 on the light
                    // canvas and body text needs 4.5:1.
                    Text(HealthPanelStrings.pausesRiskyChanges)
                        .font(.footnote.monospaced())
                        .foregroundStyle(Color.errInk)
                        .fixedSize(horizontal: false, vertical: true)
                        .multilineTextAlignment(.leading)
                }
            }
            Spacer(minLength: 0)
        }
        // One stop: what happened, which incident it is, and whether it is
        // holding anything back. Three separate stops would read `#41 ·
        // tampering · different image` as a phrase of its own.
        .accessibilityElement(children: .combine)
        // `padding(horizontal = 16.dp, vertical = 12.dp)` (`:351`).
        .padding(.horizontal, 16)
        .padding(.vertical, 12)
        .frame(maxWidth: .infinity, alignment: .leading)
        if let admin {
            HealthIncidentAdminDetails(incident: incident, admin: admin) {
                note = ""
                clearing = true
            }
            .padding(.leading, 36)
            .padding(.trailing, 16)
            .padding(.bottom, 12)
            .alert(HealthIncidentAdminWords.clearTitle, isPresented: $clearing) {
                TextField(HealthIncidentAdminWords.noteLabel, text: Binding(
                    get: { note },
                    set: { note = String($0.prefix(HealthIncidentAdminWords.noteMax)) }
                ))
                Button(HealthIncidentAdminWords.clearConfirm) { admin.onClear(note) }
                Button(HealthIncidentAdminWords.cancel, role: .cancel) {}
            } message: {
                Text(HealthIncidentAdminWords.clearBody)
            }
        }
    }
}

/// What a row needs for its admin half (``HealthPanelView`` makes it).
struct HealthIncidentAdminRow {
    let fields: HealthIncidentAdminFields
    let busy: Bool
    let onAcknowledge: () -> Void
    let onClear: (String) -> Void
}

/// An incident's admin half: expected and found, who acknowledged or cleared
/// it, and acknowledge or clear where the server offers them.
private struct HealthIncidentAdminDetails: View {

    let incident: HealthIncident
    let admin: HealthIncidentAdminRow
    let onClear: () -> Void

    var body: some View {
        VStack(alignment: .leading, spacing: 4) {
            rows(HealthIncidentAdminWords.expected, admin.fields.expected)
            rows(HealthIncidentAdminWords.found, admin.fields.found)
            if let who = HealthIncidentAdminWords.who(admin.fields) {
                mono(who, Color.fgMuted)
            }
            if healthOffersAcknowledge(incident, admin.fields) {
                Button(HealthIncidentAdminWords.acknowledge, action: admin.onAcknowledge)
                    .buttonStyle(InkOutlineButtonStyle())
                    .disabled(admin.busy)
                hint(HealthIncidentAdminWords.acknowledgeHint)
            }
            if healthOffersClear(incident, admin.fields) {
                Button(HealthIncidentAdminWords.clear, action: onClear)
                    .buttonStyle(BrandFillButtonStyle())
                    .disabled(admin.busy)
                hint(HealthIncidentAdminWords.clearHint)
            }
        }
        .frame(maxWidth: .infinity, alignment: .leading)
    }

    @ViewBuilder
    private func rows(_ title: String, _ rows: [HealthDetailRow]) -> some View {
        if !rows.isEmpty {
            mono(title, Color.fgMuted)
            ForEach(rows, id: \.key) { row in
                mono("\(row.key)  \(row.value)", Color.primary)
            }
        }
    }

    private func mono(_ text: String, _ colour: Color) -> some View {
        Text(verbatim: text)
            .font(.footnote.monospaced())
            .foregroundStyle(colour)
            .fixedSize(horizontal: false, vertical: true)
            .multilineTextAlignment(.leading)
    }

    private func hint(_ text: String) -> some View {
        Text(text)
            .font(.footnote)
            .foregroundStyle(Color.fgMuted)
            .fixedSize(horizontal: false, vertical: true)
    }
}
