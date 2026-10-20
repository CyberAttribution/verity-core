# Credential helper prototype

Build on macOS with:

    xcrun clang -fobjc-arc -framework Foundation -framework Security -framework LocalAuthentication CredentialHelper.m -o verity-credential-helper

The application packager must supply a trusted helper path to CredentialStore.
The helper uses the data protection Keychain, device-only unlocked accessibility,
non-synchronizing items, and a LocalAuthentication context that disallows prompts.
No plaintext or legacy Keychain fallback is provided. Only create/read/delete
are supported; create never silently overwrites an existing item.

This is an unfinished integration. The unsigned development helper compiles on
the MacBook Pro but item creation returns OSStatus -34018 (missing entitlement).
An appropriately signed/entitled application build and successful native
create/read/delete acceptance are required before customer use. Do not describe
mock boundary tests as native Keychain validation. No customer keys have been
entered and no paid inference requests have been issued for this prototype.

Credential references are UUIDs scoped by a profile UUID. Secret records bind
provider and HTTPS origin; the Python adapter validates the binding on read.
These references do not authorize sending data or incurring provider charges.
Customer configuration, UI, rotation/revocation workflows, budget enforcement,
and the governed paid-inference adapter still need integration. Python and the
helper necessarily handle secret plaintext in memory; no same-user malicious
code isolation, authenticated binary identity, or guaranteed memory erasure is
claimed. Distribution signing and helper ownership are packaging requirements.

Create timeouts preserve the opaque reference as safe exception metadata and
report an unknown outcome; they do not trigger a retry. Callers should persist
a newly allocated reference before creation and pass it explicitly. Recovery
must account for an item potentially created before a timeout. Provider origins
are normalized and validated, including IPv6 brackets. This is binding validation,
not authorization of a network destination; paid dispatch needs its own policy.

Signing investigation confirmed an ad hoc linker signature without a team ID,
zero valid code-signing identities on this MacBook, and native create -34018.
Apple TN3137 states data protection Keychain access-group entitlements require
authorization by a provisioning profile embedded in an app-like bundle:
https://developer.apple.com/documentation/technotes/tn3137-on-mac-keychains
No invented team identity, system security changes, or legacy fallback applied.

The Configuration credential page now records version-bound create/removal
operations without secret values. A pending record is committed before native
creation; pending/unknown records block another save and remain recoverable after
restart. Changed configurations block use of old credentials; disabled connections
still permit cleanup of retained old references. Removal is idempotent when the
native helper reports not_found. No route returns a stored credential.

The application packager supplies VERITY_CREDENTIAL_HELPER as an absolute trusted
executable path. Without it, entry is disabled and POST fails closed. Merely
setting that path is not native acceptance, signing verification, provider-key
validation, or authorization for external inference. The unsigned helper remains
blocked by -34018 until the signed/provisioned native acceptance succeeds.
