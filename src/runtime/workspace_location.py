"""Optional portable workspace selection beside the packaged executable."""
import json
import os
from pathlib import Path


def default_workspace(executable_directory=None):
    if executable_directory is not None:
        directory = Path(executable_directory)
        config = directory / 'reader-workspace.json'
        if config.is_file():
            value = json.loads(config.read_text(encoding='utf-8'))['workspace']
            if not isinstance(value, str) or not value.strip():
                raise ValueError('reader-workspace.json: workspace must be a nonempty path')
            target = (directory / value).resolve()
            if not (target / 'db/ai_reader.db').is_file():
                raise RuntimeError('Configured workspace is missing: ' + str(target))
            return target
    return Path(os.getenv('LOCALAPPDATA', Path.home())) / 'AIReader' / 'workspace'
