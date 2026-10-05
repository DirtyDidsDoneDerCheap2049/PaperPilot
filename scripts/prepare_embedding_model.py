"""Prepare the pinned, Apache-2.0 multilingual ONNX model for offline releases."""
import argparse
import hashlib
import json
from pathlib import Path
import requests

ROOT = Path(__file__).resolve().parents[1]
REPO = 'Xenova/paraphrase-multilingual-MiniLM-L12-v2'
REVISION = '2c4055b12046f11709e9df2c122e59ffbdc2f900'


def prepare(inspect=False):
    destination = ROOT / 'build/models/paper-embedding'
    destination.mkdir(parents=True, exist_ok=True)
    manifest_path = destination / 'manifest.json'
    old = json.loads(manifest_path.read_text('utf-8')) if manifest_path.exists() else None
    revision = REVISION
    if inspect:
        response = requests.get(f'https://huggingface.co/api/models/{REPO}/revision/{revision}', timeout=60)
        response.raise_for_status()
        print(json.dumps({'revision': revision, 'files': [f['rfilename'] for f in response.json()['siblings']]}, indent=2))
        return
    files = ['onnx/model_quantized.onnx', 'tokenizer.json', 'config.json', 'tokenizer_config.json', 'special_tokens_map.json']
    hashes = {}
    for name in files:
        target = destination / name
        target.parent.mkdir(parents=True, exist_ok=True)
        if target.exists() and old and old['files'].get(name) == hashlib.file_digest(target.open('rb'), 'sha256').hexdigest():
            hashes[name] = old['files'][name]
            continue
        temporary = target.with_suffix(target.suffix + '.download')
        print(f'Downloading {name}', flush=True)
        with requests.get(f'https://huggingface.co/{REPO}/resolve/{revision}/{name}', stream=True, timeout=(30, 120)) as response:
            response.raise_for_status()
            with temporary.open('wb') as stream:
                for block in response.iter_content(1024 * 1024):
                    stream.write(block)
        hashes[name] = hashlib.file_digest(temporary.open('rb'), 'sha256').hexdigest()
        temporary.replace(target)
    manifest_path.write_text(json.dumps({'repository': REPO, 'revision': revision, 'license': 'Apache-2.0',
        'dimensions': 384, 'max_sequence_length': 128, 'files': hashes}, indent=2), 'utf-8')
    notice = ('Multilingual sentence embeddings, Apache-2.0.\n'
              'Original: https://huggingface.co/sentence-transformers/paraphrase-multilingual-MiniLM-L12-v2\n'
              f'ONNX conversion: https://huggingface.co/{REPO}/tree/{revision}\n')
    (destination / 'NOTICE.txt').write_text(notice, 'utf-8')
    response = requests.get('https://www.apache.org/licenses/LICENSE-2.0.txt', timeout=60)
    response.raise_for_status()
    (destination / 'LICENSE.txt').write_text(response.text, 'utf-8')
    print(destination, flush=True)


if __name__ == '__main__':
    parser = argparse.ArgumentParser()
    parser.add_argument('--inspect', action='store_true')
    prepare(parser.parse_args().inspect)
