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

## Prepared Xcode signing target

Open CredentialHelper.xcodeproj with full Xcode. Select the VerityCredentialHelper
target and its Signing & Capabilities tab, then select the actual developer team.
Automatic signing and a default application-specific Keychain group are prepared.
No team identity or provisioning profile is invented or embedded in source.
Use Product > Build. The executable is inside VerityCredentialHelper.app at
Contents/MacOS/VerityCredentialHelper; the package's trusted helper setting must
point to that executable. The application wrapper is required for the profile.

Project, Info.plist and entitlement syntax have passed plutil. This does not
establish successful Xcode provisioning/build or native Keychain access. Team
capability availability must be checked in Xcode for the actual account. Full
Xcode, an authorized signing identity/profile and successful native acceptance
remain prerequisites; command-line clang compilation is a separate check.

validate_keychain_helper.py takes the built helper executable path. It generates
one disposable secret in memory, checks create/read, wrong-origin and profile
binding, delete and missing reads, then attempts cleanup in finally. It prints
only status and recovery IDs and returns nonzero on failure. It does not contact
a provider, print the generated credential, or automatically retry creation.
Retain its JSON if cleanup or completion is unknown. This test does not establish
OS-user separation, authenticated caller identity, release signing or notarization.

## Signed development acceptance — October 20, 2026

The prepared app built successfully with Xcode 16.4 on macOS 15.7.4, using the
operator's Personal Team, an Apple Development certificate and Xcode-managed
provisioning. Strict codesign verification passed. The signed helper passed
synthetic create/read, wrong-origin/profile denial, delete, missing-read and
confirmed cleanup. This supersedes the earlier missing-entitlement blocker for
this signed local development build; the unsigned build still fails closed.

The customer credential HTTP handlers also passed with the real signed helper
in an isolated profile: CSRF denial, save, manager reopen/read, no credential-read
route or echo, changed-configuration denial, disabled-connection cleanup and
idempotent removal. Ordinary profile files contained no generated secret and
cleanup was confirmed. No real provider key or paid API call was used.

The first integration driver used an unsupported synthetic provider and was
correctly rejected before native creation. The corrected driver used a supported
provider label with a dummy HTTPS origin; this was local storage acceptance,
not provider authorization. Evidence and local team settings remain private.

Personal-Team provisioning is development-only and expires. This result does
not establish release signing/notarization, authenticated helper callers,
pristine-account installation, credential rotation, provider key validity or
governed paid dispatch. Package trust and renewal remain required.
