import uuid
from pathlib import Path
import yaml

from src.workspace.templates import get_default_config, get_template_dirs


class WorkspaceManager:
    def __init__(self, root: Path):
        self.root = Path(root).resolve()
        self.config_path = self.root / "config.yaml"

    @classmethod
    def create_workspace(cls, root: Path, name: str = "research") -> "WorkspaceManager":
        root = Path(root).resolve()
        root.mkdir(parents=True, exist_ok=True)
        for d in get_template_dirs():
            (root / d).mkdir(parents=True, exist_ok=True)
        config_content = get_default_config(name)
        (root / "config.yaml").write_text(config_content, encoding="utf-8")
        (root / ".paper_tracker.json").write_text("[]", encoding="utf-8")
        return cls(root)

    def load_config(self) -> dict:
        with open(self.config_path, "r", encoding="utf-8") as f:
            return yaml.safe_load(f)

    def get_workspace_id(self) -> str:
        return str(uuid.uuid5(uuid.NAMESPACE_DNS, str(self.root)))
