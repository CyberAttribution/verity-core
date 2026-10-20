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
