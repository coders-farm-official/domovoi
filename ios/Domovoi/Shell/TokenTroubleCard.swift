import DomovoiKit
import SwiftUI

/// The one card for a server that answered but will not take this phone's
/// household token.
///
/// A port of `android/.../ui/shell/TokenTroubleScreen.kt`'s drawing; every
/// word of it is part 1's ``TokenTrouble``. It is drawn **in place of the
/// whole workspace**, as the pairing screen is, so that no panel behind it
/// keeps asking the server and renewing its own lockout.
///
/// Two things here are decisions and both are in the package:
///
///  - `try the old token again` is offered **only where it can do what it
///    says** (``tokenTroubleOffersOldTokenRetry(_:)``): during a wait nothing
///    is sent, so trying would not try.
///  - `use this phone's media` writes the person's own choice
///    (``ShellModel/chooseThisPhone()``), which is sticky and beats the card.
///
/// The paste field is ``CheckedPairingFieldView``, the same component the
/// pairing screen and Settings use, with `pair again` on its button — which
/// is Android's arrangement: one `CheckedPairingField` in all three places, so
/// the words and the rules cannot drift between them. Part 2 drew a disabled
/// row here instead, because the check and the vault write did not exist yet.
struct TokenTroubleCard: View {

    let model: ShellModel
    let trouble: TokenTrouble

    var body: some View {
        ScrollView {
            VStack(alignment: .leading, spacing: 10) {
                Text(tokenTroubleTitle(trouble))
                    .font(.headline)
                Text(tokenTroubleText(trouble, address: ServerCredentials.address(model.inputs.serverURL)))
                    .font(.footnote)
                    .fixedSize(horizontal: false, vertical: true)
                if let wait = tokenTroubleWaitLine(trouble) {
                    Text(wait)
                        .font(.footnote)
                        .foregroundStyle(.orange)
                }
                CheckedPairingFieldView(field: PairingFieldModel(
                    container: model.container,
                    label: ShellStrings.pairAgain
                ) { _ in
                    model.showToast(ShellStrings.pairedAgainToast)
                    model.pairingDone()
                })
                Text(tokenTroubleHint(trouble))
                    .font(.caption)
                    .foregroundStyle(Color.fgMuted)
                    .fixedSize(horizontal: false, vertical: true)
                HStack(spacing: 8) {
                    if tokenTroubleOffersOldTokenRetry(trouble) {
                        Button(ShellStrings.tryOldTokenAgain) { model.tryTheOldTokenAgain() }
                    }
                    Button(ShellStrings.useThisPhonesMedia) { model.chooseThisPhone() }
                }
                .font(.footnote)
            }
            .padding(20)
            .frame(maxWidth: .infinity, alignment: .leading)
        }
    }
}
