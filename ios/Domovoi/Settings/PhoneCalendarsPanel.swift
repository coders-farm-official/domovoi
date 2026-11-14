import DomovoiKit
import SwiftUI
import UIKit

/// Settings → Household → Phone calendars: "Share this phone's calendars
/// with the house" — the port of `PhoneCalendarsPanel.kt` (merge
/// `bf05916b`), with the review's corrections (2026-11-14).
///
/// Off until the person turns it on, and the iPhone's calendar prompt is
/// asked for only then, **after** the disclosure above the switch. Not
/// offered on a shared screen (the section is not even listed there,
/// ``SettingsSection/sharedScreenHiddenKeys``), and before the phone is
/// paired it says so instead of drawing a switch that could do nothing.
///
/// Read-only both ways: nothing is written to the phone's calendars, and
/// nothing signs in to a calendar company.
struct PhoneCalendarsPanel: View {

    let model: ShellModel

    @Environment(PhoneCalendarsBridge.self) private var bridge: PhoneCalendarsBridge?

    /// The person turned it on and iOS said no (or had said no before).
    @State private var denied = false
    @State private var asking = false
    @State private var calendars: [PhoneCalendarInfo]?
    @State private var reload = 0

    private var shared: Bool { model.owners.sharedScreen(for: model.container.store.serverURL) == true }

    var body: some View {
        VStack(alignment: .leading, spacing: 16) {
            if let bridge {
                shareCard(bridge)
                if !shared, bridge.paired, bridge.entry?.enabled == true {
                    calendarsCard(bridge)
                }
            }
        }
        .frame(maxWidth: .infinity, alignment: .leading)
        .task(id: TaskKey(enabled: bridge?.entry?.enabled == true, reload: reload)) {
            guard let bridge, bridge.entry?.enabled == true else {
                calendars = nil
                return
            }
            let reader = bridge.reader
            calendars = await Task.detached { reader.calendars() }.value
        }
        // Back from the phone's Settings: the access may be there now.
        .onReceive(NotificationCenter.default.publisher(for: UIApplication.didBecomeActiveNotification)) { _ in
            if denied, bridge?.reader.allowed() == true { denied = false }
            reload += 1
        }
    }

    private struct TaskKey: Equatable {
        let enabled: Bool
        let reload: Int
    }

    // MARK: - the switch

    @ViewBuilder
    private func shareCard(_ bridge: PhoneCalendarsBridge) -> some View {
        PanelCard(title: CalendarShareStrings.title, sub: "") {
            if shared {
                muted(CalendarShareStrings.sharedScreen)
            } else if !bridge.paired {
                muted(CalendarShareStrings.notPaired)
            } else {
                VStack(alignment: .leading, spacing: 10) {
                    muted(CalendarShareStrings.disclosure(server: model.serverLabel))
                    muted(CalendarShareStrings.privateEvents)
                    muted(CalendarShareStrings.timing)
                    let on = bridge.entry?.enabled == true
                    Toggle(isOn: Binding(
                        get: { on },
                        set: { want in flip(bridge, to: want) }
                    )) {
                        Text(on ? CalendarShareStrings.on : CalendarShareStrings.off)
                            .font(.body)
                    }
                    .accessibilityLabel(CalendarShareStrings.switchLabel)
                    .disabled(asking)
                    .frame(minHeight: 44)
                    if denied && !on {
                        Text(CalendarShareStrings.permissionDenied)
                            .font(.footnote)
                            .foregroundStyle(Color.warnInk)
                            .fixedSize(horizontal: false, vertical: true)
                        Button(CalendarShareStrings.openSettings) {
                            if let url = URL(string: UIApplication.openSettingsURLString) {
                                UIApplication.shared.open(url)
                            }
                        }
                        .frame(minHeight: 44)
                    }
                    if let notice = bridge.entry?.notice {
                        Text(notice)
                            .font(.footnote)
                            .foregroundStyle(Color.warnInk)
                            .fixedSize(horizontal: false, vertical: true)
                    }
                    if let line = CalendarShareStrings.statusLine(bridge.entry, relative: { sent in
                        relTimeMs(sent, nowMs: Int64((Date().timeIntervalSince1970 * 1000).rounded()))
                    }) {
                        Text(line)
                            .font(.footnote)
                            .foregroundStyle(bridge.entry?.lastError != nil ? Color.warnInk : Color.fgMuted)
                            .fixedSize(horizontal: false, vertical: true)
                    }
                }
            }
        }
    }

    private func flip(_ bridge: PhoneCalendarsBridge, to want: Bool) {
        if !want {
            bridge.turnOff()
            model.showToast(CalendarShareStrings.sharingOff)
            return
        }
        asking = true
        Task { @MainActor in
            let ok = await bridge.turnOn()
            asking = false
            denied = !ok
        }
    }

    // MARK: - which calendars

    @ViewBuilder
    private func calendarsCard(_ bridge: PhoneCalendarsBridge) -> some View {
        PanelCard(title: CalendarShareStrings.calendarsTitle, sub: CalendarShareStrings.calendarsSub) {
            if let list = calendars {
                if list.isEmpty {
                    muted(CalendarShareStrings.noCalendars)
                } else {
                    let ticked = Set(bridge.entry?.calendarIDs ?? [])
                    VStack(alignment: .leading, spacing: 4) {
                        ForEach(list, id: \.id) { c in
                            row(c, checked: ticked.contains(c.id)) { want in bridge.tick(c, on: want) }
                        }
                    }
                }
            } else {
                muted(bridge.reader.allowed() ? CalendarShareStrings.reading : CalendarShareStrings.permissionDenied)
            }
        }
    }

    private func row(_ c: PhoneCalendarInfo, checked: Bool, onChange: @escaping (Bool) -> Void) -> some View {
        let label = CalendarShareStrings.label(c)
        return Button {
            onChange(!checked)
        } label: {
            HStack(alignment: .center, spacing: 10) {
                Image(systemName: checked ? "checkmark.square.fill" : "square")
                    .foregroundStyle(checked ? Color.brandInk : Color.fgMuted)
                    .accessibilityHidden(true)
                // The calendar's own colour, as a dot only: the one accent
                // stays amber.
                Circle()
                    .fill(dotColor(c.colorHex))
                    .frame(width: 10, height: 10)
                    .accessibilityHidden(true)
                VStack(alignment: .leading, spacing: 2) {
                    Text(label.name)
                        .font(.body)
                        .foregroundStyle(Color.primary)
                        .fixedSize(horizontal: false, vertical: true)
                    if let account = label.account {
                        Text(account)
                            .font(.footnote)
                            .foregroundStyle(Color.fgMuted)
                            .fixedSize(horizontal: false, vertical: true)
                    }
                }
                Spacer(minLength: 0)
            }
            .frame(minHeight: 44)
            .contentShape(.rect)
        }
        .buttonStyle(.plain)
        .accessibilityElement(children: .combine)
        .accessibilityAddTraits(checked ? [.isButton, .isSelected] : .isButton)
    }

    private func dotColor(_ hex: String?) -> Color {
        guard let argb = homeHexColor(hex) else { return Color.fgMuted }
        return Color(
            red: Double((argb >> 16) & 0xFF) / 255,
            green: Double((argb >> 8) & 0xFF) / 255,
            blue: Double(argb & 0xFF) / 255
        )
    }

    private func muted(_ text: String) -> some View {
        Text(text)
            .font(.footnote)
            .foregroundStyle(Color.fgMuted)
            .fixedSize(horizontal: false, vertical: true)
    }
}
