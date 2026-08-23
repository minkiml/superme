"""Static config: the git-tracked YAML describing the system and every repo."""

from dataclasses import dataclass, field
from pathlib import Path

import yaml

from ...paths import (KNOWLEDGE_REPO_DIR, LOCAL_HARNESS_DIR, REPOS_CONFIG_FILE, ROOT_DIR,
                      SYSTEM_CONFIG_FILE)
from .common import log


@dataclass
class SystemConfig:
    """The System singleton's static-meta (config/system.yaml)."""
    version: int = 1
    identity: str = "SuperMe"
    default_model: str | None = None
    default_effort: str | None = None  # reasoning effort floor (low|medium|high); None → "medium"
    policy_version: int = 1
    default_repo: str = "global"


REVIEW_MODES = ("fast", "strict")
REVIEW_MODE_DEFAULT = "fast"


@dataclass
class RepoConfig:
    """One repo's static-meta (an entry in config/repos.yaml). Home paths are derived by
    convention (knowledge_home/operational_home), not stored."""
    id: str
    label: str
    cwd: Path
    layer: str = "local"          # "global" | "local"
    persona_append: str = ""
    extra_mcp: list = field(default_factory=list)
    onboarding: str | None = None  # "project-init" | "retrofit"; None = let the owner pick
    # `fast` = approving the item merges it; `strict` = the diff gets its own review gate first.
    review_mode: str = REVIEW_MODE_DEFAULT
    # The branch every git site targets: branch-from base, sync source, merge target.
    anchor_branch: str | None = None
    # Gitignored paths that are nonetheless SOURCE, copied read-only into a research scratch tree.
    source_ignored: list = field(default_factory=list)
    # How to boot a server running an ITEM WORKTREE's code, so another instance cannot answer
    # a check.
    vet_env: dict | None = None   # keys: `cmd` · `port_env` · `ready` · `url_env`

    def __post_init__(self):
        if not self.label:
            self.label = self.id
        self.cwd = Path(self.cwd)
        if self.review_mode not in REVIEW_MODES:
            log.warning("repo %r: unknown review_mode %r; using %r",
                        self.id, self.review_mode, REVIEW_MODE_DEFAULT)
            self.review_mode = REVIEW_MODE_DEFAULT
        self.anchor_branch = (self.anchor_branch or "").strip() or None
        # A block without a `cmd` names nothing to start, so a half-filled entry must not read as
        # a vet env.
        if isinstance(self.vet_env, dict) and not str(self.vet_env.get("cmd") or "").strip():
            log.warning("repo %r: vet_env has no `cmd`; ignoring it", self.id)
            self.vet_env = None
        elif self.vet_env is not None and not isinstance(self.vet_env, dict):
            log.warning("repo %r: vet_env must be a mapping; ignoring it", self.id)
            self.vet_env = None
        # Checked HERE once, and dropped loudly rather than sanitised: a rewritten path is one
        # nobody can find.
        clean: list[str] = []
        for raw in (self.source_ignored or []):
            # Absoluteness is tested on the RAW value: stripping the slash first would make
            # `/etc/passwd` look relative.
            absolute = Path(str(raw).strip()).is_absolute()
            rel = str(raw).strip().strip("/")
            if not rel or absolute or ".." in Path(rel).parts:
                log.warning("repo %r: source_ignored entry %r is not a repo-relative path; "
                            "ignoring it", self.id, raw)
                continue
            clean.append(rel)
        self.source_ignored = clean

    # --- home conventions (the relocation pass edits THESE, not the YAML) -----------
    def _knowledge_base(self) -> Path:
        """This repo's sub-home in the central knowledge repo: `superme-knowledge/<id>-knowledge/`,
        for global and local repos alike."""
        return KNOWLEDGE_REPO_DIR / f"{self.id}-knowledge"

    def knowledge_home(self, scope: str) -> Path:
        """The knowledge home for a scope: core → `<base>/core`, dev → `<base>/dev`."""
        return self._knowledge_base() / scope

    def operational_home(self, scope: str) -> Path:
        """The per-repo operational home for a scope, under the CODE tree. This dir IS
        the per-repo plugin root."""
        return LOCAL_HARNESS_DIR / self.id / scope

    def constitution_home(self, scope: str) -> Path:
        """The per-repo learned-constitution home: one file per item, inside the
        operational cell."""
        return self.operational_home(scope) / "constitution"

    def to_dict(self) -> dict:
        d = {
            "id": self.id,
            "label": self.label,
            "cwd": str(self.cwd),
            "layer": self.layer,
            "persona_append": self.persona_append,
            "extra_mcp": list(self.extra_mcp),
        }
        if self.onboarding:
            d["onboarding"] = self.onboarding
        # Only non-defaults are written, so an untouched entry stays byte-identical.
        if self.review_mode != REVIEW_MODE_DEFAULT:
            d["review_mode"] = self.review_mode
        if self.anchor_branch:
            d["anchor_branch"] = self.anchor_branch
        if self.vet_env:
            d["vet_env"] = dict(self.vet_env)
        if self.source_ignored:
            d["source_ignored"] = list(self.source_ignored)
        return d


def load_system_config(path: Path = SYSTEM_CONFIG_FILE) -> SystemConfig:
    """Load config/system.yaml → SystemConfig (defaults if the file is absent/empty)."""
    try:
        raw = yaml.safe_load(Path(path).read_text(encoding="utf-8")) or {}
    except (FileNotFoundError, yaml.YAMLError) as e:
        log.warning("system.yaml unavailable (%s); using defaults.", e)
        raw = {}
    return SystemConfig(
        version=int(raw.get("version", 1)),
        identity=raw.get("identity", "SuperMe"),
        default_model=raw.get("default_model"),
        default_effort=raw.get("default_effort"),
        policy_version=int(raw.get("policy_version", 1)),
        default_repo=raw.get("default_repo", "global"),
    )


def load_repos(path: Path = REPOS_CONFIG_FILE) -> dict[str, RepoConfig]:
    """Load config/repos.yaml → {id: RepoConfig}. Relative cwds resolve against ROOT_DIR;
    an absent file gives {}."""
    try:
        raw = yaml.safe_load(Path(path).read_text(encoding="utf-8")) or {}
    except (FileNotFoundError, yaml.YAMLError) as e:
        log.warning("repos.yaml unavailable (%s); no repos loaded.", e)
        return {}
    out: dict[str, RepoConfig] = {}
    for rid, spec in (raw.get("repos") or {}).items():
        spec = spec or {}
        cwd = Path(spec.get("cwd", "."))
        if not cwd.is_absolute():
            cwd = (ROOT_DIR / cwd).resolve()
        out[rid] = RepoConfig(
            id=rid,
            label=spec.get("label") or rid,
            cwd=cwd,
            layer=spec.get("layer") or ("global" if rid == "global" else "local"),
            persona_append=(spec.get("persona_append") or "").strip(),
            extra_mcp=spec.get("extra_mcp") or [],
            onboarding=spec.get("onboarding") or None,
            review_mode=(spec.get("review_mode") or REVIEW_MODE_DEFAULT),
            anchor_branch=spec.get("anchor_branch") or None,
            vet_env=spec.get("vet_env") or None,
            source_ignored=spec.get("source_ignored") or [],
        )
    return out
