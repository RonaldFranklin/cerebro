"""Owner edits over an authenticated browser session and a real content repository."""

from collections.abc import Iterator
from pathlib import Path
from subprocess import check_output
from typing import Any

import pytest
from starlette.testclient import TestClient

from cerebro.adapters.inbound.browser_api import browser_api
from cerebro.adapters.inbound.browser_edit import install_browser_edit
from cerebro.adapters.inbound.browser_reads import BrowserReads, install_browser_reads
from cerebro.application.browser import BrowserBinding
from cerebro.bootstrap.browser import owner_credentials
from cerebro.bootstrap.container import build_container
from cerebro.domain.access import AccessCeiling
from cerebro.domain.enums import Sensitivity
from tests.integration.content_kit import DOCKER_ID, SSH_ID, make_sample_repo

FIELDS = (
    "title",
    "type",
    "area",
    "priority",
    "sensitivity",
    "status",
    "summary",
    "hub",
    "related",
    "superseded_by",
    "lang",
    "review_by",
    "body",
)
PATH = f"/api/notes/{SSH_ID.value}"


@pytest.fixture
def editor(tmp_path: Path, request: pytest.FixtureRequest) -> Iterator[tuple[TestClient, Path]]:
    sample = make_sample_repo(tmp_path)
    service = build_container(sample.settings)
    service.start()
    owner_credentials(sample.settings).provision(b"fixture")
    cases = service.use_cases
    variant = getattr(request, "param", "private")
    # No listener serves a browser below restricted; "lower" exercises the adapter's own refusal.
    listener = "private" if variant == "lower" else variant
    origin = f"https://{listener}.example.invalid"
    level = Sensitivity.INTERNAL if variant == "lower" else Sensitivity.RESTRICTED
    app = browser_api(service.browser_auth, BrowserBinding(listener, origin), AccessCeiling(level))
    install_browser_reads(
        app,
        BrowserReads(
            get_overview=cases.get_overview,
            search_notes=cases.search_notes,
            read_note=cases.read_note,
            get_neighbors=cases.get_neighbors,
            get_graph=cases.get_graph,
            health_report=cases.health_report,
            listeners=(),
        ),
    )
    install_browser_edit(app, cases.owner_edit_note, cases.check_owner_edit)
    with TestClient(app, base_url=origin) as client:
        response = client.post(
            "/api/login", headers={"Origin": origin, "X-Cerebro-Request": "1"}, json={"password": "fixture"}
        )
        assert response.status_code == 200
        client.headers.update({"Origin": origin, "X-CSRF-Token": response.json()["csrf_token"]})
        yield client, sample.root


def payload(client: TestClient, path: str = PATH) -> dict[str, Any]:
    response = client.get(path)
    assert response.is_success
    view = response.json()
    return {"expected_hash": view["content_hash"], "note": {name: view[name] for name in FIELDS}}


def git(repo: Path, *args: str) -> str:
    return check_output(["git", "-C", str(repo), *args], text=True).strip()


def test_concurrent_edit_preserves_winner_and_reports_history_warning(editor: tuple[TestClient, Path]) -> None:
    client, repo = editor
    original = payload(client)
    edit = {**original, "note": {**original["note"], "sensitivity": "restricted", "body": "Updated fixture.\n"}}
    before = git(repo, "rev-parse", "HEAD")
    response = client.put(PATH, json=edit)
    assert response.status_code == 200, response.text
    result = response.json()
    assert result["id"] == SSH_ID.value
    assert [w["code"] for w in result["warnings"]] == ["previous_version_in_history"]
    assert result["commit"] == git(repo, "rev-parse", "HEAD")
    assert git(repo, "rev-parse", "HEAD^") == before
    assert result["content_hash"] == client.get(PATH).json()["content_hash"]
    assert result["validation"]["ok"]
    assert git(repo, "log", "-1", "--format=%B").strip() == f"Update note: {SSH_ID.value}\n\nWritten-by: owner"

    stale = client.put(PATH, json=original)
    assert stale.status_code == 409
    assert original["expected_hash"] not in stale.text
    assert git(repo, "rev-parse", "HEAD") == result["commit"]
    assert client.get(PATH).json()["body"] == "Updated fixture.\n"


def test_notes_linking_an_archived_note_stay_editable(editor: tuple[TestClient, Path]) -> None:
    client, _ = editor
    target = f"/api/notes/{DOCKER_ID.value}"
    archive = payload(client, target)
    archive["note"]["status"] = "archived"
    assert client.put(target, json=archive).status_code == 200
    assert client.get(target).json()["status"] == "archived"
    original = payload(client)  # links docker-conventions in body and related; read without ?history=true
    assert "[[docker-conventions]]" in original["note"]["body"]
    assert original["note"]["related"] == [DOCKER_ID.value]
    edit = {**original, "note": {**original["note"], "summary": original["note"]["summary"] + " Edited."}}
    assert client.put(PATH, json=edit).status_code == 200


def test_edit_errors_name_fields_without_echoing_rejected_input(editor: tuple[TestClient, Path]) -> None:
    client, repo = editor
    original = payload(client)
    before = git(repo, "rev-parse", "HEAD")
    note = {**original["note"], "title": "", "body": "fixture\n-----BEGIN PRIVATE KEY-----\n"}
    response = client.put(PATH, json={**original, "note": note})
    assert response.status_code == 422
    findings = response.json()["validation"]["violations"]
    assert any(v["code"] == "title_blank" and v["field"] == "title" for v in findings)
    assert any(v["code"] == "secret_detected" and v["field"] == "body" and v["line"] == 2 for v in findings)
    assert "PRIVATE KEY" not in response.text
    assert git(repo, "rev-parse", "HEAD") == before

    for invalid in (
        {"note": original["note"]},
        {**original, "expected_hash": None},
        {**original, "note": {**original["note"], "updated": "fixture-invalid"}},
        {**original, "note": {"body": "fixture-invalid"}},
    ):
        response = client.put(PATH, json=invalid)
        assert response.status_code == 422
        assert "fixture-invalid" not in response.text
        assert git(repo, "rev-parse", "HEAD") == before


@pytest.mark.parametrize("editor", ["public"], indirect=True)
def test_owner_edits_a_restricted_ceiling_session_on_the_public_listener(editor: tuple[TestClient, Path]) -> None:
    client, repo = editor
    inspected = payload(client)
    edit = {**inspected, "note": {**inspected["note"], "body": "Edited through the public listener.\n"}}
    response = client.put(PATH, json=edit)
    assert response.status_code == 200, response.text
    assert response.json()["commit"] == git(repo, "rev-parse", "HEAD")
    assert git(repo, "log", "-1", "--format=%B").endswith("Written-by: owner")


@pytest.mark.parametrize("editor", ["lower"], indirect=True)
def test_lower_ceiling_session_cannot_edit_and_is_refused_before_parsing(editor: tuple[TestClient, Path]) -> None:
    client, repo = editor
    before = git(repo, "rev-parse", "HEAD")
    response = client.put(
        PATH, content="invalid", headers={"Content-Type": "application/json", "X-Ceiling": "restricted"}
    )
    assert response.status_code == 403
    assert git(repo, "rev-parse", "HEAD") == before


def test_edit_requires_session_csrf(editor: tuple[TestClient, Path]) -> None:
    client, repo = editor
    inspected = payload(client)
    before = git(repo, "rev-parse", "HEAD")
    response = client.put(PATH, json=inspected, headers={"X-CSRF-Token": "invalid"})
    assert response.status_code == 403
    assert git(repo, "rev-parse", "HEAD") == before


def test_editor_counts_metadata_in_utf8_bytes(editor: tuple[TestClient, Path]) -> None:
    client, repo = editor
    inspected = payload(client)
    before = git(repo, "rev-parse", "HEAD")
    # 129 code points fit a character-based limit but occupy 258 UTF-8 bytes.
    oversized = "é" * 129
    for name in ("lang", "area", "type", "priority", "status", "sensitivity"):
        response = client.put(PATH, json={**inspected, "note": {**inspected["note"], name: oversized}})
        assert response.status_code == 422
        fields = response.json()["validation"]["violations"]
        assert {v["field"] for v in fields if v["code"] == "invalid_field"} == {name}
        assert oversized not in response.text
        assert git(repo, "rev-parse", "HEAD") == before
    exact = {**inspected["note"], "lang": "é" * 128}
    assert client.put(PATH, json={**inspected, "note": exact}).status_code == 200
    assert client.get(PATH).json()["lang"] == exact["lang"]


def test_checking_a_draft_reports_the_rules_and_writes_nothing(editor: tuple[TestClient, Path]) -> None:
    client, repo = editor
    inspected = payload(client)
    before = git(repo, "rev-parse", "HEAD")
    clean = client.post(PATH + "/validate", json=inspected)
    assert clean.status_code == 200, clean.text
    assert clean.headers["cache-control"] == "no-store"
    assert clean.json()["validation"]["ok"]
    assert clean.json()["base_current"]
    assert clean.json()["budget"]["hard"] == 3000
    body = "First.\n[[not-written]]\n-----BEGIN PRIVATE KEY-----\n"
    draft = {"expected_hash": "0" * 64, "note": {**inspected["note"], "body": body}}
    response = client.post(PATH + "/validate", json=draft)
    assert response.status_code == 200
    result = response.json()
    assert {(v["code"], v["line"]) for v in result["validation"]["violations"]} >= {("secret_detected", 3)}
    assert "broken_link" in {v["code"] for v in result["validation"]["violations"]}
    assert (result["broken_link_lines"], result["sensitivity_lines"]) == ([2], [])
    assert not result["base_current"]
    assert "PRIVATE KEY" not in response.text
    assert "not-written" not in response.text
    invalid = client.post(PATH + "/validate", json={**inspected, "note": {**inspected["note"], "priority": "P9"}})
    assert invalid.status_code == 200
    assert invalid.json()["budget"] is None
    assert not invalid.json()["validation"]["ok"]
    assert git(repo, "rev-parse", "HEAD") == before
    assert not git(repo, "status", "--porcelain")


@pytest.mark.parametrize("editor", ["private", "public"], indirect=True)
def test_checking_needs_the_same_session_proof_as_saving(editor: tuple[TestClient, Path]) -> None:
    client, _ = editor
    inspected = payload(client)
    path = PATH + "/validate"
    assert client.post(path, json=inspected, headers={"X-CSRF-Token": "invalid"}).status_code == 403
    assert client.post(path, json=inspected, headers={"Origin": "https://other.invalid"}).status_code == 403
    assert client.post(path, content="{}", headers={"Content-Type": "text/plain"}).status_code == 415
    assert client.post(path, content='{"a":1,"a":2}', headers={"Content-Type": "application/json"}).status_code == 422
    assert client.post(path + "?history=true", json=inspected).status_code == 422
    assert client.post(path, content=b"x" * 70000, headers={"Content-Type": "application/json"}).status_code == 413
    assert client.post("/api/notes/absent-note/validate", json=inspected).status_code == 404
    client.cookies.clear()
    assert client.post(path, json=inspected).status_code == 401


@pytest.mark.parametrize("editor", ["lower"], indirect=True)
def test_a_lower_ceiling_session_cannot_check_a_draft(editor: tuple[TestClient, Path]) -> None:
    client, _ = editor
    response = client.post(PATH + "/validate", content="invalid", headers={"Content-Type": "application/json"})
    assert response.status_code == 403
