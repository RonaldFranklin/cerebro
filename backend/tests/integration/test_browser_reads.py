"""Compare browser projections with the existing use cases over a real synthetic instance."""

from collections.abc import Iterator
from contextlib import ExitStack
from dataclasses import dataclass

import pytest
from starlette.testclient import TestClient

from cerebro.adapters.inbound.browser_api import browser_api
from cerebro.adapters.inbound.browser_reads import BrowserReads, install_browser_reads
from cerebro.adapters.inbound.read_views import health_view, read_view, search_view
from cerebro.application.browser import BrowserBinding
from cerebro.application.dto import GetOverviewRequest, HealthReportRequest, ReadNoteRequest, SearchNotesRequest
from cerebro.bootstrap.browser import owner_credentials
from cerebro.bootstrap.container import Container, build_container
from cerebro.domain.access import AccessCeiling, AccessContext
from cerebro.domain.enums import Sensitivity
from tests.integration.content_kit import (
    BROKEN_ID,
    CORE_ID,
    DOCKER_ID,
    HUB_ID,
    OLD_ID,
    RESTRICTED_MARKER,
    SSH_ID,
    VAULT_ID,
    make_sample_repo,
)


@dataclass
class Service:
    container: Container
    private: TestClient
    public: TestClient


@pytest.fixture(scope="module")
def service(tmp_path_factory: pytest.TempPathFactory) -> Iterator[Service]:
    sample = make_sample_repo(tmp_path_factory.mktemp("browser-reads"))
    container = build_container(sample.settings)
    container.start()
    owner_credentials(sample.settings).provision(b"read-fixture")
    cases = container.use_cases
    reads = BrowserReads(
        get_overview=cases.get_overview,
        search_notes=cases.search_notes,
        read_note=cases.read_note,
        get_graph=cases.get_graph,
        get_neighbors=cases.get_neighbors,
        health_report=cases.health_report,
    )
    with ExitStack() as stack:
        clients = []
        for listener, level in (("private", Sensitivity.RESTRICTED), ("public", Sensitivity.INTERNAL)):
            origin = f"https://{listener}.example.invalid"
            app = browser_api(container.browser_auth, BrowserBinding(listener, origin), AccessCeiling(level))
            install_browser_reads(app, reads)
            client = stack.enter_context(TestClient(app, base_url=origin))
            assert client.get("/api/overview").status_code == 401
            login = client.post(
                "/api/login", headers={"Origin": origin, "X-Cerebro-Request": "1"}, json={"password": "read-fixture"}
            )
            assert login.status_code == 200
            clients.append(client)
        yield Service(container, clients[0], clients[1])


@pytest.mark.parametrize("restricted", [True, False])
def test_api_projections_match_core_at_each_ceiling(service: Service, restricted: bool) -> None:
    client = service.private if restricted else service.public
    ctx = AccessContext.for_owner(AccessCeiling(Sensitivity.RESTRICTED if restricted else Sensitivity.INTERNAL))
    cases = service.container.use_cases
    search = cases.search_notes.execute(SearchNotesRequest(text="SSH"), ctx)
    response = client.get("/api/search", params={"q": "SSH"})
    assert response.status_code == 200
    assert response.headers["cache-control"] == "no-store"
    assert response.json()["results"] == search_view(search)

    expected = cases.get_overview.execute(GetOverviewRequest(), ctx)
    overview = client.get("/api/overview").json()
    assert overview["core_words"] == expected.core_words
    assert overview["core_truncated"] == expected.core_truncated
    assert overview["areas"] == [area.value for area in expected.areas]
    assert [hub["id"] for hub in overview["hubs"]] == [hub.id.value for hub in expected.hubs]
    assert [core["body"] for core in overview["core"]] == [core.body for core in expected.core]

    note = cases.read_note.execute(ReadNoteRequest(note_id=HUB_ID), ctx)
    projection = read_view(note)
    if restricted:
        projection["content_hash"] = note.content_hash
    assert client.get(f"/api/notes/{HUB_ID.value}").json() == projection
    section = cases.read_note.execute(ReadNoteRequest(note_id=SSH_ID, section="SSH"), ctx)
    assert client.get(f"/api/notes/{SSH_ID.value}", params={"section": "SSH"}).json() == read_view(section)


def test_search_history_and_exact_filters(service: Service) -> None:
    client = service.public
    params = {
        "q": "SSH",
        "area": "infrastructure",
        "type": "runbook",
        "priority": "P2",
        "status": "verified",
        "limit": "1",
    }
    assert [row["id"] for row in client.get("/api/search", params=params).json()["results"]] == [SSH_ID.value]
    empty = client.get("/api/search", params={"q": "", "type": "config"})
    omitted = client.get("/api/search", params={"type": "config"})
    assert empty.status_code == omitted.status_code == 200
    assert empty.json() == omitted.json() == {"results": []}
    history = client.get("/api/search", params={"type": "config", "history": "true"})
    assert [row["id"] for row in history.json()["results"]] == [OLD_ID.value]
    old = f"/api/notes/{OLD_ID.value}"
    for params in ({}, {"history": "true"}):
        read = client.get(old, params=params).json()
        assert (read["id"], read["status"]) == (OLD_ID.value, "superseded")
    assert client.get("/api/search", params={"type": "config"}).json() == {"results": []}
    graph = {node["id"] for node in client.get("/api/graph").json()["nodes"]}
    full = {node["id"] for node in client.get("/api/graph", params={"history": "true"}).json()["nodes"]}
    assert (OLD_ID.value in graph, OLD_ID.value in full) == (False, True)


def test_hidden_and_missing_are_identical_across_all_read_routes(service: Service) -> None:
    public = service.public
    for suffix in ("", "/neighbors"):
        for query in ("", "?history=true"):
            hidden = public.get(f"/api/notes/{VAULT_ID.value}{suffix}{query}", headers={"X-Ceiling": "restricted"})
            absent = public.get(f"/api/notes/absent-fixture{suffix}{query}")
            assert hidden.status_code == absent.status_code == 404
            assert hidden.content == absent.content
    paths = [
        "/api/search?history=true",
        "/api/overview",
        "/api/graph?history=true",
        f"/api/notes/{HUB_ID.value}",
        f"/api/notes/{SSH_ID.value}/neighbors?hops=2",
    ]
    for path in paths:
        response = public.get(path)
        assert response.status_code == 200, path
        assert VAULT_ID.value not in response.text
        assert RESTRICTED_MARKER not in response.text
    assert service.private.get(f"/api/notes/{VAULT_ID.value}").json()["id"] == VAULT_ID.value


@pytest.mark.parametrize("restricted", [True, False])
def test_graph_filters_remove_invisible_edge_endpoints(service: Service, restricted: bool) -> None:
    client = service.private if restricted else service.public
    graph = client.get("/api/graph", params={"history": "true"}).json()
    ids = {n["id"] for n in graph["nodes"]}
    assert OLD_ID.value in ids
    assert (VAULT_ID.value in ids) is restricted
    assert graph["center"] is None
    for node in graph["nodes"]:
        assert node["area"] == "infrastructure"
        assert node["sensitivity"] in {"public", "internal", "restricted"}
    path = f"/api/notes/{SSH_ID.value}/neighbors"
    filtered = client.get(
        path, params={"area": "infrastructure", "priority": "P2", "status": "verified", "sensitivity": "internal"}
    ).json()
    assert [n["id"] for n in filtered["nodes"]] == [SSH_ID.value]
    assert filtered["edges"] == []
    restricted_only = client.get(path, params={"sensitivity": "restricted"}).json()
    assert restricted_only == {"center": None, "nodes": [], "edges": []}

    local = client.get(f"/api/notes/{SSH_ID.value}/neighbors", params={"hops": "1"}).json()
    wider = client.get(f"/api/notes/{SSH_ID.value}/neighbors", params={"hops": "2"}).json()
    assert local["center"] == wider["center"] == SSH_ID.value
    assert DOCKER_ID.value in {node["id"] for node in local["nodes"]}
    assert CORE_ID.value in {node["id"] for node in wider["nodes"]}
    for view in (graph, local, wider):
        ids = {n["id"] for n in view["nodes"]}
        assert view["edges"]
        assert all(e["source"] in ids and e["target"] in ids for e in view["edges"])


def test_health_requires_restricted_listener(service: Service) -> None:
    ctx = AccessContext.for_owner(AccessCeiling(Sensitivity.RESTRICTED))
    report = service.container.use_cases.health_report.execute(HealthReportRequest(), ctx)
    assert service.private.get("/api/health").json() == health_view(report)
    response = service.public.get("/api/health", headers={"X-Forwarded-Host": "private.example.invalid"})
    assert response.status_code == 403
    assert VAULT_ID.value not in response.text
    assert "notes_checked" not in response.text


def test_two_hop_filters_require_a_path_through_matching_nodes(service: Service) -> None:
    path = f"/api/notes/{BROKEN_ID.value}/neighbors"
    unfiltered = service.private.get(path, params={"hops": 2}).json()
    assert DOCKER_ID.value in {node["id"] for node in unfiltered["nodes"]}
    # Both the center and the Docker note are P2. Their only intermediate is the P1 hub.
    filtered = service.private.get(path, params={"hops": 2, "priority": "P2"}).json()
    assert filtered["center"] == BROKEN_ID.value
    assert [node["id"] for node in filtered["nodes"]] == [BROKEN_ID.value]
    assert filtered["edges"] == []
    absent_center = service.private.get(path, params={"hops": 2, "priority": "P1"}).json()
    assert absent_center == {"center": None, "nodes": [], "edges": []}


def test_private_read_with_a_redacted_reference_has_no_edit_hash(service: Service) -> None:
    response = service.private.get(f"/api/notes/{BROKEN_ID.value}")
    assert response.status_code == 200
    assert "ghost-note" not in response.json()["body"]
    assert "content_hash" not in response.json()


def test_note_file_problems_are_listed_in_health_and_logged_without_content(
    service: Service, capsys: pytest.CaptureFixture[str]
) -> None:
    stray = service.container.settings.content_repo / "notes/stray-draft.md"
    stray.write_text(f"---\ntitle: {RESTRICTED_MARKER}\n")  # never committed
    health = service.private.get("/api/health")
    assert health.status_code == 200
    issues = [i for i in health.json()["issues"] if i["code"] == "note_file_blocking"]
    assert [i["subject"] for i in issues] == ["notes/stray-draft.md"]
    assert "differs from the last commit" in issues[0]["message"]
    service.container.search.set_revision("rev-elsewhere")
    rebuilt = service.private.get("/api/search", params={"q": "ssh"})
    assert rebuilt.status_code == 200
    assert not stray.exists()
    health = service.private.get("/api/health")
    assert [i["severity"] for i in health.json()["issues"] if i["code"] == "changes_quarantined"] == ["warning"]
    assert RESTRICTED_MARKER not in repr([i for i in health.json()["issues"] if i["code"] == "changes_quarantined"])
    assert RESTRICTED_MARKER not in capsys.readouterr().err


def test_health_can_display_a_filename_with_non_utf8_bytes(service: Service) -> None:
    filename = b"draft-\xff.md".decode("utf-8", "surrogateescape")
    stray = service.container.settings.content_repo / "notes" / filename
    literal = stray.with_name(r"draft-\xff.md")
    stray.write_text("draft")
    literal.write_text("draft")
    try:
        response = service.private.get("/api/health")
        assert response.status_code == 200
        subjects = {issue["subject"] for issue in response.json()["issues"]}
        assert {r"notes/draft-\xff.md", r"notes/draft-\\xff.md"} <= subjects
    finally:
        stray.unlink()
        literal.unlink()
