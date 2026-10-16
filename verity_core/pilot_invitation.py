"""Prepare an unsent pilot invitation. An invitation ID grants no authority."""
import re

def draft(invitation_id,package_filename,sha256,days=7):
    if not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9_-]{0,119}",invitation_id):raise ValueError("Invalid invitation ID")
    if not isinstance(package_filename,str) or not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9_. -]{0,180}",package_filename):raise ValueError("Use a bare package filename")
    if not re.fullmatch(r"[0-9a-f]{64}",sha256):raise ValueError("Invalid package SHA-256")
    if type(days) is not int or not 1<=days<=30:raise ValueError("Suggested validity must be 1 to 30 days")
    return f"""DRAFT — NOT SENT
Subject: Invitation to a controlled Verity pilot

Hello [pilot participant],

You are invited to test Verity on an Apple Silicon Mac running macOS 15 or later.
We will arrange delivery of {package_filename} through the agreed private
channel. Verify its SHA-256: {sha256}

This candidate is ad-hoc signed for controlled testing. Developer ID signing,
notarization and downloaded/quarantined Gatekeeper acceptance are pending.
Please coordinate installation with the administrator; do not disable
Gatekeeper or treat this as a public release.

Keep your existing Verity and OpenClaw installation. Extract the pilot into a
separate folder. Its bundled backend needs no separate Python installation and
uses its own Verity Pilot data directory. A local inference runtime and model
are separate prerequisites.

On first launch, open Installation & Runtime and send the administrator the
installation ID displayed there. We will issue a separate code bound to that
ID, with a suggested {days}-day validity. Paste the code into setup and confirm
the license ID and exact UTC expiration. This invitation is not an activation
code; its tracking ID ({invitation_id}) grants no access.

Choose your existing OpenClaw v0 service or a direct local Ollama model, enter
the exact installed model name, save the loopback connection settings, and
select Test saved connection. That test sends no specimen and loads no model.
Confirm memory headroom before loading a model; on a 16 GB Mac, use one
inference at a time and an appropriately sized quantized model/context.
For direct Ollama, disable its cloud features and use locally stored GGUF
weights. Model output remains untrusted and cannot execute arbitrary actions.

Run a small, non-sensitive test analysis. Inspect the result and Active Agents,
select Preserve for decision review, and export its Trust Record. Save the
checkpoint separately for independent verification. Unpreserved analysis
reports may disappear on restart. Any consequential action in this pilot is
only the fixed harmless local simulator and requires the displayed approval
controls.

Once that succeeds, provide the administrator the installation ID and the
agreed export/checkpoint so the enrollment can be recorded. That association
is operator supplied; verification does not establish hardware identity or
evidence truth.

Activation is offline and installation-ID-bound, not hardware attestation.
A replacement does not invalidate an older code. Revocation takes effect only
after the installation imports a signed cumulative list. The administrator
cannot force a remote import or revoke an offline code instantly. The app's
expiry and rollback checks depend on local state and the system clock.
Activation grants no separate permission to send evidence to a cloud provider.

[Administrator name and agreed contact channel]
"""
