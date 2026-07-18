"""Baseline load smoke for lzt-flow's hot endpoints (wave-04 release floor).

NOT part of the pytest suite — run manually against a live instance:

    uv run --with locust locust -f tests/load/locustfile.py --host http://localhost:8000

Covers the critical request-scoped paths: catalog reads (editor bootstrap), synchronous flow
invoke, and async run creation. Record baseline req/s + p95 latency + error rate under the target
concurrency in this file's header when you run it, so regressions are visible.

Baseline (fill in from your run): reqs/s ____ · p95 ____ ms · error rate ____ % @ ____ users.
"""

from __future__ import annotations

from locust import HttpUser, between, task  # type: ignore[import-not-found]


class FlowUser(HttpUser):
    wait_time = between(0.5, 2.0)

    # Set to a real compiled flow id on the target instance before running.
    flow_id = "REPLACE_WITH_A_COMPILED_FLOW_ID"

    @task(3)
    def read_catalog(self) -> None:
        self.client.get("/catalog/list", name="GET /catalog/list")

    @task(2)
    def read_categories(self) -> None:
        self.client.get("/catalog/categories", name="GET /catalog/categories")

    @task(2)
    def invoke_flow(self) -> None:
        self.client.post(
            f"/flows/{self.flow_id}/invoke",
            json={"params": {}},
            name="POST /flows/{id}/invoke",
        )

    @task(1)
    def create_run(self) -> None:
        self.client.post(
            "/runs/create",
            json={"flow_id": self.flow_id},
            name="POST /runs/create",
        )
