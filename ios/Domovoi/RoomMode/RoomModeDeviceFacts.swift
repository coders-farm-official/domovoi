import DomovoiKit
import Foundation
import UIKit

/// What a room's health frame says about this iPhone (`roommode/RoomDevice.kt`
/// and the battery part of `HealthSample.kt`; ROOMMODE-MAP §3.10): the battery
/// from `UIDevice`, the memory footprint and thread count from the kernel's
/// task info, and the CPU time from `getrusage`.
///
/// The battery is read on the main actor (UIKit's rule) and kept here, so the
/// room's threads read the last reading without hopping: battery monitoring
/// is turned on when this is made (room mode is running), and every level or
/// charger change is both cached and handed to ``batteryChanges()``.
///
/// What iOS will not say, and so the frame leaves out: which charger
/// (`plugged`), the battery's temperature (`soc_temp_c`), and the Wi-Fi's
/// signal and speed.
///
/// R2 owns this file; R3's runtime makes one (on the main actor).
struct RoomModeDeviceFacts: RoomDeviceFacts {

    private let watch: RoomModeBatteryWatch

    @MainActor
    init() {
        watch = RoomModeBatteryWatch()
    }

    func battery() -> RoomBattery { watch.current }

    func batteryChanges() -> AsyncStream<RoomBattery> { watch.changes() }

    /// `task_vm_info.phys_footprint`, the number iOS's own memory limit and
    /// Xcode's gauge use, in kB.
    func rssKB() -> Int64? {
        var info = task_vm_info_data_t()
        var count = mach_msg_type_number_t(MemoryLayout<task_vm_info_data_t>.size / MemoryLayout<natural_t>.size)
        let result = withUnsafeMutablePointer(to: &info) { pointer in
            pointer.withMemoryRebound(to: integer_t.self, capacity: Int(count)) {
                task_info(mach_task_self_, task_flavor_t(TASK_VM_INFO), $0, &count)
            }
        }
        guard result == KERN_SUCCESS else { return nil }
        return Int64(info.phys_footprint / 1024)
    }

    func threads() -> Int? {
        var list: thread_act_array_t?
        var count: mach_msg_type_number_t = 0
        guard task_threads(mach_task_self_, &list, &count) == KERN_SUCCESS else { return nil }
        if let list {
            vm_deallocate(
                mach_task_self_,
                vm_address_t(UInt(bitPattern: list)),
                vm_size_t(Int(count) * MemoryLayout<thread_t>.stride)
            )
        }
        return Int(count)
    }

    func cpuSeconds() -> Double? {
        var usage = rusage()
        guard getrusage(RUSAGE_SELF, &usage) == 0 else { return nil }
        func seconds(_ t: timeval) -> Double { Double(t.tv_sec) + Double(t.tv_usec) / 1_000_000 }
        return seconds(usage.ru_utime) + seconds(usage.ru_stime)
    }

    func uptimeSeconds() -> Int64 {
        Int64(ProcessInfo.processInfo.systemUptime)
    }

    func processUptimeSeconds() -> Int64 {
        var info = kinfo_proc()
        var size = MemoryLayout<kinfo_proc>.stride
        var mib: [Int32] = [CTL_KERN, KERN_PROC, KERN_PROC_PID, getpid()]
        guard sysctl(&mib, u_int(mib.count), &info, &size, nil, 0) == 0 else { return 0 }
        let started = info.kp_proc.p_un.__p_starttime
        let startedAt = Double(started.tv_sec) + Double(started.tv_usec) / 1_000_000
        return max(0, Int64(Date().timeIntervalSince1970 - startedAt))
    }
}

/// The battery, cached from `UIDevice` on the main actor.
///
/// `@unchecked Sendable`: the reading and the watchers are behind ``lock``;
/// UIKit is touched only on the main actor (the initialiser and the
/// notification blocks, which the main queue delivers).
final class RoomModeBatteryWatch: @unchecked Sendable {

    private let lock = NSLock()
    private var reading: RoomBattery
    private var watchers: [Int: AsyncStream<RoomBattery>.Continuation] = [:]
    private var next = 0
    private var tokens: [NSObjectProtocol] = []

    @MainActor
    init() {
        let device = UIDevice.current
        device.isBatteryMonitoringEnabled = true
        reading = RoomBatteryReporter.battery(level: device.batteryLevel, stateRaw: device.batteryState.rawValue)
        let center = NotificationCenter.default
        for name in [UIDevice.batteryLevelDidChangeNotification, UIDevice.batteryStateDidChangeNotification] {
            tokens.append(center.addObserver(forName: name, object: nil, queue: .main) { [weak self] _ in
                MainActor.assumeIsolated {
                    let d = UIDevice.current
                    self?.changed(RoomBatteryReporter.battery(level: d.batteryLevel, stateRaw: d.batteryState.rawValue))
                }
            })
        }
    }

    deinit {
        for token in tokens { NotificationCenter.default.removeObserver(token) }
    }

    var current: RoomBattery {
        lock.lock()
        defer { lock.unlock() }
        return reading
    }

    func changes() -> AsyncStream<RoomBattery> {
        AsyncStream { continuation in
            lock.lock()
            next += 1
            let id = next
            watchers[id] = continuation
            lock.unlock()
            continuation.onTermination = { [weak self] _ in
                guard let self else { return }
                self.lock.lock()
                self.watchers[id] = nil
                self.lock.unlock()
            }
        }
    }

    private func changed(_ b: RoomBattery) {
        lock.lock()
        let same = b == reading
        reading = b
        let all = Array(watchers.values)
        lock.unlock()
        guard !same else { return }
        for c in all { c.yield(b) }
    }
}
