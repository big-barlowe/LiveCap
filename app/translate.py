import json
import logging
import re
import shutil
import threading
import zipfile
from collections import OrderedDict
from functools import lru_cache

import ctranslate2
import sentencepiece
from sacremoses import MosesDetokenizer, MosesPunctNormalizer, MosesTokenizer

from .models import DATA_DIR, download, lock_for

log = logging.getLogger("livecap.translate")

INDEX_URL = "https://raw.githubusercontent.com/argosopentech/argospm-index/main/index.json"
MODELS_DIR = DATA_DIR / "translation"
MAX_LOADED = 8


class SentencePieceTokens:
    def __init__(self, path):
        self.processor = sentencepiece.SentencePieceProcessor(model_file=str(path))

    def encode(self, text):
        return self.processor.encode(text, out_type=str)

    def decode(self, tokens):
        return "".join(tokens).replace("▁", " ").strip()


class BpeTokens:
    """Moses tokens split by byte-pair encoding, which the packages built from OPUS-MT use."""

    END = "</w>"
    JOINER = "@@"

    def __init__(self, path, source, target):
        codes = path.read_text(encoding="utf-8").rstrip("\n").split("\n")
        # Earlier lines are merged first. The "\n" split matters: codes can hold characters splitlines() breaks on.
        self.ranks = {}
        for rank, line in enumerate(codes[1:] if codes[0].startswith("#version") else codes):
            self.ranks.setdefault(tuple(line.split(" ")), rank)
        self.normalizer = MosesPunctNormalizer(source)
        self.tokenizer = MosesTokenizer(source)
        self.detokenizer = MosesDetokenizer(target)
        self._split = lru_cache(maxsize=20000)(self._split_word)

    def _split_word(self, word):
        parts = [*word[:-1], word[-1] + self.END]
        while len(parts) > 1:
            best = min(zip(parts, parts[1:]), key=lambda pair: self.ranks.get(pair, len(self.ranks)))
            if best not in self.ranks:
                break
            merged = []
            index = 0
            while index < len(parts):
                if tuple(parts[index:index + 2]) == best:
                    merged.append(parts[index] + parts[index + 1])
                    index += 2
                else:
                    merged.append(parts[index])
                    index += 1
            parts = merged
        parts[-1] = parts[-1].removesuffix(self.END)
        return [part + self.JOINER for part in parts[:-1]] + parts[-1:]

    def encode(self, text):
        words = self.tokenizer.tokenize(self.normalizer.normalize(text), aggressive_dash_splits=True)
        return [piece for word in words for piece in self._split(word)]

    def decode(self, tokens):
        words = " ".join(tokens).replace(self.JOINER + " ", "").removesuffix(self.JOINER).split()
        # Moses undoes the escaping and hyphen splitting the models were trained on, but puts a space after the colon in
        # a time or a verse reference: "5: 00".
        return re.sub(r"(?<=\d): (?=\d)", ":", self.detokenizer.detokenize(words))


def load_tokens(path, metadata):
    # Packages come with one or the other, and which one can differ between the two directions of a language pair.
    if (path / "bpe.model").exists():
        return BpeTokens(path / "bpe.model", metadata["from_code"], metadata["to_code"])
    return SentencePieceTokens(path / "sentencepiece.model")


class Package:
    def __init__(self, path):
        metadata = json.loads((path / "metadata.json").read_text())
        self.prefix = metadata.get("target_prefix") or None
        self.translator = ctranslate2.Translator(str(path / "model"), device="cpu", compute_type="auto",
                                                 inter_threads=1, intra_threads=2)
        self.tokens = load_tokens(path, metadata)

    def translate(self, text):
        tokens = self.tokens.encode(text)
        prefix = [[self.prefix]] if self.prefix else None
        result = self.translator.translate_batch([tokens], target_prefix=prefix, beam_size=3, max_decoding_length=256,
                                                 replace_unknowns=True)
        output = result[0].hypotheses[0]
        if self.prefix:
            output = output[1:]
        return self.tokens.decode(output)


class Translator:
    """Offline translation with Argos Translate's model packages, pivoting through English."""

    def __init__(self):
        self._loaded = OrderedDict()
        self._lock = threading.Lock()
        self._index = None

    def _links(self, source, target):
        with lock_for("index"):
            if self._index is None:
                path = MODELS_DIR / "index.json"
                if not path.exists():
                    download(INDEX_URL, path)
                self._index = {(p["from_code"], p["to_code"]): p["links"][0] for p in json.loads(path.read_text())}
        try:
            return self._index[source, target]
        except KeyError:
            raise ValueError(f"no translation model for {source} to {target}") from None

    def _path(self, source, target):
        path = MODELS_DIR / f"{source}_{target}"
        with lock_for(path):
            if not (path / "metadata.json").exists():
                archive = path.with_suffix(".argosmodel")
                download(self._links(source, target), archive)
                staging = path.with_suffix(".staging")
                shutil.rmtree(staging, ignore_errors=True)
                with zipfile.ZipFile(archive) as bundle:
                    bundle.extractall(staging)
                # Packages hold one top-level folder; the rename publishes it complete.
                root = next(staging.iterdir())
                root.rename(path)
                shutil.rmtree(staging, ignore_errors=True)
                archive.unlink()
        return path

    def _package(self, source, target):
        key = source, target
        with self._lock:
            if key in self._loaded:
                self._loaded.move_to_end(key)
                return self._loaded[key]
        path = self._path(source, target)
        with lock_for(key):
            with self._lock:
                if key in self._loaded:
                    return self._loaded[key]
            package = Package(path)
            with self._lock:
                self._loaded[key] = package
                while len(self._loaded) > MAX_LOADED:
                    self._loaded.popitem(last=False)
        return package

    @staticmethod
    def _hops(source, target):
        if source == target:
            return []
        if "en" in (source, target):
            return [(source, target)]
        return [(source, "en"), ("en", target)]

    def is_ready(self, source, target):
        return all((MODELS_DIR / f"{a}_{b}" / "metadata.json").exists() for a, b in self._hops(source, target))

    def prepare(self, source, target):
        for a, b in self._hops(source, target):
            self._package(a, b)

    def translate(self, text, source, target):
        for a, b in self._hops(source, target):
            text = self._package(a, b).translate(text)
        return text
