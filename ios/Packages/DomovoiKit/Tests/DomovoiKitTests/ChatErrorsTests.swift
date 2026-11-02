import Foundation
import XCTest
@testable import DomovoiKit

/// Why a reply failed, and the one refusal that is not a reply's — against
/// `ChatErrorsTest.kt` and `AiOffRefusalTest.kt`, which specify both on the
/// JVM.
final class ChatErrorsTests: XCTestCase {

    // MARK: - a reply's own error

    func testNoErrorIsNoNote() {
        XCTAssertNil(chatErrorView(error: nil, code: nil, model: nil))
        XCTAssertNil(chatErrorView(error: "", code: "model_missing", model: nil))
        XCTAssertNil(chatErrorView(error: "   ", code: "model_missing", model: nil))
    }

    /// Everything but the vision refusal is the **server's own sentence**,
    /// passed through untouched.
    func testTheServersSentenceIsPassedThroughUntouched() {
        let view = chatErrorView(error: "the model is not installed", code: "model_missing", model: "llama3.2")
        XCTAssertEqual("the model is not installed", view?.text)
        XCTAssertEqual("model_missing", view?.code)
        XCTAssertTrue(view?.models ?? false, "a model to install is fixed in Settings → Models")
    }

    /// The vision refusal is worded here, and it names the model when the
    /// server named one.
    func testTheVisionRefusalIsWordedByTheApp() {
        let named = chatErrorView(
            error: "whatever the server said",
            code: "vision_model_missing",
            model: "llava:7b"
        )
        XCTAssertEqual(visionMissingText("llava:7b"), named?.text)
        XCTAssertTrue(named?.text.contains("llava:7b") ?? false)
        let unnamed = chatErrorView(error: "x", code: "vision_model_missing", model: nil)
        XCTAssertEqual(visionMissingText(nil), unnamed?.text)
        XCTAssertTrue(unnamed?.text.contains("none is set") ?? false)
        // A blank name is treated as none.
        XCTAssertEqual(visionMissingText(nil), visionMissingText("   "))
    }

    /// An error with **no code** is one the server never made — this phone's
    /// own "send failed (offline?)" — and it points at no setting.
    func testAnErrorWithNoCodePointsAtNoSetting() {
        let view = chatErrorView(error: "send failed (offline?)", code: nil, model: nil)
        XCTAssertEqual("send failed (offline?)", view?.text)
        XCTAssertNil(view?.code)
        XCTAssertFalse(view?.models ?? true)
    }

    /// The four codes whose fix is a model, and a fifth that is not one of
    /// them — asserted as a whole set, so a code added to the server without
    /// being added here is visible.
    func testTheModelCodesAreTheFourTheServerSends() {
        XCTAssertEqual(
            Set(["vision_model_missing", "model_missing", "vision_unsupported", "model_memory"]),
            ChatErrors.modelCodes
        )
        XCTAssertFalse(
            chatErrorView(error: "still answering the last one", code: "busy", model: nil)?.models ?? true
        )
    }

    // MARK: - is a picture sure to be refused?

    /// `ChatErrorsTest.kt`'s `visionMissing`, case for case.
    func testWhetherAPictureIsSureToBeRefused() {
        XCTAssertFalse(visionMissing(nil), "not knowing is not a refusal")
        XCTAssertFalse(
            visionMissing(ChatModelsInfo(installed: [], visionModel: "llava:7b")),
            "an empty list means Ollama could not be asked"
        )
        XCTAssertTrue(
            visionMissing(ChatModelsInfo(installed: ["llama3.2:latest"], visionInstalled: false)),
            "the server said so"
        )
        XCTAssertFalse(
            visionMissing(ChatModelsInfo(installed: [], visionInstalled: true)),
            "the server said so, and its word beats the list"
        )
        XCTAssertTrue(
            visionMissing(ChatModelsInfo(installed: ["llama3.2:latest"], visionModel: nil)),
            "a house with models and no vision model set"
        )
        XCTAssertTrue(
            visionMissing(ChatModelsInfo(installed: ["llama3.2:latest"], visionModel: "llava:7b")),
            "set, and not installed"
        )
        XCTAssertFalse(
            visionMissing(ChatModelsInfo(installed: ["LLaVA"], visionModel: "llava:latest")),
            "a tag-less name matches its :latest, and case does not count"
        )
    }

    func testAModelKeyIsWhatOllamaCallsIt() {
        XCTAssertEqual("llava:latest", modelKey("LLaVA"))
        XCTAssertEqual("llava:7b", modelKey("  LLaVA:7b  "))
        XCTAssertEqual("", modelKey("   "))
    }

    // `testWhetherTheHouseCanAnswerAChatAtAll` went with `serverHasChatModel`
    // itself: it was the function's only reader, which is what made the
    // function dead surface rather than a seam (`ChatErrors.swift`).

    // MARK: - the household's AI switch

    /// `AiOffRefusalTest.kt`, with the wording asserted whole.
    func testTheAiOffRefusalIsTheHeaderOrA409ThatCarriesTheSentence() {
        XCTAssertEqual("AI chat is turned off on your home server", aiChatOffText)
        XCTAssertTrue(isAiOffRefusal(status: 409, refusal: "ai-off", body: nil))
        XCTAssertTrue(isAiOffRefusal(status: 200, refusal: "AI-OFF", body: nil), "case does not count")
        XCTAssertTrue(isAiOffRefusal(status: 409, refusal: nil, body: #"{"detail":"\#(aiOffDetail), sorry"}"#))
        XCTAssertFalse(
            isAiOffRefusal(status: 409, refusal: nil, body: #"{"detail":"still answering the last one"}"#),
            "a 409 for anything else is not this"
        )
        XCTAssertFalse(
            isAiOffRefusal(status: 409, refusal: nil, body: #"{"detail":"this reply can't be tried again"}"#),
            "nor is a retry refusal"
        )
        XCTAssertFalse(
            isAiOffRefusal(status: 403, refusal: nil, body: aiOffDetail),
            "the sentence alone is read only on a 409"
        )
        XCTAssertFalse(isAiOffRefusal(status: 409, refusal: "internet-off", body: nil))
        XCTAssertFalse(isAiOffRefusal(status: 500, refusal: nil, body: nil))
    }

    /// The same question of a thrown error, which is the form the model asks
    /// it in: ``ApiFailure/refusal`` is the header as the response carried it,
    /// so there is no second channel to wire.
    func testTheRefusalIsReadStraightOffADomovoiError() {
        let header = DomovoiError.httpError(ApiFailure(
            status: 409, message: "409: …", body: #"{"detail":"no"}"#, refusal: "ai-off"
        ))
        XCTAssertTrue(isAiOffRefusal(header))
        let viaBody = DomovoiError.httpError(ApiFailure(
            status: 409, message: "409: …", body: aiOffDetail
        ))
        XCTAssertTrue(isAiOffRefusal(viaBody))
        XCTAssertFalse(isAiOffRefusal(.unreachable("nothing answered")))
        XCTAssertFalse(isAiOffRefusal(.noServer))
        XCTAssertFalse(isAiOffRefusal(.httpError(ApiFailure(status: 409, message: "409: busy", body: "busy"))))
    }

    // MARK: - what a send that was refused says

    func testAnAiOffSendSaysSoInsteadOfAStatus() {
        XCTAssertEqual(aiChatOffText, sendFailureLine(status: 409, refusal: "ai-off", body: nil))
        XCTAssertEqual(
            aiChatOffText,
            sendFailureLine(status: 409, refusal: nil, body: #"{"detail":"\#(aiOffDetail)"}"#)
        )
    }

    /// **The one place this port's wording differs from Android's.**
    ///
    /// Android says `"$status $reason"` using OkHttp's reason phrase, which
    /// Foundation does not have at all, so the same branch would read "409"
    /// and nothing else. The server's own `detail` is read instead, so a retry
    /// refusal reads as a sentence rather than a number.
    func testARefusalThatIsNotTheAiSwitchSaysTheServersOwnDetail() {
        XCTAssertEqual(
            "this reply can't be tried again",
            sendFailureLine(
                status: 409,
                refusal: nil,
                body: #"{"detail":"this reply can't be tried again","code":"not_retryable"}"#
            )
        )
        XCTAssertEqual(
            "thread not found",
            sendFailureLine(status: 404, refusal: nil, body: #"{"detail":"thread not found"}"#)
        )
    }

    /// A refusal with no sentence in it falls back to this app's own words —
    /// never to a bare number, which is what ``aiChatOffText`` exists to
    /// avoid.
    func testARefusalWithNoSentenceFallsBackToWords() {
        XCTAssertEqual(sendRefusedFallback(503), sendFailureLine(status: 503, refusal: nil, body: nil))
        XCTAssertEqual(sendRefusedFallback(500), sendFailureLine(status: 500, refusal: nil, body: "<html>"))
        XCTAssertEqual(
            sendRefusedFallback(422),
            sendFailureLine(status: 422, refusal: nil, body: #"{"detail":[{"loc":["body"],"msg":"too long"}]}"#),
            "a 422's detail is a validation report, not a sentence"
        )
        XCTAssertEqual(
            sendRefusedFallback(409),
            sendFailureLine(status: 409, refusal: nil, body: #"{"detail":"   "}"#)
        )
        XCTAssertTrue(sendRefusedFallback(503).contains("503"), "and it still says which")
    }

    /// A transport failure is not a refusal: it goes through
    /// ``failureText(_:_:)``, so Chat's offline toast reads like every other
    /// write's on this phone.
    func testATransportFailureReadsLikeEveryOtherWritesDoes() {
        XCTAssertEqual(
            failureText("send", .unreachable("nothing answered")),
            sendFailureLine(.unreachable("nothing answered"))
        )
        XCTAssertEqual("send failed (offline?)", sendFailureLine(.unreachable("x")))
        XCTAssertEqual("try again failed (offline?)", sendFailureLine(.noServer, verb: "try again"))
    }

    func testTheServersDetailIsReadOnlyWhenItIsASentence() {
        XCTAssertEqual("because", serverDetail(in: #"{"detail":" because "}"#))
        XCTAssertNil(serverDetail(in: nil))
        XCTAssertNil(serverDetail(in: ""))
        XCTAssertNil(serverDetail(in: "not json"))
        XCTAssertNil(serverDetail(in: #"{"detail":null}"#))
        XCTAssertNil(serverDetail(in: #"{"something":"else"}"#))
    }
}
