import DomovoiKit
import SwiftUI

/// The wash a voice notice sits on (`VoiceNoticeBox`'s `background`,
/// `PeopleScreen.kt:195`, `PersonDetail.kt:268`): the warning wash when voice
/// ID is off and under a person's own relearn notice, `sunken` under the
/// household's "learn voices again". The web People page uses the same two.
enum PeopleVoiceNoticeWash {
    case warn
    case sunken
}

/// A voice ID notice (`VoiceNoticeBox`, `PersonDetail.kt:360-392`; the web's
/// `VoiceIdNotice` and `PersonVoiceRelearn`): a mic, the bold line, and the
/// details muted under it. `mic.slash` when voice ID is off.
///
/// **One accessibility element**, as Android merges it
/// (`semantics(mergeDescendants = true)`): VoiceOver reads the title and the
/// body as one stop, and the icon is decoration.
///
/// Both washes are **opaque**, so the muted body has a contrast ratio that can
/// be stated: ``Color/fgMuted`` on ``Color/manualWash(_:)``'s warning wash is
/// 6.85:1 light and 6.74:1 dark, and on ``Color/manualCodeWash`` (`--sunken`)
/// 6.72:1 and 7.77:1 (WCAG's formula, ``manualContrastRatio(_:_:)``); body
/// text needs 4.5:1. The hairline outline is iOS's own: inside Settings the
/// page is `secondarySystemBackground`, where `--sunken` alone is 1.008:1 and
/// cannot be seen (``Color/manualNodeWash`` has that number).
struct PeopleVoiceNoticeView: View {

    let notice: VoiceNotice
    let wash: PeopleVoiceNoticeWash

    var body: some View {
        VStack(alignment: .leading, spacing: 2) {
            HStack(alignment: .firstTextBaseline, spacing: 8) {
                Image(systemName: notice.isOff ? "mic.slash" : "mic")
                    .font(.footnote)
                    .accessibilityHidden(true)
                Text(notice.title)
                    .font(.subheadline.weight(.semibold))
                    .fixedSize(horizontal: false, vertical: true)
                    .multilineTextAlignment(.leading)
            }
            if let detail = notice.body {
                Text(detail)
                    .font(.footnote)
                    .foregroundStyle(Color.fgMuted)
                    .fixedSize(horizontal: false, vertical: true)
                    .multilineTextAlignment(.leading)
            }
        }
        .padding(.horizontal, 14)
        .padding(.vertical, 10)
        .frame(maxWidth: .infinity, alignment: .leading)
        .background(
            RoundedRectangle(cornerRadius: 10)
                .fill(fill)
        )
        .overlay(
            RoundedRectangle(cornerRadius: 10)
                .stroke(outline, lineWidth: 1)
        )
        .accessibilityElement(children: .combine)
    }

    private var fill: Color {
        switch wash {
        case .warn: return Color.manualWash(.warn)
        case .sunken: return Color.manualCodeWash
        }
    }

    private var outline: Color {
        switch wash {
        case .warn: return Color.warnInk.opacity(0.35)
        case .sunken: return Color.fgMuted.opacity(0.25)
        }
    }
}
