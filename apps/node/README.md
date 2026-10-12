# Existing SwarmTracker Node application

This directory preserves the existing Node v0.5.0 release source under version control alongside Verity Core. It is the existing browser UI, not a new desktop application or a deployment.

Run from this directory with the repository's Core installed and FastAPI, requests, httpx, Jinja2 and python-multipart available:
    python -m uvicorn swarmtracker_node_ui.app:app --host 127.0.0.1 --port 8077

The app expects its existing legacy local runtime and Ollama endpoints. Do not confuse that legacy /v0/generate service with current OpenClaw plugin hooks. No live deployment has been verified by this import.

The original vendored Core remains for compatibility, including Vertex support not yet reconciled into the installed Core. The bridge can mix installed analysis modules with vendored optional-provider modules; consolidated provider provenance remains follow-up work. The import preserves existing behavior and does not add OS sandboxing or model forward-pass intervention.

Baseline governed tests:
    python -m pytest swarmtracker_node_ui/tests_governed.py -q

All verification in this session uses mocked providers and temporary stores. No provider credentials or user media are included.
