"""Offline multilingual embeddings. No network or synthetic-vector fallback."""
import hashlib
import json
import sys
import threading
from pathlib import Path


def model_directory():
    if getattr(sys, 'frozen', False):
        return Path(sys._MEIPASS) / 'resources/models/paper-embedding'
    return Path(__file__).resolve().parents[2] / 'build/models/paper-embedding'


class LocalEmbedding:
    name = 'multilingual-minilm-onnx-v1'

    def __init__(self, directory=None):
        self.directory = Path(directory) if directory else model_directory()
        self._session = self._tokenizer = None
        self._lock = threading.RLock()

    @property
    def available(self):
        return all((self.directory / p).is_file() for p in ('manifest.json', 'tokenizer.json', 'onnx/model_quantized.onnx'))

    @property
    def fingerprint(self):
        if not self.available:
            return 'unavailable'
        return hashlib.sha256((self.directory / 'manifest.json').read_bytes()).hexdigest()[:20]

    def _load(self):
        if self._session is not None:
            return
        if not self.available:
            raise RuntimeError('本地向量模型缺失；源码环境请运行 scripts/prepare_embedding_model.py，软件包请重新安装完整版本')
        import onnxruntime as ort
        from tokenizers import Tokenizer
        manifest = json.loads((self.directory / 'manifest.json').read_text('utf-8'))
        for filename in ('onnx/model_quantized.onnx', 'tokenizer.json'):
            with (self.directory / filename).open('rb') as stream:
                actual = hashlib.file_digest(stream, 'sha256').hexdigest()
            if actual != manifest['files'][filename]:
                raise RuntimeError('本地向量模型校验失败：' + filename)
        options = ort.SessionOptions()
        options.intra_op_num_threads = 2
        options.inter_op_num_threads = 1
        self._session = ort.InferenceSession(str(self.directory / 'onnx/model_quantized.onnx'),
                                            sess_options=options, providers=['CPUExecutionProvider'])
        self._tokenizer = Tokenizer.from_file(str(self.directory / 'tokenizer.json'))
        self._tokenizer.enable_truncation(max_length=128)
        self._tokenizer.enable_padding()

    def offsets(self, text):
        # Independent tokenizer: offsets must cover the whole source, without
        # the inference tokenizer's 128-token truncation or batch padding.
        from tokenizers import Tokenizer
        if not self.available:
            raise RuntimeError('本地向量模型缺失')
        tokenizer = Tokenizer.from_file(str(self.directory / 'tokenizer.json'))
        tokenizer.no_truncation()
        tokenizer.no_padding()
        return [offset for offset in tokenizer.encode(text, add_special_tokens=False).offsets if offset[1] > offset[0]]

    def encode(self, texts):
        import numpy as np
        with self._lock:
            self._load()
            vectors = []
            for begin in range(0, len(texts), 32):
                batch = self._tokenizer.encode_batch([str(t) for t in texts[begin:begin+32]])
                candidates = {'input_ids': np.array([t.ids for t in batch], dtype=np.int64),
                    'attention_mask': np.array([t.attention_mask for t in batch], dtype=np.int64),
                    'token_type_ids': np.array([t.type_ids for t in batch], dtype=np.int64)}
                inputs = {item.name: candidates[item.name] for item in self._session.get_inputs()}
                output = self._session.run(None, inputs)[0]
                if output.ndim == 3:
                    mask = candidates['attention_mask'][..., None]
                    output = (output * mask).sum(axis=1) / np.maximum(mask.sum(axis=1), 1)
                output = output / np.maximum(np.linalg.norm(output, axis=1, keepdims=True), 1e-12)
                vectors.extend(output.astype(np.float32).tolist())
            return vectors
