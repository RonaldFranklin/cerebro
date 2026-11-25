"""A real sample content repository (git, schema files, encrypted restricted note) and a ``Settings`` for it.

Generated in ``tmp_path`` through the real adapters, so there are no binary fixtures to keep in sync.
"""

import os
from dataclasses import dataclass
from pathlib import Path

from cerebro.adapters.outbound.crypto import FernetCipher, generate_key, load_keys
from cerebro.adapters.outbound.filesystem import FilesystemNoteRepository
from cerebro.adapters.outbound.git import GitIdentity, GitRepo
from cerebro.application.ports.notes import NoteChange
from cerebro.bootstrap.settings import Settings
from cerebro.domain.commit_message import CommitMessage
from cerebro.domain.enums import Priority, Sensitivity, Status
from cerebro.domain.note import Note, NoteId
from cerebro.domain.vocabulary import CORE, HUB, NoteType
from tests.unit.domain.builders import make_note

IDENTITY = GitIdentity(name="Cerebro Test", email="cerebro@example.invalid")
SEED = CommitMessage(
    subject="Seed the sample content",
    proposal_id="seed-0001",
    proposed_by="owner",
    curated_by="owner",
    approved_by="owner",
)

RESTRICTED_MARKER = "zebrafruit"
"""A word that appears only in the restricted note (title, summary and body)."""

CORE_ID = NoteId("core-rules")
HUB_ID = NoteId("infrastructure")
SSH_ID = NoteId("server-ssh-access")
DOCKER_ID = NoteId("docker-conventions")
VAULT_ID = NoteId("vault-layout")
OLD_ID = NoteId("old-proxy")
ISLAND_ID = NoteId("island-hub")
BROKEN_ID = NoteId("broken-link-note")
GHOST_ID = NoteId("ghost-note")


def sample_notes() -> list[Note]:
    def note(**fields: object) -> Note:
        base: dict[str, object] = {"hub": HUB_ID, "related": (), "sensitivity": Sensitivity.PUBLIC, "review_by": None}
        merged = {**base, **fields}
        if isinstance(merged.get("type"), str):
            merged["type"] = NoteType(str(merged["type"]))
        return make_note(**merged)

    return [
        note(
            id=CORE_ID,
            title="Core rules",
            type=CORE,
            priority=Priority.P0,
            hub=None,
            summary="The rules every agent follows before anything else in this knowledge base.",
            body="Always be kind and precise.\n\nSee [[docker-conventions]] for the container rules.\n",
        ),
        note(
            id=HUB_ID,
            title="Infrastructure",
            type=HUB,
            priority=Priority.P1,
            hub=None,
            summary="Entry point to everything about servers, containers and access in the home lab.",
            body="Entry point for infrastructure notes.\n",
        ),
        note(
            id=DOCKER_ID,
            title="Docker conventions",
            type="guide",
            summary="How containers are named, built and run on the home server, as plain conventions.",
            body="# Docker\n\nUse compose for every service.\n",
        ),
        note(
            id=SSH_ID,
            title="SSH access",
            type="runbook",
            sensitivity=Sensitivity.INTERNAL,
            related=(DOCKER_ID,),
            summary="How to reach the home server over SSH from the workstation and verify its host key.",
            body="# SSH\n\nConnect with the workstation key. Containers: [[docker-conventions]].\n",
        ),
        note(
            id=VAULT_ID,
            title=f"Vault layout {RESTRICTED_MARKER}",
            type="reference",
            sensitivity=Sensitivity.RESTRICTED,
            related=(SSH_ID,),
            summary=f"Where the {RESTRICTED_MARKER} vault lives and how its unseal procedure is organised.",
            body=f"# Layout\n\nThe {RESTRICTED_MARKER} paths. Access first: [[server-ssh-access]].\n",
        ),
        note(
            id=OLD_ID,
            title="Old proxy",
            type="config",
            status=Status.SUPERSEDED,
            superseded_by=DOCKER_ID,
            priority=Priority.P3,
            summary="The reverse proxy that was replaced by the compose based setup, kept for history.",
            body="Replaced by [[docker-conventions]].\n",
        ),
        note(
            id=BROKEN_ID,
            title="Broken link note",
            type="concept",
            summary="A note that points to a note that does not exist, so that the broken link is reported.",
            body="See [[ghost-note]] for details.\n",
        ),
        note(
            id=ISLAND_ID,
            title="Island hub",
            type=HUB,
            priority=Priority.P1,
            hub=None,
            summary="A hub with no members and no links to or from any other note, so that the orphan report finds it.",
            body="Nobody links here.\n",
        ),
    ]


@dataclass(frozen=True)
class SampleRepo:
    settings: Settings
    key: bytes

    @property
    def root(self) -> Path:
        return self.settings.content_repo

    @property
    def state_dir(self) -> Path:
        return self.settings.state_dir

    def git(self) -> GitRepo:
        return GitRepo(self.root, identity=IDENTITY)

    def commit_out_of_band(self, note: Note, subject: str = "Out of band") -> str:
        """A commit made behind the container's back (a manual edit or a pull)."""
        repo = FilesystemNoteRepository(
            self.root, git=self.git(), cipher=FernetCipher(load_keys(self.settings.key_file))
        )
        message = CommitMessage(
            subject=subject, proposal_id="manual-0001", proposed_by="owner", curated_by="owner", approved_by="owner"
        )
        return repo.apply([NoteChange(note=note)], message)


def write_key_file(directory: Path) -> tuple[Path, bytes]:
    directory.mkdir(mode=0o700)
    directory.chmod(0o700)
    key = generate_key()
    path = directory / "cerebro.key"
    fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    with os.fdopen(fd, "wb") as handle:
        handle.write(key + b"\n")
    return path, key


def make_sample_repo(tmp_path: Path, *, notes: list[Note] | None = None) -> SampleRepo:
    root = tmp_path / "content"
    root.mkdir()
    git = GitRepo(root, identity=IDENTITY)
    git.init("main")
    (root / "schema").mkdir()
    (root / "schema/areas.yaml").write_text("areas:\n  - infrastructure\n")
    (root / "schema/types.yaml").write_text("types:\n  - concept\n  - guide\n  - runbook\n  - reference\n  - config\n")
    git.stage(["schema/areas.yaml", "schema/types.yaml"])
    git.commit("Initialise schema")
    key_file, key = write_key_file(tmp_path / "secrets")
    repo = FilesystemNoteRepository(root, git=git, cipher=FernetCipher([key]))
    chosen = sample_notes() if notes is None else notes
    if chosen:
        repo.apply([NoteChange(note=n) for n in chosen], SEED)
    settings = Settings(
        content_repo=root,
        state_dir=tmp_path / "state",
        key_file=key_file,
        git_name=IDENTITY.name,
        git_email=IDENTITY.email,
    )
    return SampleRepo(settings=settings, key=key)
