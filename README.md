# verity_core

Shared Core package for **Verity** (AttributionAgent Jr. backend) and
**PhishFinder**. Centralizes the boundaries both services duplicate today:

| Module | Purpose |
|---|---|
| `scraping` | `requests`/`bs4` simple scrape + lazy-`nodriver` advanced scrape (Verity only) |
| `technical_intel` | WHOIS + DNS — rich `get_technical_intel` (Verity) and lightweight `get_domain_creation_date` / `has_mx_records` (PhishFinder) |
| `storage` | GCS auth + raw upload, dual credential modes (service-account file or ADC) |
| `model_router` | unified Gemini transport — **NOT extracted yet (Sprint 1 step 11)** |
| `artifacts` | v0.1.5 analytical layer: evidence schemas, normalization, deterministic correlation/campaign clustering, two-stage multi-artifact pipeline, bounded attribution parsing, Atlas graph feed |
| `routers` | ModelRouter protocol + adapters: `openclaw` (governed local runtime, injected callable) and `gemini` (wraps `model_router.call_gemini`) |
| `media` | Media Evidence Bundle (v0.4.0): future-ready references for original/URL/transcript/captions/metadata/frames/audio/comparison |
| `workflows` | Shared workflow registry (v0.4.0): general_evidence, communications_phishing, media_authenticity, emerging_technology; capabilities declared available vs future |

## Versioning / install

Both services depend on this package. Locally it is installed **editable**:

```
pip install -e /path/to/verity_core          # PhishFinder (lightweight)
pip install -e /path/to/verity_core[scraping] # Verity (adds nodriver)
```

For deploys the services pin an **immutable Git tag** (e.g.
`verity-core @ git+https://…/verity-core@v0.2.0-sprint1`). The repin happens in
the Sprint 1 cleanup step, not during extraction.

## Trust media bundles (consumer slice)

`verity_core.trust.media_bundle` binds a validated
`media_evidence_package/v1` into a `trust_record/v0.1` and stores both in one
versioned JSON file. The binding is the SHA-256 of the package's full
canonical JSON, carried in the `evidence_recorded` entry payload (header
refs hold an advisory copy).

```
python -m verity_core.trust create IMG_3295.MOV --out img.trust.json
python -m verity_core.trust create FILE --out BUNDLE.json --overwrite  # replace existing bundle
python -m verity_core.trust verify img.trust.json --source IMG_3295.MOV
python -m verity_core.trust verify BUNDLE.json
python -m verity_core.trust demo [--out PATH] [--overwrite] [--corrupt]
```

`create` ingests a real file: the source is read read-only in a single
streaming pass (full SHA-256 plus actual byte count, with a before/after
stat check that rejects files mutated during ingestion); real ffprobe and
exiftool output is folded in when those binaries are present, and their
absence is recorded as a capability fact — integrity generation never
depends on them and nothing is fabricated. The package is validated, the
canonical full-package hash is bound into the trust record, and the bundle
is published atomically. `--out` must not be the source file itself;
existing bundles are only replaced with explicit `--overwrite`.

The verifier reports three verdicts separately — package validity, record
chain integrity, and package binding — and with `--source` additionally
compares the given file's bytes against the digest bound in the bundle.
The comparison targets the exact source artifact pinned by the bundle's
`evidence_recorded` binding payload (a package may contain several source
artifacts; only the bound one counts). No overall verdict is printed
until the `--source` comparison has been made, and the bundle checks and
source status are reported as separately scoped results. Exit is nonzero
on any failure.

Limits: verification is relative to the hashes embedded in the bundle. A
`--source` mismatch means the file's bytes differ from what was bound at
creation — a content mismatch, never a judgement about the file's
authenticity (it is not, and is never reported as, a deepfake finding).
Nothing here verifies authorship or media authenticity.

### Comparing two bundles

`verity_core.trust compare` produces a bounded, deterministic Markdown
report of what two bundles each recorded — no AI judgment, no network,
no claims about which file is an original or a copy:

```
python -m verity_core.trust compare A.trust.json B.trust.json
python -m verity_core.trust compare A.trust.json B.trust.json \
    --left-source A.MOV --right-source B.MOV   # optional byte recheck
python -m verity_core.trust compare A.trust.json B.trust.json --out report.md
```

Rows are emitted only for fields the packages actually recorded; anything
absent (or recorded but never examined, e.g. when exiftool was missing at
creation) is reported as UNKNOWN, never as a difference. Streams pair by
recorded type and ordinal — display alignment only, not proof that paired
streams correspond. Container durations are compared with an exact
arithmetic delta when both sides recorded finite numbers. With
`--left-source`/`--right-source`, each file's bytes are hashed and checked
against the digest bound in its bundle. `--out` publishes atomically and
refuses to replace an existing report unless `--overwrite` is given; any
publish error fails closed and leaves the target untouched.

Untrusted recorded values (filenames, tag values) are escaped and bounded
in the report; the exact values remain unchanged in the input bundles.
The report says what each bundle records and whether the recordings
agree — identical sha256 digests mean identical byte content, nothing
about provenance or authenticity.

## Sprint 1 status

Extraction order (golden suites in each service must stay byte-identical green
after every step): **scraping → technical_intel → storage → model_router**.
`model_router` is intentionally deferred. Inline originals in each service are
retained (dead) until the cleanup step — see the Sprint 1 plan's two-step-delete
rule.
