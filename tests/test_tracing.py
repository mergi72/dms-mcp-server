from concurrent.futures import ThreadPoolExecutor
from threading import Barrier

from dms_mcp_server.server import _run_tool
from dms_mcp_server.tracing import CORRELATION_HEADER, correlation_scope, current_correlation_headers


def test_correlation_scope_is_isolated_and_produces_header() -> None:
    value = "123e4567-e89b-12d3-a456-426614174000"
    assert current_correlation_headers() == {}
    with correlation_scope(value) as active:
        assert active == value
        assert current_correlation_headers() == {CORRELATION_HEADER: value}
    assert current_correlation_headers() == {}


def test_parallel_clients_keep_results_and_downstream_correlation_ids_isolated() -> None:
    barrier = Barrier(3)
    calls = [
        ("list_items", "123e4567-e89b-12d3-a456-426614174001", "alfresco:/"),
        ("search_items", "123e4567-e89b-12d3-a456-426614174002", "edocat:/"),
        ("get_item_info", "123e4567-e89b-12d3-a456-426614174003", "webdav:/sample.txt"),
    ]

    def invoke(tool: str, correlation_id: str, path: str) -> dict:
        def downstream() -> dict:
            barrier.wait(timeout=2)
            return {"tool": tool, "path": path, "headers": current_correlation_headers()}

        return _run_tool(tool, downstream, correlation_id, path=path)

    with ThreadPoolExecutor(max_workers=3) as executor:
        results = list(executor.map(lambda call: invoke(*call), calls))

    assert {result["tool"] for result in results} == {call[0] for call in calls}
    assert {result["path"] for result in results} == {call[2] for call in calls}
    assert {result["headers"][CORRELATION_HEADER] for result in results} == {call[1] for call in calls}
    assert current_correlation_headers() == {}
