import CryptoKit
import Foundation

// Makes and uses the key that signs releases. The private key stays out of the repository.
//   release_key generate <key file>        prints the public key, for LiveCapUpdateKey in Info.plist
//   release_key public <key file>          prints the public key again
//   release_key sign <key file> <file>     prints the file's signature

func fail(_ message: String) -> Never {
    FileHandle.standardError.write(Data((message + "\n").utf8))
    exit(1)
}

func loadKey(_ path: String) -> Curve25519.Signing.PrivateKey {
    guard let text = try? String(contentsOfFile: path, encoding: .utf8),
          let raw = Data(base64Encoded: text.trimmingCharacters(in: .whitespacesAndNewlines)),
          let key = try? Curve25519.Signing.PrivateKey(rawRepresentation: raw) else { fail("Couldn't read the signing key at \(path)") }
    return key
}

let arguments = CommandLine.arguments
guard arguments.count >= 3 else { fail("usage: release_key generate|public|sign <key file> [file]") }
let keyPath = arguments[2]

switch arguments[1] {
case "generate":
    if FileManager.default.fileExists(atPath: keyPath) { fail("\(keyPath) already exists, and replacing it would stop installed copies from updating.") }
    let key = Curve25519.Signing.PrivateKey()
    let folder = (keyPath as NSString).deletingLastPathComponent
    try FileManager.default.createDirectory(atPath: folder, withIntermediateDirectories: true, attributes: [.posixPermissions: 0o700])
    guard FileManager.default.createFile(atPath: keyPath, contents: Data(key.rawRepresentation.base64EncodedString().utf8),
                                         attributes: [.posixPermissions: 0o600]) else { fail("Couldn't write \(keyPath)") }
    print(key.publicKey.rawRepresentation.base64EncodedString())
case "public":
    print(loadKey(keyPath).publicKey.rawRepresentation.base64EncodedString())
case "sign":
    guard arguments.count == 4, let file = FileHandle(forReadingAtPath: arguments[3]) else { fail("Couldn't read the file to sign") }
    var hash = SHA256()
    while let chunk = try file.read(upToCount: 1 << 20), !chunk.isEmpty { hash.update(data: chunk) }
    print(try loadKey(keyPath).signature(for: Data(hash.finalize())).base64EncodedString())
default:
    fail("usage: release_key generate|public|sign <key file> [file]")
}
