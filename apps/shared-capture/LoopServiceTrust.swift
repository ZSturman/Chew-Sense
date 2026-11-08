import CryptoKit
import Foundation
import Security

/// Accepts only the pinned self-signed certificate; with no pin (pairing), it
/// records the presented fingerprint for the HMAC binding.
nonisolated final class PinnedTrust: NSObject, URLSessionDelegate, @unchecked Sendable {
    private let lock = NSLock()
    private var _expected: String?
    private var _observed: String?
    private var _pinFailed = false

    var expected: String? {
        get { lock.withLock { _expected } }
        set { lock.withLock { _expected = newValue; _pinFailed = false } }
    }
    var observed: String? { lock.withLock { _observed } }
    var pinFailed: Bool { lock.withLock { _pinFailed } }

    func urlSession(_ session: URLSession, didReceive challenge: URLAuthenticationChallenge,
                    completionHandler: @escaping (URLSession.AuthChallengeDisposition, URLCredential?) -> Void) {
        guard challenge.protectionSpace.authenticationMethod == NSURLAuthenticationMethodServerTrust,
              let trust = challenge.protectionSpace.serverTrust,
              let chain = SecTrustCopyCertificateChain(trust) as? [SecCertificate],
              let leaf = chain.first else {
            completionHandler(.cancelAuthenticationChallenge, nil)
            return
        }
        let der = SecCertificateCopyData(leaf) as Data
        let fingerprint = SHA256.hash(data: der).map { String(format: "%02x", $0) }.joined()
        let pin = expected
        lock.withLock { _observed = fingerprint }
        if let pin, pin != fingerprint {
            lock.withLock { _pinFailed = true }
            completionHandler(.cancelAuthenticationChallenge, nil)
            return
        }
        completionHandler(.useCredential, URLCredential(trust: trust))
    }
}

/// The device token for the loop service, in this app's own keychain.
nonisolated enum Keychain {
    private static let service = "ChewSenseLoop"
    private static let account = "device-token"

    struct StoreError: LocalizedError {
        let status: OSStatus
        var errorDescription: String? { "Could not store the pairing token (\(status))." }
    }

    static func save(token: String) throws {
        delete()
        let status = SecItemAdd([
            kSecClass: kSecClassGenericPassword, kSecAttrService: service, kSecAttrAccount: account,
            kSecAttrAccessible: kSecAttrAccessibleAfterFirstUnlockThisDeviceOnly,
            kSecValueData: Data(token.utf8),
        ] as CFDictionary, nil)
        guard status == errSecSuccess else { throw StoreError(status: status) }
    }

    static func token() -> String? {
        var item: CFTypeRef?
        let status = SecItemCopyMatching([
            kSecClass: kSecClassGenericPassword, kSecAttrService: service, kSecAttrAccount: account,
            kSecReturnData: true,
        ] as CFDictionary, &item)
        guard status == errSecSuccess, let data = item as? Data else { return nil }
        return String(data: data, encoding: .utf8)
    }

    static func delete() {
        SecItemDelete([kSecClass: kSecClassGenericPassword, kSecAttrService: service,
                       kSecAttrAccount: account] as CFDictionary)
    }
}
