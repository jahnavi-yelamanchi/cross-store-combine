"""In-process event bus with Kafka semantics: topics, partitions by key,
consumer-group offsets. Same Avro wire format as Redpanda/Kafka in compose."""

from __future__ import annotations

import zlib
from collections.abc import Callable, Iterable
from dataclasses import dataclass, field

from xstore.cdc.avro import ChangeEventCodec, LocalSchemaRegistry
from xstore.stores.base import ChangeEvent, Store

TOPIC = "xstore.changes"
Handler = Callable[[ChangeEvent], None]


@dataclass
class Message:
    topic: str
    partition: int
    offset: int
    key: str
    value: bytes


@dataclass
class InProcessBus:
    partitions: int = 6
    codec: ChangeEventCodec = field(default_factory=lambda: ChangeEventCodec(LocalSchemaRegistry()))
    _log: dict[tuple[str, int], list[Message]] = field(default_factory=dict)
    _offsets: dict[tuple[str, str, int], int] = field(default_factory=dict)

    def _partition(self, key: str) -> int:
        return zlib.crc32(key.encode()) % self.partitions

    def publish(self, event: ChangeEvent, topic: str = TOPIC) -> Message:
        key = f"{event.store}|{event.entity_type}|{event.key}"
        part = self._partition(key)
        log = self._log.setdefault((topic, part), [])
        msg = Message(topic, part, len(log), key, self.codec.encode(event))
        log.append(msg)
        return msg

    def poll(self, group: str, topic: str = TOPIC) -> list[ChangeEvent]:
        """Return all unconsumed events for the group and commit offsets."""
        out: list[ChangeEvent] = []
        for part in range(self.partitions):
            log = self._log.get((topic, part), [])
            start = self._offsets.get((group, topic, part), 0)
            out.extend(self.codec.decode(m.value) for m in log[start:])
            self._offsets[(group, topic, part)] = len(log)
        return sorted(out, key=lambda e: e.ts_ms)

    def consume(self, group: str, handler: Handler, topic: str = TOPIC) -> int:
        events = self.poll(group, topic)
        for event in events:
            handler(event)
        return len(events)

    def end_offsets(self, topic: str = TOPIC) -> dict[int, int]:
        """Partition -> next offset (log end), like Kafka high watermarks."""
        return {p: len(self._log.get((topic, p), [])) for p in range(self.partitions)}

    def lag(self, group: str, topic: str = TOPIC) -> int:
        return sum(
            len(self._log.get((topic, p), [])) - self._offsets.get((group, topic, p), 0)
            for p in range(self.partitions)
        )


def pump(stores: Iterable[Store], bus: InProcessBus, cursors: dict[str, int]) -> int:
    """Publish each store's new changes; advance per-store cursors. Returns count."""
    n = 0
    for store in stores:
        for event in store.stream_changes(cursors.get(store.name, 0)):
            bus.publish(event)
            cursors[store.name] = event.seq
            n += 1
    return n
