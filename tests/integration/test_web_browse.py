from __future__ import annotations

import subprocess
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from groundtruth.api.app import create_app
from groundtruth.api.services import Services
from groundtruth.auth import build_strategy
from groundtruth.storage.job_store import JobStore
from groundtruth.storage.registry import VaultRegistry
from groundtruth.storage.source_index import SourceIndex
from groundtruth.web.browse import build_browse_router
from groundtruth.web.render import render_note_body

pytestmark = pytest.mark.integration

_FM = (
    "---\ntitle: {t}\ntags: [company]\nsources: []\n"
    "created: 2026-01-01\nupdated: 2026-01-01\n---\n\n{body}"
)


def _git(cwd: Path, *a: str) -> None:
    subprocess.run(["git", *a], cwd=cwd, check=True, capture_output=True)


@pytest.fixture
def client(tmp_path: Path) -> TestClient:
    repo = tmp_path / "repo"
    (repo / "work" / "companies").mkdir(parents=True)
    (repo / "work" / "people").mkdir(parents=True)
    _git(repo, "init", "-b", "main")
    (repo / "work" / "schema.md").write_text("# Schema\n\n## Folders\n- companies/\n- people/\n")
    (repo / "work" / "companies" / "Acme.md").write_text(
        _FM.format(t="Acme", body="Acme rivals [[people/Bob]] and [[companies/Ghost]].\n")
    )
    (repo / "work" / "people" / "Bob.md").write_text(_FM.format(t="Bob", body="Works at Acme.\n"))
    _git(repo, "add", "-A")
    _git(repo, "-c", "user.email=t@t", "-c", "user.name=t", "commit", "-m", "seed")
    state = tmp_path / "state"
    reg = VaultRegistry(state)
    reg.register("work", repo)
    services = Services(
        state_dir=str(state),
        registry=reg,
        job_store=JobStore(state),
        source_index=SourceIndex(state),
    )
    return TestClient(
        create_app(auth=build_strategy("none"), routers=[build_browse_router(services)])
    )


class TestBrowse:
    def test_tree_renders_the_folder_structure(self, client: TestClient) -> None:
        html = client.get("/browse/work").text
        assert "companies/" in html
        assert "people/" in html
        assert 'href="/browse/work/companies/Acme.md"' in html

    def test_note_renders_with_readable_frontmatter(self, client: TestClient) -> None:
        html = client.get("/browse/work/companies/Acme.md").text
        assert "<h2>Acme</h2>" in html
        assert "<th>tags</th><td>company</td>" in html
        assert "Acme rivals" in html

    def test_wikilink_is_clickable_within_browse(self, client: TestClient) -> None:
        html = client.get("/browse/work/companies/Acme.md").text
        assert 'href="/browse/work/people/Bob.md"' in html

    def test_dangling_wikilink_is_visibly_broken(self, client: TestClient) -> None:
        html = client.get("/browse/work/companies/Acme.md").text
        assert 'class="broken-link"' in html
        assert 'href="/browse/work/companies/Ghost' not in html

    def test_no_edit_delete_or_create_affordance(self, client: TestClient) -> None:
        for path in ("/browse", "/browse/work", "/browse/work/companies/Acme.md"):
            html = client.get(path).text.lower()
            assert "<form" not in html
            assert "<textarea" not in html
            assert "hx-post" not in html and "hx-put" not in html and "hx-delete" not in html
            assert "<button" not in html

    def test_traversal_is_refused(self, client: TestClient) -> None:
        assert client.get("/browse/work/../../etc/passwd").status_code in (400, 404)


class TestSafeRendering:
    def test_html_in_note_body_is_escaped(self) -> None:
        out = render_note_body(
            "<script>alert(1)</script> and <img src=x onerror=alert(2)>",
            vault="work",
            existing_paths=[],
        )
        assert "<script>" not in out
        assert "onerror=" not in out or "&" in out  # escaped
        assert "&lt;script&gt;" in out

    def test_wikilink_in_untrusted_body_cannot_inject(self) -> None:
        out = render_note_body('[["><script>x</script>]]', vault="work", existing_paths=[])
        assert "<script>x</script>" not in out
