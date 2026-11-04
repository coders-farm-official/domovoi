// The Images plugin band is no longer a view of its own, and no longer wraps
// the Images page.
//
// Two owner answers took it apart. Answer 58 ("fold the two plugin bands into
// one view") moved the shape to `ios/Domovoi/Shell/PluginBandView.swift`: this
// file and `ios/Domovoi/Stations/StationsPluginBand.swift` held two copies of
// one band, written the same night by two items that could not see each
// other's files, differing only in the route's icon, the capability and
// whether `manage` was drawn — where Android has one `PluginScreenFrame`
// (`android/.../ui/components/PluginFrame.kt:92`).
//
// Answer 29 then took the band off the screen's frame: it is the first row of
// `ImagesView`'s own `ScrollView` now (`PluginBandView` with
// `place: .insideThePage`), so it scrolls away with the page at accessibility
// text sizes instead of keeping 256 of 852 points on a 393-point phone. The
// `ImagesPluginBand` wrapper that `RouteScreen.swift` used to construct went
// with that change, which needed `ImagesView.swift` and `RouteScreen.swift` in
// one commit and so was made on the batch's integration branch.
//
// Everything the band ports, and everything it leaves out, is documented in
// `PluginBandView.swift`. The file is kept rather than deleted so that a merge
// of a branch where it still has a body resolves as a conflict somebody reads
// rather than as two declarations of one type.
