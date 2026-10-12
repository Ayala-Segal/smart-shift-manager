import re

MAX_PARAGRAPH = 400


def split_paragraphs(text):
    paragraphs = []
    for block in re.split(r"\n+", text or ""):
        cleaned = " ".join(block.split())
        if not cleaned:
            continue
        paragraphs.extend(_split_long(cleaned))
    return paragraphs


def _split_long(paragraph):
    if len(paragraph) <= MAX_PARAGRAPH:
        return [paragraph]
    sentences = re.split(r"(?<=[.])\s+", paragraph)
    chunks = []
    current = ""
    for sentence in sentences:
        if not sentence:
            continue
        candidate = sentence if not current else current + " " + sentence
        if current and len(candidate) > MAX_PARAGRAPH:
            chunks.append(current)
            current = sentence
        else:
            current = candidate
    if current:
        chunks.append(current)
    return chunks or [paragraph[:MAX_PARAGRAPH]]
