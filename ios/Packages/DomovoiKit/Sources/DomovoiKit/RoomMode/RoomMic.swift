import Foundation

/// A microphone source that can say why it stopped (iOS took it: an
/// interruption, a media-services reset). ``RoomAudioSource`` is one; a
/// feed is not, and never stops on its own.
public protocol RoomPCMSourceStopReporting: AnyObject {
    /// Why the source stopped handing over audio, while it has.
    var stoppedBy: RoomMicStop? { get }
}

/// The room's microphone rules, on top of a ``RoomPCMSource`` — the iPhone's
/// `roommode/RoomMic.kt`: 16 kHz mono PCM16 in 30 ms frames, raw while it
/// listens for the wake word, voice processing (Apple's echo canceller)
/// during a drop-in, when it has to hear the room while the other room's
/// voice plays (ROOMMODE-MAP D4).
///
/// Silence the system hands over (the microphone off for the app, another
/// app holding it) is all-zero samples: ``blocked`` turns true after two
/// seconds of them and false at the first real sample.
///
/// Mute is enforced HERE, not by its callers: every ``read(_:)`` asks
/// `muted` first and, when it is, closes the source and answers ``mutedRead``
/// — so in every state (listening, the acknowledgement, a capture, the wait
/// for a reply, a drop-in) the microphone is shut within one frame of Mute,
/// and ``open(call:)`` refuses while muted. `onOpenChanged` hears every open
/// and close, which is what the screen's mic chip shows (it never says "mic
/// off" while a source is open).
///
/// Not thread-safe: the room's listening thread owns it.
public final class RoomMic {

    /// What ``read(_:)`` answers while muted (the source is then closed).
    public static let mutedRead = -100

    /// Two seconds of 30 ms frames.
    public static let blockedAfterFrames = 2_000 / RoomAudioFrame.milliseconds

    private let make: RoomPCMSourceMaking
    private let muted: () -> Bool
    private let onOpenChanged: (Bool) -> Void
    private var source: (any RoomPCMSource)?
    private var zeroFrames = 0
    private var scratch: [Int16] = []

    /// The mode it is open in (`true`: a call), or nil when closed.
    public private(set) var openAs: Bool?

    /// Two seconds of exact zeros: the system is handing over silence.
    public private(set) var blocked = false

    /// Why the last source stopped on its own, when iOS said
    /// (``RoomPCMSourceStopReporting``); nil after a clean open.
    public private(set) var lastStop: RoomMicStop?

    public init(
        source make: @escaping RoomPCMSourceMaking,
        muted: @escaping () -> Bool = { false },
        onOpenChanged: @escaping (Bool) -> Void = { _ in }
    ) {
        self.make = make
        self.muted = muted
        self.onOpenChanged = onOpenChanged
    }

    /// A source is open now.
    public var isOpen: Bool { source != nil }

    /// Opens a source for listening (`call == false`) or a drop-in. Does
    /// nothing while muted. Throws what the source threw
    /// (``RoomMicFailure`` when iOS will not open it).
    public func open(call: Bool) throws {
        close()
        if muted() { return }
        let s = make()
        do {
            try s.start(call: call)
        } catch {
            lastStop = (error as? RoomMicFailure)?.stop
            throw error
        }
        source = s
        openAs = call
        zeroFrames = 0
        lastStop = nil
        onOpenChanged(true)
    }

    /// One whole frame into `buf`; the sample count, ``mutedRead`` while
    /// muted, or negative when the source stopped.
    public func read(_ buf: inout [Int16]) -> Int {
        if muted() {
            close()
            return Self.mutedRead
        }
        guard let s = source else { return -1 }
        var got = 0
        while got < buf.count {
            let n: Int
            if got == 0 {
                n = s.read(into: &buf)
            } else {
                if scratch.count != buf.count - got { scratch = [Int16](repeating: 0, count: buf.count - got) }
                n = s.read(into: &scratch)
                if n > 0 {
                    for i in 0..<n { buf[got + i] = scratch[i] }
                }
            }
            if n < 0 {
                lastStop = (s as? RoomPCMSourceStopReporting)?.stoppedBy
                return n
            }
            if n == 0 { continue }
            got += n
        }
        noteZeros(buf, got)
        return got
    }

    private func noteZeros(_ buf: [Int16], _ n: Int) {
        var allZero = true
        for i in 0..<n where buf[i] != 0 {
            allZero = false
            break
        }
        if allZero {
            zeroFrames += 1
            if zeroFrames >= Self.blockedAfterFrames { blocked = true }
        } else {
            zeroFrames = 0
            blocked = false
        }
    }

    public func close() {
        let wasOpen = source != nil
        source?.stop()
        source = nil
        openAs = nil
        if wasOpen { onOpenChanged(false) }
    }
}
