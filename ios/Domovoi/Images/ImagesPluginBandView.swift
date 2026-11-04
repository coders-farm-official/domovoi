// `ImagesPluginBand` moved to `ios/Domovoi/Shell/PluginBandView.swift`.
//
// Owner answer 58: "fold the two plugin bands into one view." This file and
// `ios/Domovoi/Stations/StationsPluginBand.swift` held two copies of one
// shape, written the same night by two items that could not see each other's
// files, differing only in the route's icon, the capability and whether
// `manage` was drawn — where Android has one `PluginScreenFrame`
// (`android/.../ui/components/PluginFrame.kt:92`).
//
// `ImagesPluginBand` still exists, with the same call-site API, as a thin
// wrapper over `PluginBandView` in that file: its one call site is
// `RouteScreen.swift:125-132`, which the shell unit does not own. Everything
// the band ports, and everything it leaves out, is documented there.
//
// The file is kept rather than deleted so that a merge of the overnight
// integration, where it still has a body, resolves as a conflict somebody
// reads rather than as two declarations of one type.
