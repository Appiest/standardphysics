import XCTest
@testable import StandardPhysics

final class SessionOwnerTests: XCTestCase {
    func testATeamGuestSessionDecodes() throws {
        let owner = try decode("""
        {"owner_id": "2F1D6E1E-8D0B-4C54-9E0A-3C6B1B8F2A10", "email": null, "shop_name": "My shop",
         "role": "team", "guest": true, "deletes_at": "2026-10-26T10:00:00Z"}
        """)

        XCTAssertTrue(owner.isTeam)
        XCTAssertTrue(owner.guest)
        XCTAssertNil(owner.email)
        XCTAssertEqual(owner.deletesAt, "2026-10-26T10:00:00Z")
    }

    func testAnOwnerSavedBeforeRolesExistedIsAnOwner() throws {
        let owner = try decode(#"{"email": "tea@example.com", "shop_name": "Tea House"}"#)

        XCTAssertFalse(owner.isTeam)
        XCTAssertFalse(owner.guest)
        XCTAssertEqual(owner.email, "tea@example.com")
    }

    func testARoleThePhoneDoesNotKnowIsAnOwner() throws {
        XCTAssertFalse(try decode(#"{"email": "a@example.com", "shop_name": "A", "role": "admin"}"#).isTeam)
    }

    func testASavedOwnerSurvivesTheRoundTrip() throws {
        let owner = SessionStore.Owner(email: "a@example.com", shopName: "A", role: .team)
        let decoded = try JSONDecoder().decode(SessionStore.Owner.self, from: JSONEncoder().encode(owner))

        XCTAssertEqual(decoded, owner)
    }

    private func decode(_ json: String) throws -> SessionStore.Owner {
        try JSONDecoder().decode(SessionStore.Owner.self, from: Data(json.utf8))
    }
}
