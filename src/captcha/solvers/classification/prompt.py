"""Prompt-to-label matching for grid heads.

Captcha prompts arrive either as a bare object ("bus"), a plural ("buses") or a
full instruction ("select all images with a traffic light"). The head's label
vocabulary is a fixed row order; this module maps the client string onto one
row deterministically. It is pure stdlib and does not import torch/PIL.
"""

from __future__ import annotations

import re
import unicodedata
from collections.abc import Mapping, Sequence

from captcha.domain.errors import ModelInferenceError

_ARTICLE_PREFIXES = ("a", "an", "the")
#: Strip the instruction wrapper, then articles: "select all images with a bus"
#: and "a bus" both normalize to "bus".
_PROMPT_PREFIXES = (
    "select all images with",
    "select all squares with",
    "select all pictures with",
    "select all",
    "pick all images with",
    "pick all squares with",
    "pick all pictures with",
    "pick all",
    "choose all images with",
    "choose all squares with",
    "choose all",
    "click on",
    "click the",
    "find all",
    "find the",
    "images of",
    "image of",
)
_PUNCTUATION = re.compile(r"[^\w\s]+")
_SEPARATORS = re.compile(r"[_\-\u2010-\u2015]+")
_SPACES = re.compile(r"\s+")


def _strip_prefixes(text: str) -> str:
    tokens = text.split()
    for prefix in sorted(_PROMPT_PREFIXES, key=lambda item: -len(item.split())):
        head = prefix.split()
        if tokens[: len(head)] == head:
            return " ".join(tokens[len(head) :])
    return text


def _strip_articles(text: str) -> str:
    tokens = text.split()
    while tokens and tokens[0] in _ARTICLE_PREFIXES:
        tokens = tokens[1:]
    return " ".join(tokens)


def normalize_text(text: str) -> str:
    """Case/separator/punctuation-insensitive prompt or label form."""
    value = unicodedata.normalize("NFKC", str(text)).casefold()
    value = value.replace("'", "").replace("\u2019", "")
    value = _SEPARATORS.sub(" ", value)
    value = _PUNCTUATION.sub(" ", value)
    value = _SPACES.sub(" ", value).strip()
    return _strip_articles(_strip_prefixes(value))


def _number_variants(text: str) -> tuple[str, ...]:
    """Singular/plural forms of the last token: buses -> bus, bus -> buses."""
    words = text.split()
    if not words:
        return ()
    last = words[-1]
    variants = {last}
    if last.endswith("ies") and len(last) > 3:
        variants.add(last[:-3] + "y")
    if last.endswith("es") and len(last) > 2:
        variants.add(last[:-2])
        variants.add(last[:-1])
    if last.endswith("s") and not last.endswith(("ss", "us", "is")) and len(last) > 1:
        variants.add(last[:-1])
    else:
        variants.add(last + "s")
        variants.add(last + "es")
    stem = " ".join(words[:-1])
    return tuple(f"{stem} {variant}".strip() for variant in sorted(variants))


def resolve_label(
    prompt: str,
    labels: Sequence[str],
    aliases: Mapping[str, str] | None = None,
    *,
    head: str = "",
) -> int:
    """Map a client prompt onto a head's label row.

    Order: exact label, explicit alias, singular/plural, same token set,
    unique token subset ("a red bus" -> "bus"). Ambiguous matches are an
    inference error, never a coin flip.
    """
    index = {label: i for i, label in enumerate(labels)}
    alias_map = {normalize_text(alias): normalize_text(target) for alias, target in (aliases or {}).items()}
    candidates = [normalize_text(prompt)]
    if not candidates[0]:
        raise ModelInferenceError("prompt is empty after normalization", context={"head": head, "prompt": prompt})

    for candidate in candidates:
        if candidate in index:
            return index[candidate]
    for candidate in candidates:
        target = alias_map.get(candidate)
        if target is not None:
            return index[target]
    for candidate in candidates:
        for variant in _number_variants(candidate):
            if variant in index:
                return index[variant]
            target = alias_map.get(variant)
            if target is not None:
                return index[target]
    for candidate in candidates:
        tokens = frozenset(candidate.split())
        exact = [label for label in labels if frozenset(label.split()) == tokens]
        if len(exact) == 1:
            return index[exact[0]]
        contained = [label for label in labels if set(label.split()) < tokens]
        if not contained:
            continue
        longest = max(contained, key=lambda label: (len(label.split()), len(label)))
        if sum(1 for label in contained if len(label.split()) == len(longest.split())) == 1:
            return index[longest]
        raise ModelInferenceError(
            "prompt matches more than one head label",
            context={"head": head, "prompt": prompt, "matches": sorted(contained[:16])},
        )

    raise ModelInferenceError(
        f"head '{head}' has no label matching prompt '{prompt}'",
        context={"head": head, "prompt": prompt, "known_labels": list(labels[:32])},
    )


__all__ = ["normalize_text", "resolve_label"]
