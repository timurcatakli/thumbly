"""Token digests: OAuth grants, device codes and owner identities are stored as SHA-256 only."""

import hashlib


def digest(token):
    return hashlib.sha256(token.encode()).hexdigest()
