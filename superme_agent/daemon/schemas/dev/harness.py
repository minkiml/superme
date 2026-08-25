"""Schemas for the Manage-Harness routes (routers/dev/harness.py)."""

from pydantic import BaseModel, ConfigDict


class PluginEntry(BaseModel):
    """One universal skill/agent in the harness tree. Skills carry `access`; agents carry
    `model`/`tools`. Routes use response_model_exclude_unset so each emits only its own keys."""
    model_config = ConfigDict(extra="allow")
    kind: str
    name: str
    description: str | None = None
    category: str | None = None
    access: str | None = None        # skills only
    model: str | None = None         # agents only
    tools: str | list[str] | None = None  # agents only (frontmatter scalar or list)


class PluginScope(BaseModel):
    """One scope group (dev | core | shared) in the Skills & Agents tab."""
    scope: str
    label: str
    plugin: str
    note: str
    skills: list[PluginEntry]
    agents: list[PluginEntry]


class HarnessPluginsResponse(BaseModel):
    scopes: list[PluginScope]


class LocalPluginsResponse(BaseModel):
    """A single host's OWN local-harness skills + agents (its `local-harness/<id>/dev` plugin tree) —
    flat (no dev/core/shared split; the dev workspace is already mode-scoped)."""
    context_id: str
    skills: list[PluginEntry]
    agents: list[PluginEntry]


class PaletteResponse(BaseModel):
    context_id: str
    mode: str
    commands: list[str]


class FoundationFile(BaseModel):
    """One universal identity/charter file — SuperMe's repo-agnostic machinery (Foundations)."""
    key: str            # stable id (self | dev-charter | core-charter)
    label: str          # display name
    scope: str          # universal | dev | core
    path: str           # absolute source path
    present: bool       # the file exists on disk
    body: str           # raw markdown (empty when absent)


class ConstitutionEntry(BaseModel):
    """One LEARNED universal constitution item (always-on rule) for a mode."""
    mode: str                       # dev | core
    slug: str
    enabled: bool
    foundational: bool = False      # charter-pinned → not disable-able
    title: str
    body: str
    source: str | None = None
    created: str | None = None


class FoundationResponse(BaseModel):
    files: list[FoundationFile]
    constitutions: list[ConstitutionEntry]


class FoundationFileSaveResponse(BaseModel):
    ok: bool
    key: str


class DeputyMandateResponse(BaseModel):
    """The per-repo deputy mandate (the standing acceptance bar). Lives in the harness cell
    (`local-harness/<id>/dev/deputy/mandate.md`); seeded from a template on connect, editable here."""
    context_id: str
    path: str
    content: str


class DeputyMandateSaveResponse(BaseModel):
    ok: bool
    context_id: str


class PluginFileResponse(BaseModel):
    """The raw markdown of one skill/agent (popup preview/edit source)."""
    scope: str
    kind: str
    name: str
    path: str
    content: str


class PluginFileSaveResponse(BaseModel):
    ok: bool
    scope: str
    kind: str
    name: str


class ConstitutionFileResponse(BaseModel):
    """The raw markdown (frontmatter intact) of one constitution — the popup's edit source."""
    slug: str
    scope: str
    path: str
    content: str


class ConstitutionFileSaveResponse(BaseModel):
    ok: bool
    slug: str
    scope: str


class PublishedRow(BaseModel):
    """One published LEARNED artifact, reconciled with live on-disk state."""
    proposal_id: int
    form: str
    scope: str
    slug: str
    title: str
    summary: str | None = None
    created: str | None = None
    present: bool
    enabled: bool


class PublishedResponse(BaseModel):
    context_id: str
    published: list[PublishedRow]


class PublishedToggleResponse(BaseModel):
    """ok + proposal_id + the operational toggle result (present/enabled) spread in via extra."""
    model_config = ConfigDict(extra="allow")
    ok: bool
    proposal_id: int


class ManagedConstitution(BaseModel):
    """One constitution for management — dir-scanned (provenance-agnostic: hand-authored + learned
    alike), keyed by (scope, slug). Powers the enable/disable surfaces for ALL constitutions."""
    slug: str
    scope: str                       # universal_dev | repo_dev  (the toggle key)
    mode: str                        # dev | core
    origin: str                      # universal | repo (local to this host)
    enabled: bool
    foundational: bool = False       # charter-pinned → not disable-able
    title: str
    description: str | None = None
    body: str
    source: str | None = None
    created: str | None = None
    updated: str | None = None


class ConstitutionsResponse(BaseModel):
    context_id: str
    constitutions: list[ManagedConstitution]


class ConstitutionToggleResponse(BaseModel):
    """ok + slug + scope + the operational toggle result (present/enabled)."""
    model_config = ConfigDict(extra="allow")
    ok: bool
    slug: str
    scope: str


class AssetItem(BaseModel):
    """One asset-pool item. `available` is the shelf's switch, `restricted` is who may take it,
    `enabled` is this repo's."""
    slug: str
    title: str
    description: str | None = None
    body: str
    adopted: bool
    enabled: bool
    available: bool = True           # off → muted everywhere, each repo's own record kept
    restricted: bool = False         # hub-only, and this is not the engine's cell


class AssetsResponse(BaseModel):
    context_id: str
    assets: list[AssetItem]


class AssetActionResponse(BaseModel):
    """Result of a per-repo asset action (adopt / enable / disable / drop)."""
    ok: bool
    slug: str
    action: str
    adopted: bool
    enabled: bool


class PublishedDeleteResponse(BaseModel):
    """ok + proposal_id + `removed` (the delete_published result) carried via extra."""
    model_config = ConfigDict(extra="allow")
    ok: bool
    proposal_id: int


class PublishedFileResponse(BaseModel):
    """The editable source of a published artifact — its raw markdown + resolved path."""
    proposal_id: int
    form: str
    scope: str
    slug: str
    path: str
    content: str


class PublishedFileSaveResponse(BaseModel):
    ok: bool
    proposal_id: int


class PluginFileBody(BaseModel):
    scope: str
    kind: str
    name: str
    content: str
    context_id: str = "global"


class FoundationFileBody(BaseModel):
    key: str
    content: str


class DeputyMandateBody(BaseModel):
    content: str
    context_id: str = "global"


class PublishedToggleBody(BaseModel):
    enabled: bool
    context_id: str = "global"


class ConstitutionToggleBody(BaseModel):
    enabled: bool
    scope: str                       # universal_dev | repo_dev
    context_id: str = "global"


class ConstitutionFileBody(BaseModel):
    slug: str
    scope: str                       # universal_dev | repo_dev (| _core)
    content: str
    context_id: str = "global"


class AssetActionBody(BaseModel):
    action: str  # 'adopt' | 'enable' | 'disable' | 'drop'
    context_id: str = "global"


class PublishedFileBody(BaseModel):
    content: str
    context_id: str = "global"
