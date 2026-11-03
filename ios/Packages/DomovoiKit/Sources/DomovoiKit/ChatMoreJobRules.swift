import Foundation

/// Which long-document job card appears where, and what its bar reads.
///
/// Ported from `ChatScreen.kt:978-1046` (the three places a job is drawn) and
/// `ChatDocsUi.kt:301`/`:345` (the card and "carry on"). Pure: a job's state
/// decides what a person can do about it, and a view cannot be asked whether
/// it got that right.
public enum ChatMoreJobRules {

    /// A job that has stopped and can be carried on (`ChatScreen.kt:979`).
    public static func stopped(_ jobs: [ChatMoreJob]) -> [ChatMoreJob] {
        jobs.filter { $0.state == "stopped" || $0.state == "failed" }
    }

    /// A job a restart interrupted: carried on only when asked
    /// (`ChatScreen.kt:1023`).
    public static func interrupted(_ jobs: [ChatMoreJob]) -> [ChatMoreJob] {
        jobs.filter { $0.state == "interrupted" }
    }

    /// A job still working — including one this screen did not start
    /// (`ChatScreen.kt:1037`).
    public static func live(_ jobs: [ChatMoreJob]) -> [ChatMoreJob] {
        jobs.filter(\.live)
    }

    /// How many parts are still to read.
    ///
    /// **Always `parts - read`**, as `ChatScreen.kt:1030` and `:1027` both
    /// compute it. `part` is which part is being worked on now, which for a
    /// stopped job is one too many — a "carry on (4 parts left)" on a job
    /// with three left is the bug this function exists to pin.
    public static func left(_ job: ChatMoreJob) -> Int {
        max(0, job.parts - job.read)
    }

    /// The bar under a job's line: **`read` of `parts`**
    /// (`ChatDocsUi.kt:330`), 0 when the server has not said how many parts
    /// there are.
    public static func progress(_ job: ChatMoreJob) -> Double {
        guard job.parts > 0 else { return 0 }
        return min(1, max(0, Double(job.read) / Double(job.parts)))
    }

    /// The line a card shows: the server's own progress sentence, and for an
    /// interrupted job with none the fallback `ChatScreen.kt:1026` uses.
    public static func line(_ job: ChatMoreJob) -> String {
        if job.line.isEmpty, job.state == "interrupted" { return ChatMoreStrings.jobInterrupted }
        return job.line
    }

    /// "carry on (3 parts left)" (`ChatDocsUi.kt:345`).
    public static func carryOnText(_ left: Int) -> String { ChatMoreStrings.carryOn(left) }

    /// The stopped job whose **newest** reply is the row at `index`, which is
    /// the one row that offers "carry on" (`ChatScreen.kt:980`, `:991`).
    ///
    /// `rows` is the transcript's extras in the transcript's order. Android
    /// finds the last row whose `doc.job.id` is the job's and offers the
    /// button only there, so a thread that summarised the same document twice
    /// does not grow two buttons for one job.
    public static func carryOnRow(
        for job: ChatMoreJob,
        rows: [ChatMoreRowExtras]
    ) -> Int64? {
        rows.last { $0.doc?.job?.id == job.id }?.id
    }

    /// Whether a job's card may be answered at all: nothing while a send or a
    /// card answer is streaming (`ChatScreen.kt:1022`, `:1036`).
    public static func answerable(sending: Bool) -> Bool { !sending }
}
