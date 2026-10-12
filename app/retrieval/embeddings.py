import hashlib
import math
import re

DIMENSIONS = 128

# Fold related scheduling words onto one feature so a shift requirement
# can match a profile that uses different wording.
SYNONYMS = {
    "cash": "cashier",
    "cashier": "cashier",
    "register": "cashier",
    "till": "cashier",
    "lead": "lead",
    "supervisor": "lead",
    "evening": "evening",
    "night": "evening",
    "busy": "busy",
    "rush": "busy",
    "crowded": "busy",
    "morning": "morning",
    "opening": "morning",
    "weekend": "weekend",
    "saturday": "weekend",
    "customer": "customer",
    "service": "customer",
    "overnight": "overnight",
}


class LocalTokenEmbedder:
    """Build a normalized vector from tokens and character trigrams.

    The vector store keeps these vectors. They are not written to SQL Server.
    """

    def name(self):
        return "local-token"

    def __call__(self, input):
        return [_embed_text(text) for text in input]


def _embed_text(text):
    vector = [0.0] * DIMENSIONS
    for token in re.findall(r"[a-z0-9]+", (text or "").lower()):
        folded = SYNONYMS.get(token, token)
        _accumulate(vector, folded, 1.0)
        if len(folded) >= 3:
            for index in range(len(folded) - 2):
                _accumulate(vector, folded[index : index + 3], 0.3)
    norm = math.sqrt(sum(value * value for value in vector)) or 1.0
    return [value / norm for value in vector]


def _accumulate(vector, key, weight):
    digest = hashlib.sha256(key.encode("utf-8")).digest()
    slot = int.from_bytes(digest[:2], "big") % DIMENSIONS
    vector[slot] += weight
