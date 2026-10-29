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
/// **The paste field is not here.** Android's card embeds
/// `CheckedPairingField`, the same component the pairing screen uses — and
/// the pairing screen is part 3's, along with the paste check and the vault
/// write that make a field do anything. A field that looked like it took a
/// token and did not would be exactly the misleading control this project has
/// a rule against, so the card says where the token goes and part 3 adds the
/// field.
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
                PairingFieldPlaceholder(label: ShellStrings.pairAgain)
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
