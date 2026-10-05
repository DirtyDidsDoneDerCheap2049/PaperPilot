"""Keep validated paper extraction variants inside their own workspace."""
import hashlib
import json
import logging
from pathlib import Path
import tempfile
from src.llm.response_style import style_messages

logger = logging.getLogger(__name__)
CACHE_VERSION = 1


def request_key(llm, messages):
    model = getattr(llm, 'fast_model', None)
    options = llm.request_options(model, structured=True) if callable(getattr(llm, 'request_options', None)) else {'model': model}
    identity = {'version': CACHE_VERSION, 'endpoint': getattr(llm, '_base_url', ''),
                'options': options, 'messages': style_messages(messages, structured=True)}
    return hashlib.sha256(json.dumps(identity, ensure_ascii=False, sort_keys=True).encode()).hexdigest()


def path_for(workspace, key):
    if len(key) != 64 or any(c not in '0123456789abcdef' for c in key):
        raise ValueError('Invalid cache key')
    root = Path(workspace).resolve()
    path = root/'.agent_history/cache/question_evidence'/f'{key}.json'
    for candidate in [path, *path.parents]:
        if candidate == root:
            break
        if candidate.is_symlink() or (hasattr(candidate, 'is_junction') and candidate.is_junction()):
            raise ValueError('Linked cache path')
    if not path.resolve().is_relative_to(root):
        raise ValueError('Cache path escapes workspace')
    return path


def load_profile(workspace, key, paper_id, text):
    try:
        entry = json.loads(path_for(workspace, key).read_text(encoding='utf-8'))
        profile = entry.get('profile')
        digest = hashlib.sha256(text.encode()).hexdigest()
        if entry.get('version') != CACHE_VERSION or entry.get('key') != key or not isinstance(profile, dict):
            return None
        if profile.get('paper_id') != paper_id or profile.get('document_sha256') != digest or profile.get('error'):
            return None
        if not any(e.get('source_verified') for e in profile.get('evidence', []) if isinstance(e, dict)):
            return None
        return profile
    except (OSError, ValueError, TypeError, AttributeError):
        return None


def save_profile(workspace, key, profile):
    if profile.get('error') or not any(e.get('source_verified') for e in profile.get('evidence', []) if isinstance(e, dict)):
        return
    temporary = None
    try:
        target = path_for(workspace, key)
        target.parent.mkdir(parents=True, exist_ok=True)
        path_for(workspace, key)
        with tempfile.NamedTemporaryFile(mode='w', encoding='utf-8', dir=target.parent,
                                         suffix='.tmp', delete=False) as stream:
            temporary = Path(stream.name)
            json.dump({'version': CACHE_VERSION, 'key': key, 'profile': profile}, stream, ensure_ascii=False)
        temporary.replace(target)
    except (OSError, ValueError, TypeError):
        logger.warning('Paper evidence cache could not be saved; validated result remains usable')
    finally:
        if temporary is not None and temporary.exists():
            try:
                temporary.unlink()
            except OSError:
                logger.warning('Temporary evidence cache could not be removed')
