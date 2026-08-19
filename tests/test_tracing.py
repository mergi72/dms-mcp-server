from dms_mcp_server.tracing import CORRELATION_HEADER, correlation_scope, current_correlation_headers


def test_correlation_scope_is_isolated_and_produces_header() -> None:
    value = "123e4567-e89b-12d3-a456-426614174000"
    assert current_correlation_headers() == {}
    with correlation_scope(value) as active:
        assert active == value
        assert current_correlation_headers() == {CORRELATION_HEADER: value}
    assert current_correlation_headers() == {}
