"""Paper-Notes static site exporter."""

import json, logging
from pathlib import Path
from datetime import datetime

logger = logging.getLogger(__name__)


class SiteExporter:
    def __init__(self, db=None):
        self.db = db

    def export(self, workspace_root: Path, output_dir: Path | None = None):
        """Export notes and reports as a static MkDocs-compatible site."""
        out = output_dir or workspace_root / "site_export"
        out.mkdir(parents=True, exist_ok=True)
        (out / "notes").mkdir(exist_ok=True)
        (out / "reports").mkdir(exist_ok=True)

        # Copy notes
        notes_src = workspace_root / "notes" / "generated"
        if notes_src.exists():
            for md_file in notes_src.rglob("*.md"):
                rel = md_file.relative_to(notes_src)
                dest = out / "notes" / rel
                dest.parent.mkdir(parents=True, exist_ok=True)
                dest.write_text(md_file.read_text(encoding="utf-8"),
                                encoding="utf-8")

        # Copy reports
        reports_src = workspace_root / "reports"
        if reports_src.exists():
            for md_file in reports_src.glob("*.md"):
                dest = out / "reports" / md_file.name
                dest.write_text(md_file.read_text(encoding="utf-8"),
                                encoding="utf-8")

        # Generate index
        notes_files = []
        if (out / "notes").exists():
            notes_files = list((out / "notes").rglob("*.md"))
        reports_files = []
        if (out / "reports").exists():
            reports_files = list((out / "reports").rglob("*.md"))

        index = f"""# AI Reader - Research Archive

Exported on {datetime.now().isoformat()}

## Reports ({len(reports_files)})

"""
        for rp in sorted(reports_files):
            index += f"- [{rp.stem}](reports/{rp.name})\n"

        index += f"\n## Notes ({len(notes_files)})\n\n"
        for np in sorted(notes_files):
            index += f"- [{np.stem}](notes/{np.relative_to(out/'notes')})\n"

        (out / "index.md").write_text(index, encoding="utf-8")
        logger.info(f"Site exported to {out}: {len(notes_files)} notes, {len(reports_files)} reports")
        return out

    def export_to_api(self, workspace_root: Path) -> dict:
        out = self.export(workspace_root)
        return {"path": str(out), "url": f"file://{out}/index.md"}
