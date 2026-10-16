# Verity development Mac app

A bundled Apple Silicon pilot builder and offline enrollment now accompany this development shell. See [pilot installation](../../docs/pilot-installation.md) for its macOS 15 minimum, build and verification limits. The development builder below remains available.

This is a native AppKit/WKWebView shell for the existing Node interface.
It launches an owned local HTTP worker on an OS-assigned 127.0.0.1 port and
closes only that worker when the window/app closes. Readiness is correlated
with a per-launch nonce. The page has no native command bridge. Files are
chosen through a native file picker; exports use a native Save panel.
The app inherits the operator's existing Google SDK environment without
printing or storing credential/evidence diagnostics.

This development bundle requires the configured external repo and Python
environment. It is not a signed/notarized or independently distributable app.
The current build targets Apple Silicon and macOS 12 or later.

Install the repo with its desktop extra, then build:

    python -m pip install -e ".[desktop]"
    python apps/desktop/build.py --output /absolute/path/Verity.app \
      --repo /absolute/path/verity-core --python /absolute/path/venv/bin/python \
      --icon /absolute/path/verity-core/apps/desktop/assets/AppIcon.icns

Use --overwrite only for an app built by this builder. Unrelated app output
and unrelated staging folders are never deleted. The Mac mini's Homebrew
Python needed current Expat; --python-library-path points to that library
for this child process only, without changing shell or system Python settings.

Runtime architecture: the app/backend and Google client SDK can run on this
Mac mini; the intended OpenClaw runtime is on the other Mac mini. This shell
does not start, install or select an inference model. Its existing Node
backend still expects loopback OpenClaw/Ollama addresses. On October 13, a loopback SSH tunnel from the M2 Pro to the M1 was configured and synthetic inference was verified. This is a session connection; automatic startup and reconnection are not yet installed. Remote ledger files are not forwarded by the tunnel. The current Google transport uses Vertex REST requests with google-auth
Application Default Credentials through the vendored optional adapter.
It is not a Google Gen AI SDK or Google ADK runtime implementation.
Google is a governed supplement: text intake must complete local OpenClaw
analysis before cloud assistance; there is no standalone Google-only mode.

Google cloud assistance remains behind the existing strict per-job intent,
policy, bounded selection and mandatory authorization-ledger gates.
Switching runtime adapters must preserve those gates. Startup does not imply
that an inference runtime is connected or that cloud use is authorized.
Missing runtime/provider states are shown honestly; no analysis is fabricated.

The temporary icon is a tighter-framed image edit of the user's supplied
AAjr_EYE_03.png. The edit preserves the recognizable eye/network design.
It is an AI framing edit, not a pixel-exact crop. Source prompt: enlarge and
center the complete eye/network mark to approximately 88% of a square black
canvas, preserving blue/white/black design, connections and proportions,
with no text or new objects. Built-in image editing produced the PNG;
macOS sips/iconutil created ICNS packaging sizes.

Validation includes actual worker startup, local sample HTTP response,
SIGTERM/SIGINT shutdown and released port on Mac; native compilation,
dashboard rendering, JavaScript evaluation, snapshot and quit/child cleanup.
It does not certify interactive Save-panel completion or an unconfigured
live inference/cloud workflow. Run:

    python -m pytest -q

Mac Python 3.13 accepted excessively nested JSON rejected by Python 3.10.
An explicit 128-container depth limit now protects trust loading and mandatory
ledger tail validation before JSON parsing, preserving duplicate-key and
finite-value hooks. Original zero-provider-call regressions are retained.

No main merge, public deployment, model replacement, signing-key use,
authenticated-human approval or universal OS network sandbox is claimed.
