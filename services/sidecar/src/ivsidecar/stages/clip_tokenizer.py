"""CLIP's byte-level BPE tokenizer (T4), read from the model's tokenizer.json.

A small port of OpenAI's simple_tokenizer, so search needs no extra package (the Hugging Face
`tokenizers` wheel would bring huggingface-hub and an HTTP stack into an offline app).
Differences from the original: no ftfy text repair, and `\\p{L}` / `\\p{N}` are approximated
with Python's `re` classes (letters = `[^\\W\\d_]`, numbers = `\\d`). Both only matter for
unusual input; search queries are short everyday phrases.
"""

from __future__ import annotations

import html
import json
import re
from functools import lru_cache
from pathlib import Path

BOS, EOS = "<|startoftext|>", "<|endoftext|>"
CONTEXT = 77  # CLIP's text length, start and end tokens included

_PAT = re.compile(
    r"<\|startoftext\|>|<\|endoftext\|>|'s|'t|'re|'ve|'m|'ll|'d|[^\W\d_]+|\d|(?:[^\s\w]|_)+",
    re.IGNORECASE,
)


@lru_cache
def _byte_to_unicode() -> dict[int, str]:
    """GPT-2's reversible map from bytes to printable characters (no spaces or controls)."""
    bs = list(range(ord("!"), ord("~") + 1)) + list(range(ord("¡"), ord("¬") + 1)) + list(range(ord("®"), ord("ÿ") + 1))
    cs = bs[:]
    n = 0
    for b in range(256):
        if b not in bs:
            bs.append(b)
            cs.append(256 + n)
            n += 1
    return dict(zip(bs, map(chr, cs), strict=True))


def _clean(text: str) -> str:
    text = html.unescape(html.unescape(text)).replace(BOS, " ").replace(EOS, " ")  # typed, not special
    return re.sub(r"\s+", " ", text).strip().lower()


class ClipTokenizer:
    def __init__(self, tokenizer_json: Path):
        model = json.loads(tokenizer_json.read_text(encoding="utf-8"))["model"]
        self.vocab: dict[str, int] = model["vocab"]
        merges = [tuple(m.split(" ")) if isinstance(m, str) else tuple(m) for m in model["merges"]]
        self.ranks = {pair: i for i, pair in enumerate(merges)}
        self.bos, self.eos = self.vocab[BOS], self.vocab[EOS]
        self._cache: dict[str, list[str]] = {}

    def _bpe(self, token: str) -> list[str]:
        if token in self._cache:
            return self._cache[token]
        word = [*token[:-1], token[-1] + "</w>"]
        while len(word) > 1:
            pairs = {(word[i], word[i + 1]) for i in range(len(word) - 1)}
            best = min(pairs, key=lambda p: self.ranks.get(p, float("inf")))
            if best not in self.ranks:
                break
            out, i = [], 0
            while i < len(word):
                if i < len(word) - 1 and (word[i], word[i + 1]) == best:
                    out.append(word[i] + word[i + 1])
                    i += 2
                else:
                    out.append(word[i])
                    i += 1
            word = out
        if len(self._cache) < 10000:
            self._cache[token] = word
        return word

    def encode(self, text: str) -> list[int]:
        """Token ids with start and end tokens, cut to CLIP's 77 (the end token is kept)."""
        b2u = _byte_to_unicode()
        ids: list[int] = []
        for tok in _PAT.findall(_clean(text)):
            mapped = "".join(b2u[b] for b in tok.encode("utf-8"))
            ids.extend(self.vocab[p] for p in self._bpe(mapped) if p in self.vocab)
        return [self.bos, *ids[: CONTEXT - 2], self.eos]
