import Foundation

/// One ``WakeGate`` per wake word (1.4.0: a room may wake on up to five),
/// as the Pi's wake loop keeps them (satellite/client.py `_wait_for_wake`):
/// each word at its own threshold, with its own patience run and its own
/// cooldown, all fed the same chunk's scores. The first word whose gate
/// opens wakes the room; the others' runs are forgotten, so they do not
/// carry into the next listen.
///
/// Pure, like the gate: no audio, no clock of its own. Not thread-safe: the
/// listening thread owns it.
public final class WakeGateSet {
    /// The words, main word first, one per gate.
    public let words: [String]
    public let gates: [WakeGate]

    public init(
        words: [String],
        thresholds: [Float],
        patience: Int = WakeGate.patienceDefault,
        cooldownSec: Double = WakeGate.cooldownSecDefault
    ) {
        let room = thresholds.first ?? RoomWakeSettings.defaultThreshold
        self.words = words
        self.gates = words.indices.map { i in
            WakeGate(threshold: i < thresholds.count ? thresholds[i] : room, patience: patience, cooldownSec: cooldownSec)
        }
    }

    public var thresholds: [Float] { gates.map(\.threshold) }

    /// Feeds one chunk's scores, one per word in ``words``' order (a word
    /// with no score scores 0). The index of the word that woke the room,
    /// or nil.
    public func feed(_ scores: [Float], now: Int64) -> Int? {
        var fired: Int?
        for (i, gate) in gates.enumerated() {
            let score: Float = i < scores.count ? scores[i] : 0
            if gate.feed(score, now: now) && fired == nil { fired = i }
        }
        if let fired {
            for (i, gate) in gates.enumerated() where i != fired { gate.reset() }
        }
        return fired
    }

    /// One chunk over a word's own threshold, with no patience and no
    /// cooldown: the drop-in "hang up" rule (the Pi's `_wake_hit`, which
    /// barge-in uses too). The first such word in ``words``' order, or nil.
    public func heard(_ scores: [Float]) -> Int? {
        for (i, gate) in gates.enumerated() where i < scores.count {
            if scores[i] > gate.threshold { return i }
        }
        return nil
    }

    /// The peak score of the run that last woke `index`'s word.
    public func lastPeak(_ index: Int) -> Float? {
        index >= 0 && index < gates.count ? gates[index].lastPeak : nil
    }

    /// No wake on any word until `ms` (default: the cooldown) from `now`.
    public func quietFor(now: Int64, ms: Int64? = nil) {
        for gate in gates { gate.quietFor(now: now, ms: ms) }
    }

    /// Forget every run in progress.
    public func reset() {
        for gate in gates { gate.reset() }
    }
}
