/// Nothing yet. The package exists so the Xcode project and the scripts have
/// their final shape from the start; the port is its own milestone.
///
/// See `README.md` beside `Package.swift`.
public enum DomovoiPhonemizer {
    /// What the port is measured against: every case in
    /// `domovoi/phonemizer/golden.json` exact, and the fixture's `near_tie`
    /// cases allowed one phoneme's difference. Fix the port, never the
    /// fixture.
    public static let goldenFixture = "domovoi/phonemizer/golden.json"
}
