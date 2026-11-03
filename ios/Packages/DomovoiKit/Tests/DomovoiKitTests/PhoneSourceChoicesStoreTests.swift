import Foundation
import XCTest
@testable import DomovoiKit

/// How long a side stays chosen, and when it is forgotten.
///
/// The lifetime is the whole point of this type: a `@State` on
/// ``RouteScreen`` lasts one visit to one page, and Android's is the
/// workspace's.
@MainActor
final class PhoneSourceChoicesStoreTests: XCTestCase {

    override func setUp() {
        super.setUp()
        PhoneSourceChoicesStore.forget()
    }

    /// Leaving a page and coming back finds the side that was chosen —
    /// which a per-view `@State` could not do.
    func testTheSameServerKeepsTheChoiceAcrossTwoLookups() {
        PhoneSourceChoicesStore.of(server: "http://house.local:6369")
            .choose(.videos, .device)
        let again = PhoneSourceChoicesStore.of(server: "http://house.local:6369")
        XCTAssertEqual(again.of(.videos), .device)
        // And it is per page, not one shared flag: Music was never asked
        // about.
        XCTAssertEqual(again.of(.music), .server)
    }

    /// A choice made about one house is not a choice about the next one, and
    /// **every page starts on the server's side** there (`YourDevice.kt:30`).
    func testADifferentServerStartsOnTheServersSideAgain() {
        PhoneSourceChoicesStore.of(server: "http://house.local:6369")
            .choose(.videos, .device)
        let other = PhoneSourceChoicesStore.of(server: "http://flat.local:6369")
        XCTAssertEqual(other.of(.videos), .server)
        // Going back is going back to a rebuilt set, as Android's rebuild on
        // a workspace change is — not to the old one.
        let first = PhoneSourceChoicesStore.of(server: "http://house.local:6369")
        XCTAssertEqual(first.of(.videos), .server)
    }
}

/// The source behind a page, replaced while the page is reading it.
///
/// Written for the bug it was built for: a folder added through the picker
/// was saved and listed and never appeared on the page.
final class PhoneSwappableSourceTests: XCTestCase {

    private func source(_ label: String) -> PhoneMemorySource {
        PhoneMemorySource(
            shelves: [FileLibrary(id: "phone:folder:/\(label)", label: label, kind: "folder")],
            rows: [:]
        )
    }

    func testTheLibrariesChangeAfterAReplace() async {
        let box = PhoneSwappableSource(source("Recordings"))
        let before = await box.libraries().map(\.label)
        XCTAssertEqual(before, ["Recordings"])

        box.replace(with: PhoneCompositeSource([source("Recordings"), source("Trips")]))
        let after = await box.libraries().map(\.label)
        XCTAssertEqual(after, ["Recordings", "Trips"])
    }

    /// And a page reading through the composite sees it too, because the box
    /// is a **reference** — which is the whole of the fix.
    func testACompositeHoldingTheBoxSeesTheReplacement() async {
        let box = PhoneSwappableSource(source("Recordings"))
        let page = PhoneCompositeSource([box])
        let first = await page.libraries().count
        XCTAssertEqual(first, 1)
        box.replace(with: PhoneCompositeSource([source("Recordings"), source("Trips")]))
        let after = await page.libraries().map(\.label)
        XCTAssertEqual(after, ["Recordings", "Trips"])
    }
}
