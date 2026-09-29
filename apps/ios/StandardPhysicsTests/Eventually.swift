import XCTest

extension XCTestCase {
    /// Polls until `condition` holds, failing the test if it never does.
    ///
    /// The default ceiling is long because a loaded CI simulator can take seconds to
    /// deliver a stubbed response. A passing test returns as soon as the condition holds,
    /// so the ceiling only ever costs time on a failure.
    @MainActor
    func waitUntil(
        timeout: TimeInterval = 15,
        file: StaticString = #filePath,
        line: UInt = #line,
        _ condition: @escaping () -> Bool
    ) async throws {
        let deadline = Date().addingTimeInterval(timeout)
        while !condition() {
            if Date() >= deadline {
                XCTFail("Timed out after \(timeout) s", file: file, line: line)
                return
            }
            try await Task.sleep(for: .milliseconds(10))
        }
    }
}
