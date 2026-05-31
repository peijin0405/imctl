"""Progress tracking — file-based so restarts are safe."""

import json
from pathlib import Path

_BASE = Path(__file__).parent.parent / "data"


def get_paths(output_dir: Path) -> dict:
    return {
        "enriched":  output_dir / "enriched.jsonl",
        "rejected":  output_dir / "rejected.jsonl",
        "failed":    output_dir / "failed.jsonl",
        "completed": output_dir / "completed.txt",
    }


def ensure_dirs(output_dir: Path):
    output_dir.mkdir(parents=True, exist_ok=True)


def load_completed(output_dir: Path) -> set:
    p = get_paths(output_dir)["completed"]
    if not p.exists():
        return set()
    return set(p.read_text().splitlines())


def mark_completed(output_dir: Path, investor_id: str):
    with open(get_paths(output_dir)["completed"], "a") as f:
        f.write(investor_id + "\n")


def append_jsonl(path: Path, record: dict):
    with open(path, "a") as f:
        f.write(json.dumps(record, ensure_ascii=False) + "\n")
