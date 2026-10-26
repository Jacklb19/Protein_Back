"""Small process-local cache of public PDB mappings; never stores user identities or sequences."""
from collections import OrderedDict
from time import monotonic

from .sifts import ResidueMapping


class SiftsCache:
    def __init__(self, *, max_bytes: int = 4_000_000, ttl_seconds: float = 300):
        self.max_bytes = max_bytes
        self.ttl_seconds = ttl_seconds
        self._items: OrderedDict[str, tuple[float, bytes]] = OrderedDict()
        self._bytes = 0

    def _remove(self, key: str):
        self._bytes -= len(self._items.pop(key)[1])

    def get(self, pdb_id: str) -> ResidueMapping | None:
        now = monotonic()
        for key, (expires, _) in list(self._items.items()):
            if expires <= now:
                self._remove(key)
        item = self._items.get(pdb_id)
        if item is None:
            return None
        self._items.move_to_end(pdb_id)
        return ResidueMapping.model_validate_json(item[1])

    def put(self, mapping: ResidueMapping):
        # Avoid serializing a huge mapping merely to decide not to cache it.
        if len(mapping.rows) > 5_000:
            return
        encoded = mapping.model_dump_json(by_alias=True).encode('utf-8')
        if len(encoded) > min(self.max_bytes, 2_000_000):
            return
        if mapping.pdb_id in self._items:
            self._remove(mapping.pdb_id)
        while self._items and (len(self._items) >= 4 or self._bytes + len(encoded) > self.max_bytes):
            self._remove(next(iter(self._items)))
        self._items[mapping.pdb_id] = (monotonic() + self.ttl_seconds, encoded)
        self._bytes += len(encoded)
