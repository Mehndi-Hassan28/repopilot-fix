import httpx

from repopilot_api.code_intelligence import GitHubSource, parse_symbols, supported_path


def test_file_filter_limits_generated_and_binary_extensions():
    assert supported_path("src/main.ts", 100, 100)
    assert not supported_path("node_modules/pkg/index.js", 10, 100)
    assert not supported_path("dist/app.js", 10, 100)
    assert not supported_path("package-lock.json", 10, 100)
    assert not supported_path("src/logo.png", 10, 100)
    assert not supported_path("src/app.min.js", 10, 100)
    assert not supported_path("src/main.py", 101, 100)


def test_parser_reports_one_based_lines():
    symbols = parse_symbols("\n\ndef greet(name):\n    return name\n", "python")
    assert {"kind": "function", "name": "greet", "line": 3} in symbols


def test_typescript_parser_extracts_declarations_and_imports():
    symbols = parse_symbols(
        'import { value } from "./module";\nexport class Thing {}\nfunction run() {}',
        "typescript",
    )
    assert {"kind": "import", "name": 'import { value } from "./module";', "line": 1} in symbols
    assert {"kind": "class", "name": "Thing", "line": 2} in symbols
    assert {"kind": "function", "name": "run", "line": 3} in symbols


def test_github_api_errors_are_graceful_and_bounded():
    client = httpx.Client(transport=httpx.MockTransport(lambda request: httpx.Response(404)))
    source = GitHubSource(client, max_requests=1)
    try:
        try:
            source.get("https://api.github.com/missing")
        except RuntimeError as exc:
            assert "404" in str(exc)
        else:
            raise AssertionError("expected GitHub error")
        try:
            source.get("https://api.github.com/second")
        except RuntimeError as exc:
            assert "request limit" in str(exc)
        else:
            raise AssertionError("expected request limit")
    finally:
        client.close()


def test_github_rate_limit_is_reported():
    client = httpx.Client(
        transport=httpx.MockTransport(
            lambda request: httpx.Response(403, headers={"X-RateLimit-Remaining": "0"})
        )
    )
    source = GitHubSource(client)
    try:
        try:
            source.get("https://api.github.com/limited")
        except RuntimeError as exc:
            assert "rate limit" in str(exc)
        else:
            raise AssertionError("expected rate limit")
    finally:
        client.close()
