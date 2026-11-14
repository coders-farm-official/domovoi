import Foundation
import XCTest
@testable import DomovoiKit

/// Room mode is a preview (owner decision 2, 2026-10-16; Android's
/// `RoomModePreviewTest`): the card that offers it and its explainer say so,
/// as a label beside the choice. The views are the app target's, which this
/// package cannot build, so — as Android's second case does for its
/// Activity — the sources are read.
final class RoomModePreviewTests: XCTestCase {

    private static let app: URL = {
        var url = URL(fileURLWithPath: #filePath)
        for _ in 0..<5 { url = url.deletingLastPathComponent() }
        return url.appendingPathComponent("Domovoi/RoomMode")
    }()

    private func source(_ file: String) throws -> String {
        try String(contentsOf: Self.app.appendingPathComponent(file), encoding: .utf8)
    }

    func testThePreviewWordsAreAndroids() {
        XCTAssertEqual("preview", RoomModeStrings.previewLabel)
        XCTAssertEqual(
            "It works, but it has been tried on fewer phones than the rest of Domovoi, and it may still change.",
            RoomModeStrings.previewMeans
        )
        XCTAssertEqual("Use this device as a room", RoomModeStrings.entryTitle)
        XCTAssertEqual(RoomModeStrings.entryTitle, RoomModeStrings.entryButton, "named twice, as Android's card")
    }

    func testTheEntryCardSaysPreviewBesideTheChoice() throws {
        let src = try source("RoomModeEntryCard.swift")
        let card = try XCTUnwrap(src.components(separatedBy: "struct RoomModeEntryCard").dropFirst().first)
            .components(separatedBy: "struct RoomModeEntryLine").first ?? ""
        XCTAssertTrue(card.contains("RoomPreviewLabel()"), "the entry card draws the preview label")
        XCTAssertTrue(card.contains("RoomModeStrings.entryTitle") && card.contains("RoomModeStrings.entryButton"))
        let line = try XCTUnwrap(src.components(separatedBy: "struct RoomModeEntryLine").dropFirst().first)
        XCTAssertTrue(line.contains("RoomModeStrings.previewLabel"), "the no-server line says preview too")
    }

    func testTheExplainerSaysPreviewToo() throws {
        let src = try source("RoomSetupView.swift")
        let explainer = try XCTUnwrap(src.components(separatedBy: "struct RoomExplainerView").dropFirst().first)
            .components(separatedBy: "RoomModeStrings.startRoomMode").first ?? ""
        XCTAssertTrue(explainer.contains("RoomPreviewLabel()"), "the explainer shows the preview label under its heading")
    }
}
