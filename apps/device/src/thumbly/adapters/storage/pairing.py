import hashlib
import os
from pathlib import Path

from pydantic import ValidationError

from thumbly.domain.models import Binding


def device_tag(binding: Binding) -> str:
    """Pseudonymous profile key; independent of the transport so validation survives a mode switch."""
    return hashlib.sha256(binding.udid.replace("-", "").encode()).hexdigest()[:16]


class PairingStore:
    def __init__(self, state_dir: Path):
        self.path = state_dir / "binding.json"

    def read(self) -> Binding | None:
        """An unreadable binding file reads as no binding, so the owner rebinds."""
        if not self.path.exists():
            return None
        try:
            return Binding.model_validate_json(self.path.read_bytes())
        except ValidationError:
            return None

    def delete(self):
        self.path.unlink(missing_ok=True)

    def write(self, binding: Binding):
        temp = self.path.with_suffix(".tmp")
        fd = os.open(temp, os.O_CREAT | os.O_TRUNC | os.O_WRONLY, 0o600)
        with os.fdopen(fd, "w") as handle:
            handle.write(binding.model_dump_json())
            handle.flush()
            os.fsync(handle.fileno())
        temp.replace(self.path)
        directory = os.open(self.path.parent, os.O_RDONLY)
        try:
            os.fsync(directory)
        finally:
            os.close(directory)
