import DomovoiKit
import SwiftUI

/// A long-document summary in progress, under the transcript.
///
/// `DocJobCard` (`ChatDocsUi.kt:301`): the server's own progress line, a bar
/// of how much has been **read**, and `stop`.
///
/// ### The bar is `read` of `parts`, never `part`
///
/// `part` is which part is being worked on now and `read` is how many are
/// finished, so a bar drawn from `part` is always one ahead of the truth and
/// finishes before the job does. ``ChatMoreJobRules/progress(_:)`` is the one
/// place that decides it and the fixture seeds `read: 2, part: 3` so a case
/// can tell them apart.
///
/// ### What is not, and why there is no control for it
///
/// * **No "use the home server" offer and no `GET /doc-jobs/speed`.** Both
///   exist to compare the *phone's* model against the house's
///   (`fetchDocSpeed`, `ChatDocsApi.kt:147`), and this app has one place and
///   no phone model to compare.
/// * **No `delete`.** `DELETE /api/chat/doc-jobs/{id}` exists
///   (`chat_doc_jobs.py:1083`) and Android's chat never calls it — the job
///   list it belongs to is the assistant's own screen.
struct ChatMoreJobCardView: View {

    let job: ChatMoreJob
    let more: ChatMoreModel

    var body: some View {
        VStack(alignment: .leading, spacing: 6) {
            Text(ChatMoreJobRules.line(job))
                .font(.subheadline)
                .fixedSize(horizontal: false, vertical: true)
                .multilineTextAlignment(.leading)
                // So a screen reader hears the progress change rather than
                // having to go looking for it (`liveRegion`,
                // `ChatDocsUi.kt:318`).
                .accessibilityAddTraits(.updatesFrequently)
            if job.parts > 0 {
                ProgressView(value: Double(job.read), total: Double(job.parts))
                    .tint(Color.brandInk)
                    .accessibilityLabel(ChatMoreStrings.partsRead(read: job.read, parts: job.parts))
            }
            if job.live {
                Button(ChatMoreStrings.stop) { more.stopJob(job.id) }
                    .buttonStyle(.plain)
                    .font(.subheadline)
                    .foregroundStyle(Color.errInk)
                    .frame(minHeight: 44)
                    .disabled(more.answering)
            } else if !ChatMoreJobRules.interrupted([job]).isEmpty {
                // An interrupted job's own offer (`ChatScreen.kt:1022`). A
                // **stopped** one's "carry on" belongs to the row its newest
                // reply is (``ChatMoreModel/carryOn(for:)``), which is why
                // this branch is only the interrupted case.
                Button(ChatMoreJobRules.carryOnText(ChatMoreJobRules.left(job))) {
                    more.resumeJob(job.id)
                }
                .buttonStyle(.plain)
                .font(.subheadline)
                .foregroundStyle(Color.brandInk)
                .frame(minHeight: 44)
                .disabled(more.answering)
            }
            if let failure = job.error, !failure.detail.isEmpty {
                Text(failure.detail)
                    .font(.caption)
                    .foregroundStyle(Color.errInk)
                    .fixedSize(horizontal: false, vertical: true)
                    .multilineTextAlignment(.leading)
            }
        }
        .padding(10)
        .frame(maxWidth: .infinity, alignment: .leading)
        .background(RoundedRectangle(cornerRadius: 12).fill(Color.fgMuted.opacity(0.08)))
    }
}

/// Every job of the open thread that has a card of its own.
///
/// A **live** job and an **interrupted** one each draw one
/// (`ChatScreen.kt:1036`, `:1022`); a stopped one does not, because its offer
/// is the "carry on" under its newest reply (`:994`). So this is not simply
/// `more.jobs`.
struct ChatMoreJobCardsView: View {

    let more: ChatMoreModel

    var body: some View {
        let cards = ChatMoreJobRules.live(more.jobs) + ChatMoreJobRules.interrupted(more.jobs)
        if !cards.isEmpty {
            VStack(alignment: .leading, spacing: 8) {
                ForEach(cards) { job in
                    ChatMoreJobCardView(job: job, more: more)
                }
            }
            .padding(.horizontal, 16)
            .padding(.bottom, 8)
            .frame(maxWidth: .infinity, alignment: .leading)
        }
    }
}
