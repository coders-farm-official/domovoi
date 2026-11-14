// `StationsPluginBand` moved to `ios/Domovoi/Shell/PluginBandView.swift`.
//
// Owner answer 58: "fold the two plugin bands into one view." See the note in
// `ios/Domovoi/Images/ImagesPluginBandView.swift`, which this file is the
// other half of.
//
// `StationsPluginBand` still exists, with the same call-site API, as a thin
// wrapper over `PluginBandView`: its one call site is `StationsView.swift:86`,
// which the shell unit does not own. It **gained the `manage` link** in the
// fold (owner answer 28: "leave the `manage` link") — one band cannot answer
// that question two ways.
