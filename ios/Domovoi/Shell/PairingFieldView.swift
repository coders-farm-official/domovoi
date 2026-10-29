import DomovoiKit
import SwiftUI

/// The paste-and-pair row, shared by the pairing screen, Settings →
/// Phones & browsers → Household token and the token-trouble card — one
/// component in all three places, as Android's `CheckedPairingField` is.
///
/// Every decision is ``PairingFieldModel``'s: whether the button is live,
/// what the hint says, what the server's answer is called, and the one branch
/// that writes the token. This file is the keyboard, which is the one thing
/// that is genuinely a view's.
///
/// ### The keyboard, which is the point of this file
///
/// A household token an admin chose can carry capitals and punctuation, which
/// a soft keyboard would otherwise "helpfully" rewrite. Android turns off
/// capitalisation and autocorrect and asks for an ASCII keyboard, and so does
/// this: `.textInputAutocapitalization(.never)`,
/// `.autocorrectionDisabled()`, `.keyboardType(.asciiCapable)`.
///
/// **Smart quotes and smart dashes are not turned off, because SwiftUI has no
/// modifier for them.** `UITextInputTraits.smartQuotesType` and
/// `.smartDashesType` are UIKit's and have no `View` counterpart, so turning
/// them off needs a `UIViewRepresentable` around `UITextField` or a global
/// appearance proxy — neither of which this batch is entitled to pick. The
/// map's §5.3 asks for them, so this is a **gap, named in
/// `I1C3-REPORT.md`**, not a judgement that they do not matter.
///
/// What happens meanwhile is worth knowing, because it decides how urgent the
/// gap is: smart punctuation turns `"` into `“` and `--` into `—`, both of
/// which are outside 0x20…0x7E, so ``TokenPasteField/isUsable(_:)`` refuses
/// the paste and the field says `letters, digits, punctuation and spaces
/// only`. The person is stopped with an accurate hint rather than sending a
/// token the server never issued — and a token with no quote or double hyphen
/// in it is unaffected.
///
/// `.textContentType` is deliberately **not** set. `.password` would invite
/// iOS to offer to save the household token to the person's keychain and
/// their iCloud, which is a copy of a household credential nobody asked for.
struct CheckedPairingFieldView: View {

    @State private var field: PairingFieldModel

    init(field: PairingFieldModel) {
        _field = State(initialValue: field)
    }

    var body: some View {
        VStack(alignment: .leading, spacing: 6) {
            HStack(spacing: 8) {
                TextField(ShellStrings.householdTokenField, text: $field.typed)
                    .font(.body.monospaced())
                    .textFieldStyle(.roundedBorder)
                    .textInputAutocapitalization(.never)
                    .autocorrectionDisabled()
                    .keyboardType(.asciiCapable)
                    .disabled(!field.isEnabled)
                    .accessibilityLabel(ShellStrings.householdTokenField)
                Button(field.buttonLabel) {
                    Task { await field.submit() }
                }
                .disabled(!field.canSubmit)
                .buttonStyle(.borderedProminent)
            }
            if let hint = field.hint {
                Text(hint)
                    .font(.caption)
                    .foregroundStyle(Color.fgMuted)
            }
            if let message = field.message {
                Text(message)
                    .font(.footnote)
                    .foregroundStyle(field.messageIsRefusal ? Color.errInk : Color.warnInk)
                    .fixedSize(horizontal: false, vertical: true)
            }
        }
        .onDisappear { field.stop() }
    }
}
