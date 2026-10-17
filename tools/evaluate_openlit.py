"""Isolated metadata-only OpenLIT probe; run in an optional SDK venv.

Never connects to a model or hosted collector. The fixed coordinator adapter is
replaced with synthetic responses, while its actual telemetry callback is used.
"""
import json
import os
import tempfile
import threading
import time
from http.server import BaseHTTPRequestHandler, HTTPServer
from pathlib import Path
import sys

# Explicit sampling is necessary for a reproducible one-request evaluation.
os.environ["OTEL_TRACES_SAMPLER"] = "always_on"
os.environ.pop("OTEL_TRACES_SAMPLER_ARG", None)
sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "apps" / "node"))

import openlit
from openlit._instrumentors import get_all_instrumentors
from opentelemetry import trace
from swarmtracker_node_ui.session_coordinator import Coordinator, MODEL


def main():
    records = []
    outage = threading.Event()

    class Sink(BaseHTTPRequestHandler):
        def do_POST(self):
            data = self.rfile.read(int(self.headers["Content-Length"]))
            records.append(data)
            self.send_response(503 if outage.is_set() else 200)
            self.end_headers()
            self.wfile.write(b"{}")

        def log_message(self, *_):
            pass

    server = HTTPServer(("127.0.0.1", 0), Sink)
    threading.Thread(target=server.serve_forever, daemon=True).start()
    with tempfile.TemporaryDirectory() as directory:
        pricing = Path(directory) / "pricing.json"
        pricing.write_text("{}")
        openlit.init(
            otlp_endpoint=f"http://127.0.0.1:{server.server_port}",
            capture_message_content=False,
            disabled_instrumentors=list(get_all_instrumentors()),
            disable_metrics=True, disable_events=True, disable_batch=True,
            pricing_json=str(pricing),
        )
        tracer = trace.get_tracer("verity.synthetic.review")

        def observe(item):
            with tracer.start_as_current_span("glm.review") as span:
                for key in ("session_id", "task_id", "request_id", "model", "latency_ms", "finish_reason", "error_class"):
                    if item[key] is not None:
                        span.set_attribute("verity." + key, item[key])
                for key, value in item["usage"].items():
                    span.set_attribute("gen_ai.usage." + key, value)

        def spec(name):
            return dict(id=name, purpose="SECRET_PROMPT_123", dependencies=[],
                        authorization="explicit-read-only-glm-review", acceptance_criteria="Review synthetic text",
                        model_settings=dict(model=MODEL, reasoning_effort="low",
                                            chat_template_kwargs={"reasoning_effort":"low"}, max_tokens=2048),
                        expected_outputs="Short response", validation_gates="Separate human check")

        def adapter(item, request_id):
            return dict(request_id=request_id, content="SECRET_RESPONSE_456",
                        finish_reason="length" if item["id"] == "truncated" else "stop",
                        usage={"prompt_tokens":8, "completion_tokens":2, "total_tokens":10,
                               "credential":"SECRET_CREDENTIAL_789"})

        coordinator = Coordinator(Path(directory)/"queue.db", adapter=adapter, telemetry=observe, session_id="synthetic-session")
        coordinator.configure(rate=1, start=time.time()-1, ceiling=20)
        coordinator.enqueue(spec("success"));coordinator.enqueue(spec("truncated"));coordinator.control("running")
        assert coordinator.run_one("worker") == "success"
        assert coordinator.run_one("worker") == "truncated"
        deadline = time.monotonic()+5
        while len(records) < 2 and time.monotonic() < deadline:
            time.sleep(.05)
        assert len(records) >= 2, "Expected both OTLP spans"
        combined = b"".join(records)
        for marker in (b"synthetic-session", b"success", b"truncated", b"glm-5.3-flash",
                       b"finish_reason", b"request_id", b"latency_ms", b"prompt_tokens", b"completion_tokens"):
            assert marker in combined, marker
        assert b"SECRET_" not in combined

        outage.set()
        coordinator.enqueue(spec("outage"))
        assert coordinator.run_one("worker") == "outage"
        deadline = time.monotonic()+5
        while len(records) < 3 and time.monotonic() < deadline:
            time.sleep(.05)
        states={task["id"]:task["state"] for task in coordinator.snapshot()["tasks"]}
        assert states == {"success":"completed", "truncated":"failed", "outage":"completed"}
        assert not any(state == "independently_verified" for state in states.values())
        assert len(records) >= 3
        print(json.dumps({"sdk":openlit.__version__ if hasattr(openlit,"__version__") else "installed",
                          "otlp_spans":len(records),"states":states,"sensitive_content_in_spans":False,
                          "collector_outage_did_not_verify":True}))
    server.shutdown();server.server_close()


if __name__ == "__main__":
    main()
