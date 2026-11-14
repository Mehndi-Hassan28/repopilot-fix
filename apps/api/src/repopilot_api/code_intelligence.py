"""Bounded, read-only public GitHub source discovery and syntax extraction."""

import os
import time
from urllib.parse import quote

import httpx

SUPPORTED = {
    ".js": "javascript",
    ".jsx": "javascript",
    ".mjs": "javascript",
    ".cjs": "javascript",
    ".ts": "typescript",
    ".tsx": "typescript",
    ".py": "python",
}
EXCLUDED_DIRS = {
    "node_modules",
    "vendor",
    "dist",
    "build",
    "target",
    "coverage",
    ".next",
    "__pycache__",
    "bower_components",
}
EXCLUDED_NAMES = {
    "package-lock.json",
    "yarn.lock",
    "pnpm-lock.yaml",
    "poetry.lock",
    "pipfile.lock",
    "cargo.lock",
    "composer.lock",
}
GENERATED_SUFFIXES = (".min.js", ".bundle.js", ".generated.js", ".generated.ts", ".g.ts")


def limits():
    return (
        int(os.getenv("INDEX_MAX_FILES", "300")),
        int(os.getenv("INDEX_MAX_FILE_BYTES", "200000")),
        int(os.getenv("INDEX_MAX_API_REQUESTS", "350")),
    )


def supported_path(path: str, size: int, max_bytes: int) -> bool:
    parts = path.split("/")
    return (
        len(path) <= 1024
        and not any(p.lower() in EXCLUDED_DIRS or p.startswith(".") for p in parts[:-1])
        and parts[-1].lower() not in EXCLUDED_NAMES
        and not parts[-1].lower().endswith(GENERATED_SUFFIXES)
        and size <= max_bytes
        and os.path.splitext(parts[-1])[1].lower() in SUPPORTED
    )


def parse_symbols(source: str, language: str) -> list[dict]:
    """Return conservative declarations/imports; parser absence/errors yield an empty list."""
    try:
        from tree_sitter_language_pack import get_parser

        parser = get_parser(language)
        tree = parser.parse(source.encode("utf-8"))
        output = []

        def visit(node):
            kind = node.type
            if language == "python":
                recognized = {
                    "function_definition": ("function", "name"),
                    "class_definition": ("class", "name"),
                    "import_statement": ("import", None),
                    "import_from_statement": ("import", None),
                }
            else:
                recognized = {
                    "function_declaration": ("function", "name"),
                    "class_declaration": ("class", "name"),
                    "method_definition": ("function", "name"),
                    "import_statement": ("import", None),
                }
            if kind in recognized:
                sym_kind, field = recognized[kind]
                name_node = node.child_by_field_name(field) if field else None
                name = (
                    source.encode("utf-8")[name_node.start_byte : name_node.end_byte].decode(
                        "utf-8"
                    )
                    if name_node
                    else source.encode("utf-8")[node.start_byte : node.end_byte]
                    .decode("utf-8")
                    .splitlines()[0][:255]
                )
                output.append(
                    {"kind": sym_kind, "name": name[:255], "line": node.start_point.row + 1}
                )
            for child in node.children:
                visit(child)

        visit(tree.root_node)
        return output
    except Exception:
        return []


class GitHubSource:
    def __init__(self, client=None, max_requests=None):
        self.client = client or httpx.Client(timeout=httpx.Timeout(8.0), follow_redirects=False)
        self.owned = client is None
        self.max_requests = max_requests or limits()[2]
        self.requests = 0

    def close(self):
        if self.owned:
            self.client.close()

    def get(self, url):
        if self.requests >= self.max_requests:
            raise RuntimeError("GitHub API request limit reached")
        self.requests += 1
        for attempt in range(3):
            try:
                response = self.client.get(
                    url,
                    headers={
                        "Accept": "application/vnd.github+json",
                        "User-Agent": "RepoPilot-Fix",
                    },
                )
            except httpx.TimeoutException:
                if attempt == 2:
                    raise RuntimeError("GitHub request timed out")
                time.sleep(0.1 * (attempt + 1))
                continue
            except httpx.HTTPError as exc:
                raise RuntimeError("Could not reach GitHub") from exc
            if response.status_code in (429, 403) and (
                response.status_code == 429 or response.headers.get("X-RateLimit-Remaining") == "0"
            ):
                raise RuntimeError("GitHub rate limit reached")
            if response.status_code >= 500 and attempt < 2:
                time.sleep(0.1 * (attempt + 1))
                continue
            if response.status_code >= 400:
                raise RuntimeError(f"GitHub returned HTTP {response.status_code}")
            return response
        raise RuntimeError("GitHub request failed")

    def resolve_commit(self, owner, repo, branch):
        ref = self.get(f"https://api.github.com/repos/{owner}/{repo}/commits/{branch}").json()
        return ref["sha"]

    def discover(self, owner, repo, sha):
        tree = self.get(
            f"https://api.github.com/repos/{owner}/{repo}/git/trees/{sha}?recursive=1"
        ).json()
        if tree.get("truncated"):
            raise RuntimeError("Repository tree is too large to index safely")
        max_files, max_bytes, _ = limits()
        candidates = [
            item
            for item in tree.get("tree", [])
            if item.get("type") == "blob"
            and supported_path(item.get("path", ""), item.get("size", max_bytes + 1), max_bytes)
        ]
        file_budget = max(0, self.max_requests - self.requests)
        return candidates[: min(max_files, file_budget)]

    def fetch_file(self, owner, repo, sha, path):
        encoded_path = quote(path, safe="/")
        url = f"https://api.github.com/repos/{owner}/{repo}/contents/{encoded_path}?ref={sha}"
        data = self.get(url).json()
        if data.get("encoding") != "base64":
            raise RuntimeError("Unsupported GitHub file encoding")
        import base64

        raw = base64.b64decode(data["content"], validate=False)
        if b"\x00" in raw:
            raise RuntimeError("Binary file skipped")
        return raw.decode("utf-8")

    def fetch_issue(self, owner: str, repo: str, issue_number: int) -> dict:
        if type(issue_number) is not int or issue_number < 1:
            raise ValueError("Issue number must be a positive integer.")
        encoded_owner = quote(owner, safe="")
        encoded_repo = quote(repo, safe="")
        try:
            response = self.get(
                f"https://api.github.com/repos/{encoded_owner}/{encoded_repo}/issues/{issue_number}"
            )
        except RuntimeError as exc:
            if "HTTP 404" in str(exc):
                raise RuntimeError(f"Issue #{issue_number} not found on GitHub") from exc
            raise
        data = response.json()
        if not isinstance(data, dict):
            raise RuntimeError("Invalid GitHub issue response")
        if "pull_request" in data:
            raise ValueError(f"Issue #{issue_number} is a pull request, not an issue.")
        return {
            "number": data.get("number", issue_number),
            "title": (data.get("title") or "").strip()[:500],
            "body": (data.get("body") or "").strip()[:10000],
            "state": data.get("state", "open"),
            "html_url": data.get(
                "html_url", f"https://github.com/{owner}/{repo}/issues/{issue_number}"
            ),
            "author": (
                data.get("user", {}).get("login", "")
                if isinstance(data.get("user"), dict)
                else ""
            ),
            "labels": [
                label["name"]
                for label in data.get("labels", [])
                if isinstance(label, dict) and "name" in label
            ],
        }
