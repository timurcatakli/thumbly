import Foundation

// MARK: - Models (every field optional so new server fields or missing ones never break decoding)

struct Overview: Decodable {
    var name: String?
    var bound: Bool?
    var cable: Bool?
    var usb_pairing: Pairing?

    struct Pairing: Decodable {
        var state: String?
        var device: String?
        var last_error: JSON?
        var retry_in: Double?
    }
}

struct Info: Decodable {
    var name: String?
    var version: String?
    var issuer: String?
    var mcp_url: String?
    var public_url: String?
    var scopes: [String]?
}

struct Status: Decodable {
    var device: String?
    var setup_required: Bool?
    var connection: Connection?
    var control: Control?
    var pending_consents: Int?

    struct Connection: Decodable {
        var state: String?
        var transport: String?
        var message: String?
        var product_version: String?
        var developer_mode: Bool?
        var ddi_mounted: Bool?
        var device_present: Bool?
    }

    struct Control: Decodable {
        var active: Bool?
        var owned: Bool?
        var paused: Bool?
        var remaining_seconds: Double?
        var queue_depth: Int?
    }
}

struct Consent: Decodable, Identifiable {
    var handle: String
    var client_name: String?
    var client_id: String?
    var scopes: [String]?
    var issuer: String?
    var expires_in: Double?
    var redirect_hosts: [String]?
    var id: String { handle }
}

struct AIClient: Decodable, Identifiable {
    var client_id: String
    var client_name: String?
    var approved: Bool?
    var scopes: [String]?
    var last_used: Double?
    var active_until: JSON?
    var renewed_while_used: Bool?
    var never_expires: Bool?
    var isSelf: Bool?
    var id: String { client_id }

    enum CodingKeys: String, CodingKey {
        case client_id, client_name, approved, scopes, last_used, active_until, renewed_while_used, never_expires
        case isSelf = "self"
    }
}

struct Operation: Decodable, Identifiable {
    var id: String
    var kind: String?
    var client: String?
    var state: String?
    var created: Double?
    var updated: Double?
    var error_code: String?
    var app: String?
    var url: String?
}

struct OperationsPage: Decodable {
    var operations: [Operation]?
    var next_cursor: String?
    var has_more: Bool?
    var total: Int?
}

struct Discover: Decodable {
    struct USB: Decodable { var udid: String? }
    var usb: [USB]?
}

/// Any JSON value, for fields whose shape varies.
enum JSON: Decodable {
    case string(String), number(Double), bool(Bool), object([String: JSON]), array([JSON]), null

    init(from decoder: Decoder) throws {
        let c = try decoder.singleValueContainer()
        if c.decodeNil() { self = .null }
        else if let v = try? c.decode(Bool.self) { self = .bool(v) }
        else if let v = try? c.decode(Double.self) { self = .number(v) }
        else if let v = try? c.decode(String.self) { self = .string(v) }
        else if let v = try? c.decode([String: JSON].self) { self = .object(v) }
        else { self = .array(try c.decode([JSON].self)) }
    }

    subscript(key: String) -> JSON? { if case .object(let o) = self { o[key] } else { nil } }
    var string: String? { if case .string(let s) = self { s } else { nil } }
    var number: Double? { if case .number(let n) = self { n } else { nil } }
}

struct APIError: LocalizedError {
    var code: String
    var message: String
    var errorDescription: String? { message.isEmpty ? code : message }
}

// MARK: - Client

struct APIClient: Sendable {
    let base: URL

    static let shared = APIClient(base: URL(string: ProcessInfo.processInfo.environment["THUMBLY_API_URL"]
        ?? "http://127.0.0.1:8770")!)

    private static let session: URLSession = {
        let config = URLSessionConfiguration.ephemeral
        config.timeoutIntervalForRequest = 8
        return URLSession(configuration: config)
    }()

    func overview() async throws -> Overview { try await get("/api/overview") }
    func info() async throws -> Info { try await get("/api/info") }
    func status() async throws -> Status { try await get("/api/status") }
    func discover() async throws -> Discover { try await get("/api/discover") }

    func consents() async throws -> [Consent] {
        struct R: Decodable { var consents: [Consent]? }
        return try await (get("/api/consents") as R).consents ?? []
    }

    func clients() async throws -> [AIClient] {
        struct R: Decodable { var clients: [AIClient]? }
        return try await (get("/api/clients") as R).clients ?? []
    }

    func operations(limit: Int = 25, cursor: String? = nil, actionsOnly: Bool = true) async throws -> OperationsPage {
        var path = "/api/operations?limit=\(limit)" + (actionsOnly ? "&actions=1" : "")
        if let cursor, let enc = cursor.addingPercentEncoding(withAllowedCharacters: .urlQueryAllowed.subtracting(["&", "+", "="])) {
            path += "&cursor=\(enc)"
        }
        return try await get(path)
    }

    func diagnostics() async throws -> Data { try await raw("GET", "/api/diagnostics", body: nil) }

    func pause() async throws { try await post("/api/pause", [:]) }
    func resume() async throws { try await post("/api/resume", [:]) }
    func clearOperations() async throws { try await post("/api/operations/clear", [:]) }
    func decide(handle: String, allow: Bool, window: String) async throws {
        let h = handle.addingPercentEncoding(withAllowedCharacters: .urlPathAllowed.subtracting(["/"])) ?? handle
        try await post("/api/consents/\(h)", ["decision": allow ? "allow" : "deny", "window": window])
    }
    func revoke(clientID: String) async throws {
        let id = clientID.addingPercentEncoding(withAllowedCharacters: .urlPathAllowed.subtracting(["/"])) ?? clientID
        try await post("/api/clients/\(id)/revoke", [:])
    }
    func showDeveloperModeSwitch(udid: String) async throws {
        try await post("/api/setup/developer_mode", ["payload": ["udid": udid, "owner_confirmed": true]])
    }
    func unbind() async throws { try await post("/api/setup/unbind", [:]) }
    func reset(name: String) async throws { try await post("/api/reset", ["name": name]) }

    // MARK: plumbing

    private func get<T: Decodable>(_ path: String) async throws -> T {
        try JSONDecoder().decode(T.self, from: try await raw("GET", path, body: nil))
    }

    private func post(_ path: String, _ body: [String: Any]) async throws {
        _ = try await raw("POST", path, body: try JSONSerialization.data(withJSONObject: body))
    }

    private func raw(_ method: String, _ path: String, body: Data?) async throws -> Data {
        var req = URLRequest(url: URL(string: path, relativeTo: base)!)
        req.httpMethod = method
        req.setValue("application/json", forHTTPHeaderField: "Accept")
        if method != "GET" {
            req.setValue("dashboard", forHTTPHeaderField: "x-thumbly-client")
            req.setValue("application/json", forHTTPHeaderField: "Content-Type")
            req.httpBody = body
        }
        let (data, response) = try await Self.session.data(for: req)
        let code = (response as? HTTPURLResponse)?.statusCode ?? 0
        guard (200..<300).contains(code) else {
            struct E: Decodable { struct Inner: Decodable { var code: String?; var message: String? }; var error: Inner? }
            let e = (try? JSONDecoder().decode(E.self, from: data))?.error
            throw APIError(code: e?.code ?? "HTTP_\(code)", message: e?.message ?? "Request failed (\(code))")
        }
        return data
    }
}
