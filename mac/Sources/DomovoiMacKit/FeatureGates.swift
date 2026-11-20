import Foundation

/// Controls that are built and unit-tested but not yet proven on a real
/// Mac. The owner's rule is no misleading controls: until a test on real
/// hardware proves one, it stays out of sight (no menu item, no consent
/// page option, and the helper refuses to switch it on).
///
/// To turn one on, change its constant here, and nothing else: every place
/// that shows or does the control reads it. FeatureGatesTests pins today's
/// value, so flipping it also means changing that test, on purpose.
public enum FeatureGates {
    /// "Start after a power failure" (`pmset autorestart`) for desktop Macs.
    /// OFF: unverified on Apple Silicon (mac-installer/TRAIN-CHECKLIST.md,
    /// "Phase 5 follow-ups", has the real-Mac test that must pass first,
    /// on a desktop Mac).
    public static let startAfterPowerFailure = false
}
