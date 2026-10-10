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
python -m verity_core.trust demo [--out PATH] [--overwrite] [--corrupt]
python -m verity_core.trust verify BUNDLE.json   # nonzero exit if not verified
```

The verifier reports three verdicts separately — package validity, record
chain integrity, and package binding — and never claims to verify
authorship or media authenticity: it establishes consistency against the
hashes embedded in the bundle only.

## Sprint 1 status

Extraction order (golden suites in each service must stay byte-identical green
after every step): **scraping → technical_intel → storage → model_router**.
`model_router` is intentionally deferred. Inline originals in each service are
retained (dead) until the cleanup step — see the Sprint 1 plan's two-step-delete
rule.
