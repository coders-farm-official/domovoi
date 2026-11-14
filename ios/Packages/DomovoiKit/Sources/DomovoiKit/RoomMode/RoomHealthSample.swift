import Foundation

/// The room phone's own `health` frame (sent once a minute when the core's
/// `ready` lists `health`) — `roommode/HealthSample.kt`. Not
/// `RoomsHealthSample`, which is the DASHBOARD's reading of a room's health
/// (ROOMMODE-MAP §13.2): this is what a room says about itself.
///
/// The Pi's sample where a phone has the fact, and a phone's own: the battery
/// and "the mic hands us silence". Numbers and short strings only, never
/// speech; well under the core's 4 KB cap.
///
/// The battery is ONE object, the core's (domovoi/satellite_health.py
/// `_battery`): `battery` = {percent 0-100, charging, on_power[, plugged]}.
/// An iPhone cannot say which charger or its battery's temperature, so
/// `plugged` and `soc_temp_c` are left out, as on a phone that will not say;
/// it has no Wi-Fi reading an app may take, so `wifi` is left out too
/// (ROOMMODE-MAP §3.10-3.11).
public enum RoomHealthSample {

    public struct Facts: Equatable, Sendable {
        public var seq: Int
        public var ts: Double
        public var rssKB: Int64?
        public var cpuPct: Double?
        /// The battery's temperature: the nearest a phone has to the Pi's
        /// SoC reading (nil on an iPhone).
        public var tempC: Double?
        public var uptimeS: Int64
        public var procUptimeS: Int64
        public var coreOK: Bool
        public var micExpected: Bool
        public var micFPS: Double
        public var idle: Bool
        public var wsUp: Bool
        public var wsDownForS: Int64
        public var reconnects: Int
        public var wifiRxMbits: Double?
        public var wifiSSID: String?
        public var wifiSignalDBM: Int?
        public var batteryPercent: Int?
        public var batteryCharging: Bool?
        public var onPower: Bool?
        public var micBlocked: Bool
        public var wakeLoopAgeS: Double?
        public var threads: Int?
        /// Which charger (`battery.plugged`), when a phone can say.
        public var plugged: String?

        public init(
            seq: Int, ts: Double, rssKB: Int64?, cpuPct: Double?, tempC: Double?, uptimeS: Int64,
            procUptimeS: Int64, coreOK: Bool, micExpected: Bool, micFPS: Double, idle: Bool, wsUp: Bool,
            wsDownForS: Int64, reconnects: Int, wifiRxMbits: Double?, wifiSSID: String?, wifiSignalDBM: Int?,
            batteryPercent: Int?, batteryCharging: Bool?, onPower: Bool?, micBlocked: Bool,
            wakeLoopAgeS: Double?, threads: Int?, plugged: String? = nil
        ) {
            self.seq = seq
            self.ts = ts
            self.rssKB = rssKB
            self.cpuPct = cpuPct
            self.tempC = tempC
            self.uptimeS = uptimeS
            self.procUptimeS = procUptimeS
            self.coreOK = coreOK
            self.micExpected = micExpected
            self.micFPS = micFPS
            self.idle = idle
            self.wsUp = wsUp
            self.wsDownForS = wsDownForS
            self.reconnects = reconnects
            self.wifiRxMbits = wifiRxMbits
            self.wifiSSID = wifiSSID
            self.wifiSignalDBM = wifiSignalDBM
            self.batteryPercent = batteryPercent
            self.batteryCharging = batteryCharging
            self.onPower = onPower
            self.micBlocked = micBlocked
            self.wakeLoopAgeS = wakeLoopAgeS
            self.threads = threads
            self.plugged = plugged
        }

        /// The battery these facts carry.
        public var battery: RoomBattery {
            RoomBattery(percent: batteryPercent, charging: batteryCharging, onPower: onPower, tempC: tempC, plugged: plugged)
        }
    }

    /// `HealthSample.kt:375-410`.
    public static func build(_ f: Facts) -> RoomProtocol.Frame {
        var o: RoomProtocol.Frame = [
            "type": .string("health"),
            "seq": .int(Int64(f.seq)),
            "ts": .double(f.ts),
            "uptime_s": .int(f.uptimeS),
            "proc_uptime_s": .int(f.procUptimeS),
            "core_ok": .bool(f.coreOK),
            "mic_expected": .bool(f.micExpected),
            "mic_fps": .double(round1(f.micFPS)),
            "idle": .bool(f.idle),
            "ws": .string(f.wsUp ? "up" : "down"),
            "ws_down_for_s": .int(f.wsDownForS),
            "reconnects": .int(Int64(f.reconnects)),
            "mic_blocked": .bool(f.micBlocked),
        ]
        if let v = f.rssKB { o["rss_kb"] = .int(v) }
        if let v = f.threads { o["threads"] = .int(Int64(v)) }
        if let v = f.cpuPct { o["cpu_pct"] = .double(round1(v)) }
        if let v = f.tempC { o["soc_temp_c"] = .double(round1(v)) }
        if let v = f.wakeLoopAgeS { o["wake_loop_age_s"] = .double(round1(v)) }
        if f.wifiRxMbits != nil || f.wifiSSID != nil || f.wifiSignalDBM != nil {
            var wifi: RoomProtocol.Frame = [:]
            if let v = f.wifiRxMbits { wifi["rx_mbits"] = .double(round1(v)) }
            if let v = f.wifiSSID { wifi["ssid"] = .string(String(v.prefix(64))) }
            if let v = f.wifiSignalDBM { wifi["signal_dbm"] = .int(Int64(min(0, max(-120, v)))) }
            o["wifi"] = .object(wifi)
        }
        let pct = f.batteryPercent.map { min(100, max(0, $0)) }
        if pct != nil || f.batteryCharging != nil || f.onPower != nil {
            var battery: RoomProtocol.Frame = [:]
            if let pct { battery["percent"] = .int(Int64(pct)) }
            if let v = f.batteryCharging { battery["charging"] = .bool(v) }
            if let v = f.onPower { battery["on_power"] = .bool(v) }
            if let v = f.plugged { battery["plugged"] = .string(v) }
            o["battery"] = .object(battery)
        }
        return o
    }

    static func round1(_ v: Double) -> Double { (v * 10).rounded() / 10 }
}
