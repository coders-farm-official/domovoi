import XCTest
@testable import DomovoiMacKit

final class TemplateTests: XCTestCase {
    func testFillsEachNameOnce() throws {
        let out = try Template.render("a: {{A}}\nb: {{B}} {{A}}", values: ["A": "1", "B": "two"])
        XCTAssertEqual(out, "a: 1\nb: two 1")
    }

    func testAMissingValueIsAnError() {
        XCTAssertThrowsError(try Template.render("x: {{X}}", values: [:])) { e in
            XCTAssertEqual("\(e)", "no value for {{X}}")
        }
    }

    func testAValueTheTemplateNeverUsesIsAnError() {
        XCTAssertThrowsError(try Template.render("x: {{X}}", values: ["X": "1", "TYPO": "2"]))
    }

    func testAValueIsNeverReadAsATemplate() throws {
        let out = try Template.render("{{A}} {{B}}", values: ["A": "{{B}}", "B": "b"])
        XCTAssertEqual(out, "{{B}} b")
    }

    func testSectionsKeepOrDropTheirLines() throws {
        let t = "top\n  {{#ROLE}}\nrole {{R}}\n  {{/ROLE}}\nend"
        XCTAssertEqual(try Template.render(t, values: ["R": "x"], sections: ["ROLE": true]), "top\nrole x\nend")
        // Off: the value inside is not needed, and not an error when given.
        XCTAssertEqual(try Template.render(t, values: ["R": "x"], sections: ["ROLE": false]), "top\nend")
        XCTAssertEqual(try Template.render(t, values: [:], sections: ["ROLE": false]), "top\nend")
        XCTAssertThrowsError(try Template.render(t, values: [:], sections: [:]), "a section needs an answer")
        XCTAssertThrowsError(try Template.render("{{#A}}\nx", values: [:], sections: ["A": true]), "never closed")
        XCTAssertThrowsError(try Template.render("x", values: [:], sections: ["A": true]), "no such section")
    }

    func testLowerCaseOrOddBracesAreText() throws {
        XCTAssertEqual(try Template.render("{{x}} {{ }} {{A}}", values: ["A": "1"]), "{{x}} {{ }} 1")
    }

    func testYAMLStringsAreJSONQuoted() {
        XCTAssertEqual(Template.yamlString("/Library/Application Support/x"), "\"/Library/Application Support/x\"")
        XCTAssertEqual(Template.yamlString("a\"b\\c\nd\u{1}"), "\"a\\\"b\\\\c\\nd\\u0001\"")
        XCTAssertEqual(Template.yamlString("#: - {x}"), "\"#: - {x}\"")
    }

    func testXMLEscape() {
        XCTAssertEqual(Template.xmlEscape("a<b>&\"c'"), "a&lt;b&gt;&amp;&quot;c&apos;")
    }
}
