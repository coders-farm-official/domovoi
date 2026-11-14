import Foundation

// Health — `RoomEngine.kt:1060-1174`: the minute's `health` frame (when the
// core's `ready` lists `health`), and the battery between minutes
// (domovoi/room_battery.py warns at 20 % and 10 %).

extension RoomEngine {

    func startHealth(run: Int) {
        let timer = DispatchSource.makeTimerSource(queue: healthQueue)
        let every = DispatchTimeInterval.milliseconds(Int(parts.healthIntervalMS))
        timer.schedule(deadline: .now() + every, repeating: every)
        timer.setEventHandler { [weak self] in self?.healthTick(run: run) }
        // Every battery change (level or charger) between the minutes.
        let changes = parts.device.batteryChanges()
        let battery = Task { [weak self] in
            for await b in changes {
                if Task.isCancelled { break }
                guard let self else { break }
                self.healthQueue.async { self.onBattery(b) }
            }
        }
        setHealthTimer(timer, battery: battery)
        timer.resume()
    }

    func healthTick(run: Int) {
        guard isRunning(run), ready, features.contains("health") else { return }
        let f = healthFacts()
        shared.withLock { $0.lastFacts = f }
        if send(RoomHealthSample.build(f)) { batteryReporter.sent(f.battery, nowMS: parts.clock()) }
    }

    /// One decision at a time (the health queue is serial): two readings
    /// landing together send once.
    func onBattery(_ b: RoomBattery) {
        guard running, ready, features.contains("health") else { return }
        let now = parts.clock()
        guard let wait = batteryReporter.decide(b, nowMS: now) else { return }
        if wait == 0 {
            sendBattery(b, now: now)
            return
        }
        // Inside the gap: look again when it ends (one look pending).
        let first: Bool = shared.withLock { s in
            if s.batteryRetry { return false }
            s.batteryRetry = true
            return true
        }
        guard first else { return }
        let device = parts.device
        healthQueue.asyncAfter(deadline: .now() + .milliseconds(Int(wait))) { [weak self] in
            guard let self else { return }
            self.shared.withLock { $0.batteryRetry = false }
            self.onBattery(device.battery())
        }
    }

    func sendBattery(_ b: RoomBattery, now: Int64) {
        let base = shared.withLock { $0.lastFacts } ?? {
            let f = healthFacts()
            shared.withLock { $0.lastFacts = f }
            return f
        }()
        var f = base
        f.seq = shared.withLock { s -> Int in
            s.healthSeq += 1
            return s.healthSeq
        }
        f.ts = parts.wall()
        f.batteryPercent = b.percent
        f.batteryCharging = b.charging
        f.onPower = b.onPower
        f.plugged = b.plugged
        f.tempC = b.tempC ?? base.tempC
        if send(RoomHealthSample.build(f)) { batteryReporter.sent(b, nowMS: now) }
    }

    func healthFacts() -> RoomHealthSample.Facts {
        let device = parts.device
        let now = parts.clock()
        let cpu = device.cpuSeconds()
        let battery = device.battery()
        let micBlocked = status.micBlocked
        return shared.withLock { s in
            var cpuPct: Double?
            if let cpu {
                cpuPct = (cpu - s.lastCPU) * 100_000 / Double(max(1, now - s.lastCPUAt))
                s.lastCPU = cpu
                s.lastCPUAt = now
            }
            let frames = s.framesThisMinute
            s.framesThisMinute = 0
            s.healthSeq += 1
            return RoomHealthSample.Facts(
                seq: s.healthSeq,
                ts: parts.wall(),
                rssKB: device.rssKB(),
                cpuPct: cpuPct,
                tempC: battery.tempC,
                uptimeS: device.uptimeSeconds(),
                procUptimeS: device.processUptimeSeconds(),
                coreOK: s.ready,
                micExpected: !s.muted,
                micFPS: Double(frames) * 1000 / Double(parts.healthIntervalMS),
                idle: s.phase == .listening,
                wsUp: s.ready,
                wsDownForS: s.ready ? 0 : (now - s.downSince) / 1000,
                reconnects: max(0, s.reconnects - 1),
                // An iPhone app may not read its Wi-Fi's signal or speed
                // (ROOMMODE-MAP §3.11): left out.
                wifiRxMbits: nil,
                wifiSSID: nil,
                wifiSignalDBM: nil,
                batteryPercent: battery.percent,
                batteryCharging: battery.charging,
                onPower: battery.onPower,
                micBlocked: micBlocked,
                wakeLoopAgeS: s.lastFrameAt > 0 ? Double(now - s.lastFrameAt) / 1000 : nil,
                threads: device.threads(),
                plugged: battery.plugged
            )
        }
    }
}
