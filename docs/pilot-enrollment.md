# Offline pilot enrollment and first analysis

This is an Apple Silicon pilot for macOS 15 or later. The application includes
its Python backend. It does not require the recipient's Python installation,
and does not include a model or start an inference service. The October 15
two-Mac test used macOS 15.7.4 on an M1 Pro MacBook and macOS 26.1 on a 16 GB
M1 Mac mini. Both were existing user accounts with isolated pilot data, not
pristine operating-system installations.

The current package is ad-hoc signed for controlled pilot testing. Developer ID
signing, notarization, and downloaded/quarantined Gatekeeper acceptance remain
pending. Do not distribute it as a public release or instruct recipients to
disable Gatekeeper. A release administrator must supply the approved package
and its exact SHA-256 through the agreed delivery channel.

## Recipient journey

1. Keep the existing Verity/OpenClaw installation. Extract the pilot ZIP into a
   separate folder. For an engineer's isolated test, set VERITY_DATA_HOME to a
   new private directory before starting its executable. A normal new install
   defaults to ~/Library/Application Support/Verity Pilot/data.
2. Open Installation & Runtime. Copy the generated installation ID to the pilot
   administrator. This local installation identifier is not a hardware identity.
3. The administrator issues a short-lived code for that installation and
   records its metadata. Paste the code into the first-launch activation box.
   A valid code displays the license ID and expiration in UTC. Incorrect,
   wrong-installation, expired, or imported-revoked codes are rejected.
4. Enter your operator label and exact installed model name. Choose either the
   existing OpenClaw v0 service or direct Ollama. Save the loopback addresses,
   then select Test saved connection. The result distinguishes an unreachable
   runtime, an unavailable model inventory, and a missing model. Installed model
   names are shown to help copy the exact name. This check does not load a model
   or prove that inference will succeed.
5. For direct Ollama, use local GGUF weights and disable cloud features on the
   independently operated service. The tested MacBook service used
   OLLAMA_NO_CLOUD=1. The client refuses cloud markers, remote-model metadata,
   redirects, inherited proxies, incomplete generations, and model-name
   substitution. It trusts the configured loopback service; it is not runtime
   attestation or protection against a compromised host.
6. Confirm memory headroom before loading a model. The mini's existing
   qwen2.5:7b-instruct Q4_K_M weights occupied 4.36 GiB on disk. A 4K context
   needed roughly 224 MiB of KV cache; its observed model allocation was about
   4.59 GiB. Initial pressure was normal, with about 165 MiB swap after loading.
   Later, multiple QA app windows plus desktop activity reached warning pressure
   and about 411 MiB swap. Closing the obsolete test app and unloading the test
   model restored normal pressure. The connection test now shows weight size
   and this Mac memory pressure, with guidance to close apps or choose a smaller
   model. It does not guarantee fit. Run one inference at a time and prefer a
   smaller model when normal desktop work leaves limited headroom.
   The MacBook test instead used qwen2.5:3b-instruct Q4_K_M (1.80 GiB weights).
   These measurements are not a blanket guarantee for other contexts or apps.
7. Open Analyze and submit a small, non-sensitive specimen. Inspect the finding
   and Active Agents. The single-analysis request boundary is recorded locally
   without prompts, responses, or tool arguments in its activity history.
   Activity observations are separate from authorization and runtime health.
8. Select Preserve for decision review. This durably snapshots the analysis.
   An unpreserved analysis report is an in-memory work item and may disappear
   on restart. An approved action never means the model can execute arbitrary
   tools: this pilot's controlled action is only the fixed local simulator.
9. Export decision history and save its checkpoint separately. Verify with:
   PYTHONPATH=/path/to/source python -m verity_core.trust.verify_decision
   run.decision.json --checkpoint retained.checkpoint.json
   The verifier uses the standard library and runs without the application.
10. Send the administrator the installation ID and agreed first-analysis
    export/checkpoint, if authorized for that evidence. The administrator
    associates them with the issued license. The association is operator
    supplied; integrity verification does not prove which machine created it.

## Administrator issuance and tracking

Keep the Ed25519 private issuer key outside source, packages, reports, and
recipient machines. Bundle only its public verification key. Store the local
registry and code artifacts in a private administrator directory. Use a new
license ID for every issuance, including replacements and expired pilots.

```sh
PYTHONPATH=. python tools/pilot_enrollment.py issue \
  --registry /private/pilot/enrollment.db --private-key /private/issuer.pem \
  --installation-id install-ID --license-id pilot-ID --days 7 \
  --output /private/pilot/issued/pilot-ID.code
```

Existing codes may be adopted using the same public issuer key and their
owner-only code file. Adoption currently accepts valid, unexpired codes.
An issuance already in the registry retains its history after expiration.

```sh
PYTHONPATH=. python tools/pilot_enrollment.py adopt \
  --registry /private/pilot/enrollment.db --public-key /private/public.pem \
  --code-file /private/existing.code
```

After a first-analysis export has been independently verified:

```sh
PYTHONPATH=. python tools/pilot_enrollment.py ack \
  --registry /private/pilot/enrollment.db --license-id pilot-ID \
  --installation-id install-ID --export run.decision.json \
  --checkpoint retained.checkpoint.json
```

Status lists issuance/export state, expiry, replacement links, acknowledgment
reports, and revocation-list issuance separately. No contact is sent and no
remote installation discovery occurs.

## Issuance failure and replacement

The registry durably reserves metadata and the code fingerprint before writing
the private code file. A crash or filesystem failure may leave a visible
pending_reconciliation issuance. This is not a cross-filesystem transaction.
If the complete file exists, use reconcile. If it is absent, retry-export uses
the retained issuer key and original canonical claims to regenerate the exact
same Ed25519 code. A wrong key is refused by the reserved fingerprint. If a partial artifact exists, add --preserve-partial to move it to a unique
private backup name before re-exporting. The tool does not overwrite it or
silently reuse the license ID. Losing the original issuer key prevents exact
re-export; retain that key securely for the pilot lifetime.

```sh
PYTHONPATH=. python tools/pilot_enrollment.py retry-export \
  --registry /private/pilot/enrollment.db --license-id pilot-ID \
  --private-key /private/issuer.pem
```

To replace, issue a new license ID with --replace OLD-LICENSE-ID for the same
installation. Replace the latest successor, not an earlier link; forks and
replacement of unfinished issuances are refused. Replacement does not disable
the old code. The old code remains valid until its signed expiry or until that
installation imports a signed revocation list covering it.

## Offline revocation boundaries

Use the existing pilot_entitlement.py revoke command to produce a signed
cumulative list with an advancing revision. Record its issuance using
pilot_enrollment.py record-revocations and the public key. Import the list in
the recipient's setup page. Earlier IDs must remain in every newer list.
The current pilot schema caps a list at 100 license IDs.

The administrator registry cannot push a list, force an import, detect a client
that ignored it, or revoke a code remotely. List issuance and delivery do not
establish effective revocation. The app rejects a listed code after import and
retains that list across relaunch. Expiry and clock-rollback checks use local
state and the system clock. A host owner can copy or remove installation state;
the code is installation-ID-bound, not hardware-bound. This is offline pilot
entitlement enforcement, not tamper-proof DRM, authentication, or attestation.
Activation also grants no per-job cloud-evidence permission.

## Preserving the existing Mac mini

The separate test lives under
/Users/swarmtracker/swarmtracker-pilot-tests/2026-10-15.
The original /Users/swarmtracker/openclaw-v0 service and configuration are
preserved. Its normal inference log naturally receives the test requests.
VERITY_READONLY_RUNTIME_LEDGER can point the pilot at that existing log for
read-only participant observation. Pilot databases and activation state stay in
the separate data directory. Do not replace the original application or point
the pilot's writable data home at the original installation.
