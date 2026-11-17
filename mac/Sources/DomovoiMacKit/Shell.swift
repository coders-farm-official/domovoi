import Foundation

/// One program run: an absolute path, its arguments, environment on top of
/// the helper's own, and optionally what goes to its standard input.
public struct Command: Equatable, CustomStringConvertible {
    public var executable: String
    public var arguments: [String]
    public var environment: [String: String]
    public var stdin: Data?
    public var timeout: TimeInterval?
    /// Where it runs (nil: the helper's own folder). The system install runs
    /// limactl as the role account, which may not be able to read the
    /// folder the helper was started in.
    public var currentDirectory: String?

    public init(_ executable: String, _ arguments: [String] = [], environment: [String: String] = [:], stdin: Data? = nil, timeout: TimeInterval? = nil,
                currentDirectory: String? = nil) {
        self.executable = executable
        self.arguments = arguments
        self.environment = environment
        self.stdin = stdin
        self.timeout = timeout
        self.currentDirectory = currentDirectory
    }

    /// For logs: the program's name and arguments, never the environment
    /// or standard input.
    public var description: String {
        ([(executable as NSString).lastPathComponent] + arguments.map { $0.contains(" ") ? "'\($0)'" : $0 }).joined(separator: " ")
    }
}

public struct CommandResult: Equatable {
    public var status: Int32
    public var stdout: String
    public var stderr: String
    public var timedOut: Bool

    public init(status: Int32, stdout: String = "", stderr: String = "", timedOut: Bool = false) {
        self.status = status
        self.stdout = stdout
        self.stderr = stderr
        self.timedOut = timedOut
    }

    public var ok: Bool { status == 0 && !timedOut }

    /// The last few lines of whatever it printed, for an error message.
    public var tail: String {
        let text = (stderr.isEmpty ? stdout : stderr).trimmingCharacters(in: .whitespacesAndNewlines)
        return text.split(separator: "\n").suffix(6).joined(separator: " | ")
    }
}

/// Everything the helper runs goes through this, so the tests can answer
/// for `limactl`, `launchctl` and the rest.
public protocol CommandRunner: AnyObject {
    /// Runs `command` to the end. `onLine` gets each line of standard output
    /// as it arrives (provision.sh's progress lines).
    func run(_ command: Command, onLine: ((String) -> Void)?) throws -> CommandResult
}

extension CommandRunner {
    public func run(_ command: Command) throws -> CommandResult { try run(command, onLine: nil) }

    /// Runs it and throws, naming the command and its last lines, unless it
    /// succeeded.
    @discardableResult
    public func check(_ command: Command, _ what: String) throws -> CommandResult {
        let r = try run(command)
        guard r.ok else {
            throw HelperError("\(what) failed (\(command), \(r.timedOut ? "timed out" : "exit \(r.status)")): \(r.tail)")
        }
        return r
    }
}

public struct HelperError: Error, CustomStringConvertible, Equatable {
    public let message: String
    public init(_ message: String) { self.message = message }
    public var description: String { message }
}

/// The real thing: `Process`, no shell in between.
public final class ProcessRunner: CommandRunner {
    public init() {}

    public func run(_ command: Command, onLine: ((String) -> Void)?) throws -> CommandResult {
        let p = Process()
        p.executableURL = URL(fileURLWithPath: command.executable)
        p.arguments = command.arguments
        if let d = command.currentDirectory { p.currentDirectoryURL = URL(fileURLWithPath: d, isDirectory: true) }
        var env = ProcessInfo.processInfo.environment
        for (k, v) in command.environment { env[k] = v }
        p.environment = env
        let out = Pipe(), err = Pipe()
        p.standardOutput = out
        p.standardError = err
        let input = Pipe()
        p.standardInput = command.stdin == nil ? FileHandle.nullDevice : input

        let lock = NSLock()
        var outData = Data(), errData = Data(), pending = Data()
        let outDone = DispatchSemaphore(value: 0), errDone = DispatchSemaphore(value: 0)
        out.fileHandleForReading.readabilityHandler = { h in
            let chunk = h.availableData
            if chunk.isEmpty { h.readabilityHandler = nil; outDone.signal(); return }
            var lines: [String] = []
            lock.lock()
            outData.append(chunk)
            pending.append(chunk)
            while let nl = pending.firstIndex(of: 0x0a) {
                lines.append(String(decoding: pending[pending.startIndex..<nl], as: UTF8.self))
                pending.removeSubrange(pending.startIndex...nl)
            }
            lock.unlock()
            if let onLine { lines.forEach(onLine) }
        }
        err.fileHandleForReading.readabilityHandler = { h in
            let chunk = h.availableData
            if chunk.isEmpty { h.readabilityHandler = nil; errDone.signal(); return }
            lock.lock(); errData.append(chunk); lock.unlock()
        }

        let finished = DispatchSemaphore(value: 0)
        p.terminationHandler = { _ in finished.signal() }
        do {
            try p.run()
        } catch {
            out.fileHandleForReading.readabilityHandler = nil
            err.fileHandleForReading.readabilityHandler = nil
            throw HelperError("can't start \(command.executable): \(error.localizedDescription)")
        }
        if let data = command.stdin {
            DispatchQueue.global().async {
                input.fileHandleForWriting.write(data)
                try? input.fileHandleForWriting.close()
            }
        }
        var timedOut = false
        if let t = command.timeout {
            if finished.wait(timeout: .now() + t) == .timedOut {
                timedOut = true
                p.terminate()
                if finished.wait(timeout: .now() + 5) == .timedOut { kill(p.processIdentifier, SIGKILL) }
            }
        }
        p.waitUntilExit()
        _ = outDone.wait(timeout: .now() + 5)
        _ = errDone.wait(timeout: .now() + 5)
        lock.lock()
        let rest = pending
        let o = String(decoding: outData, as: UTF8.self), e = String(decoding: errData, as: UTF8.self)
        lock.unlock()
        if let onLine, !rest.isEmpty { onLine(String(decoding: rest, as: UTF8.self)) }
        return CommandResult(status: p.terminationStatus, stdout: o, stderr: e, timedOut: timedOut)
    }
}
