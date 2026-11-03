import DomovoiKit
import SwiftUI

/// One container incident — `IncidentRow` (`ContainersScreen.kt:349`).
///
/// What the guard found, the monospaced line that identifies it, and — on the
/// one holding the freeze — what it is holding back.
///
/// **There is no control on this row, and that is the whole decision.**
/// Acknowledging and clearing are `require_admin_mutation` and
/// `require_admin_security` at both hops (`web/backend/api/containers.py:133`,
/// `:146`); a phone holds a household token, so either button would 401 every
/// time. Android's panel offers neither either, and both say where they are
/// done instead — ``HealthPanelStrings/adminClearsThese``, drawn once under
/// the Containers part rather than once per row.
struct HealthIncidentRowView: View {

    let incident: HealthIncident

    /// ``HealthPanelModel/nowMs``, so `opened 7m ago` moves with the screen's
    /// own thirty-second tick rather than with a clock this view read itself.
    let nowMs: Int64

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
        // `padding(horizontal = 16.dp, vertical = 12.dp)` (`:351`).
        .padding(.horizontal, 16)
        .padding(.vertical, 12)
        .frame(maxWidth: .infinity, alignment: .leading)
        // One stop: what happened, which incident it is, and whether it is
        // holding anything back. Three separate stops would read `#41 ·
        // tampering · different image` as a phrase of its own.
        .accessibilityElement(children: .combine)
    }
}
