import Foundation
import XCTest
@testable import DomovoiKit

/// The one gate: whether this phone is offered as a place at all.
///
/// Short, because the type is — and it is the most consequential file of the
/// milestone, since a wrong answer here is either a chip on a phone that
/// cannot answer (the rule broken) or no chip on a phone that can (the
/// milestone invisible).
final class AssistantAvailabilityTests: XCTestCase {

    func testOnlyAnAvailableModelCanChat() {
        XCTAssertTrue(AssistantSupport.phoneCanChat(.available))
        XCTAssertFalse(AssistantSupport.phoneCanChat(.checking))
        for reason in AssistantUnavailableReason.allCases {
            XCTAssertFalse(
                AssistantSupport.phoneCanChat(.unavailable(reason)),
                "\(reason) still means no reply will be written here"
            )
        }
    }

    /// Checking answers **false**, not true: a chip that appears a moment
    /// after Chat opens is better than one that appears and is taken away.
    func testCheckingIsNotAPlaceButIsNotAnAnswerEither() {
        XCTAssertFalse(AssistantSupport.phoneEngine(.checking))
        XCTAssertNil(AssistantSupport.phoneReady(.checking))
        XCTAssertEqual(AssistantSupport.phoneReady(.available), true)
        XCTAssertEqual(AssistantSupport.phoneReady(.unavailable(.notEnabled)), false)
    }

    /// Both of `PlaceFacts`' phone inputs come from the one answer until M4,
    /// and this is the case that would fail the day one of them stops
    /// agreeing — which is what M4 means to do deliberately.
    func testBothPlaceFactsComeFromTheOneAnswer() {
        let on = AssistantPlaceFacts(availability: .available, away: false, serverChatOn: true)
        XCTAssertTrue(on.phoneEngine)
        XCTAssertTrue(on.phoneCapable)

        let off = AssistantPlaceFacts(availability: .unavailable(.deviceNotEligible), away: false, serverChatOn: true)
        XCTAssertFalse(off.phoneEngine)
        XCTAssertFalse(off.phoneCapable)
        XCTAssertEqual(assistantListedPlaces(off), [.server], "nothing changes anywhere on such a phone")
    }

    /// Every reason has a sentence, and they are four different sentences:
    /// "turn it on in Settings" and "this iPhone cannot" are not the same
    /// advice.
    func testEveryUnavailableReasonHasItsOwnSentence() {
        let said = AssistantUnavailableReason.allCases.map { AssistantStrings.unavailableReason($0) }
        XCTAssertEqual(Set(said).count, AssistantUnavailableReason.allCases.count)
        for sentence in said {
            XCTAssertFalse(sentence.isEmpty)
            XCTAssertFalse(
                sentence.hasSuffix("."),
                "it is a clause inside ``modelCouldNotStart(_:)``, not a sentence of its own"
            )
        }
        XCTAssertTrue(
            AssistantStrings.unavailableReason(.modelNotReady).contains("try again"),
            "the one reason that mends itself says so"
        )
    }
}
