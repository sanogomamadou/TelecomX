"""Where generated events go. Kafka arrives in lot 1.6; until then, files and memory."""

from __future__ import annotations

import json
from collections import defaultdict
from collections.abc import Iterable
from pathlib import Path
from typing import Any

from telecomx_generators.events.engine import Emitted

__all__ = ["JsonlSink", "MemorySink"]


class MemorySink:
    """Keeps events grouped by topic. For tests."""

    def __init__(self) -> None:
        self.by_topic: dict[str, list[Emitted]] = defaultdict(list)

    def write(self, emitted: Iterable[Emitted]) -> int:
        count = 0
        for item in emitted:
            self.by_topic[item.topic].append(item)
            count += 1
        return count


class JsonlSink:
    """One ``<topic>.jsonl`` file per topic, one JSON event per line."""

    def __init__(self, directory: Path) -> None:
        self.directory = directory
        directory.mkdir(parents=True, exist_ok=True)
        self.counts: dict[str, int] = defaultdict(int)

    def write(self, emitted: Iterable[Emitted]) -> int:
        handles: dict[str, Any] = {}
        total = 0
        try:
            for item in emitted:
                handle = handles.get(item.topic)
                if handle is None:
                    handle = handles[item.topic] = (self.directory / f"{item.topic}.jsonl").open(
                        "a", encoding="utf-8", newline="\n"
                    )
                payload = json.loads(item.event.model_dump_json())
                handle.write(json.dumps(payload, ensure_ascii=False, separators=(",", ":")) + "\n")
                self.counts[item.topic] += 1
                total += 1
        finally:
            for handle in handles.values():
                handle.close()
        return total
