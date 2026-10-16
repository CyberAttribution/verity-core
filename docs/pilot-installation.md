# Native pilot installation and activation

The pilot builder packages Python and the backend dependencies inside the existing AppKit/WKWebView shell. It preserves the development builder. The current pilot is Apple Silicon only and requires macOS 15 or later because the bundled Python library has an LC_BUILD_VERSION minimum of 15.0. Local acceptance used macOS 15.7.4. Intel support and older macOS versions need separate builds and checks.

## Build

Use a dedicated build environment with the project's desktop extra, Pillow and PyInstaller installed. The October 15 environment used Python 3.14.7 and PyInstaller 6.22.3. A source dependency lock and build manifest accompany the session evidence. No model weights, private issuer key or provider credentials belong in the app.

```
python apps/desktop/package.py --repo /absolute/verity-core \
  --output /new/path/Verity.app --activation-public-key /private/activation-public.pem
```

The builder records the source commit, dirty status and a source-files SHA-256, rejects an existing output, and stages in an owned unique directory. It applies an isolated compiler VFS overlay only if identical duplicate SwiftBridging module declarations are detected; system compiler files are not edited. Relative bundle paths allow relocation. The frozen worker uses an owned ephemeral loopback socket and the shell stops only its own worker.

The output is ad-hoc signed and signature-verified for local pilot testing. This is not Developer ID signing or notarization. No usable Developer ID signing identity was present in the October 15 MacBook keychain. Download quarantine/Gatekeeper distribution behavior remains unverified. Do not describe the candidate as a public distributable release.

## First launch

A bundled launch requires activation and opens setup while inactive. The setup page shows a newly generated installation ID. An administrator issues an Ed25519-signed code bound to that installation, with an expiry. Invalid signatures, wrong installation IDs, expired codes and imported revoked license IDs are refused. Replacement codes can use a new license ID. The CLI defaults to seven days and bounds pilot validity to 1–30 days.

Generate an issuer pair once, outside source and exports:

```
PYTHONPATH=. python tools/pilot_entitlement.py init \
  --private-key /private/issuer.pem --public-key /private/activation-public.pem
PYTHONPATH=. python tools/pilot_entitlement.py issue \
  --private-key /private/issuer.pem --installation-id install-ID \
  --license-id pilot-ID --days 7 --output /private/new-code.txt
```

Only the public key is bundled. Keep the private key owner-only and separately backed up. Do not print or commit it. Activation is offline; there is no online activation/revocation server. To revoke before expiry, issue and import a signed cumulative revocation list with an advancing revision. There is no instantaneous remote revocation while offline. Clock rollback checks use retained local state and are best effort, not trusted time, hardware attestation or tamper-proof DRM. Removing or modifying local state is outside that security claim.

Setup accepts operator label, model and loopback OpenClaw/Ollama addresses. Configuration is validated, durably saved owner-only via atomic replacement, then applied. A remote runtime needs a separately established private tunnel; setup does not install inference models. Product activation does not grant per-job cloud-evidence authorization or alter existing cloud gates.

Data defaults to `~/Library/Application Support/Verity/data`. `VERITY_DATA_HOME` supports isolated testing. `VERITY_SMOKE_DIR` redirects internal native smoke evidence, and `--smoke-test --smoke-agents` renders the Agents page and quits. Do not collect or publish screenshots with user data.

## Verified scope and remaining checks

The packaged-backend acceptance exercised activation gating, a real signed code, rejection before approval, approved fixed simulation, export, and display of the real development-monitoring histories with a system-only PATH, cleared Python paths and cwd /tmp. The decision remains explicitly synthetic. No real local analysis runtime was available on this MacBook; real analysis/export through a native local runtime remains pending. Optional media tools such as ffmpeg/exiftool are not bundled and retain honest unavailable diagnostics. A clean separate-account installation, upgrade/migration checks and Developer ID/notarization remain required before a design-partner release.
