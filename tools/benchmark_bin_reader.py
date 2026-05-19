import argparse
import hashlib
import json
import resource
import statistics
import sys
import time
from pathlib import Path


REPO_ROOT = Path(__file__).resolve().parents[1]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from ubm_data_mining.gsbag_parser.topic_parser.bin_reader import BinReader


def _build_digest(records):
    hasher = hashlib.sha256()
    hasher.update(str(len(records)).encode("utf-8"))
    if not records:
        return hasher.hexdigest()

    for _, msg_timestamp, receive_timestamp, pub_timestamp in records:
        hasher.update(f"{msg_timestamp}|{receive_timestamp}|{pub_timestamp};".encode("utf-8"))
    return hasher.hexdigest()


def _run_once(file_path, topic, workers, min_parallel_count):
    start = time.perf_counter()
    records = BinReader.load_serialized_to_pb(
        str(file_path),
        topic,
        num_workers=workers,
        min_parallel_count=min_parallel_count,
    )
    elapsed = time.perf_counter() - start
    rss_kb = resource.getrusage(resource.RUSAGE_SELF).ru_maxrss
    count = len(records)

    if records:
        first_msg_ts = int(records[0][1])
        last_msg_ts = int(records[-1][1])
    else:
        first_msg_ts = None
        last_msg_ts = None

    return {
        "elapsed_sec": elapsed,
        "count": count,
        "first_msg_timestamp": first_msg_ts,
        "last_msg_timestamp": last_msg_ts,
        "digest": _build_digest(records),
        "peak_rss_kb": int(rss_kb),
    }


def main():
    parser = argparse.ArgumentParser(description="Benchmark current Python BinReader implementation")
    parser.add_argument("--file-path", required=True, help="Path to .bin file")
    parser.add_argument("--topic", required=True, help="Topic name for protobuf decoding")
    parser.add_argument("--repeat", type=int, default=3, help="Number of repeated runs")
    parser.add_argument("--workers", type=int, default=None, help="Override num_workers")
    parser.add_argument("--min-parallel-count", type=int, default=2000, help="Parallel threshold")
    parser.add_argument("--warmup", action="store_true", help="Run one warmup before benchmark")
    parser.add_argument("--output-json", default="", help="Optional path to save benchmark summary as JSON")
    args = parser.parse_args()

    file_path = Path(args.file_path).expanduser().resolve()
    if not file_path.exists():
        raise FileNotFoundError(f"Bin file not found: {file_path}")

    if args.repeat <= 0:
        raise ValueError("--repeat must be > 0")

    if args.warmup:
        _run_once(file_path, args.topic, args.workers, args.min_parallel_count)

    runs = []
    for index in range(args.repeat):
        result = _run_once(file_path, args.topic, args.workers, args.min_parallel_count)
        result["run_index"] = index + 1
        runs.append(result)

    elapsed_values = [item["elapsed_sec"] for item in runs]
    count_values = [item["count"] for item in runs]
    digest_values = [item["digest"] for item in runs]

    summary = {
        "file_path": str(file_path),
        "topic": args.topic,
        "workers": args.workers,
        "min_parallel_count": args.min_parallel_count,
        "repeat": args.repeat,
        "file_size_bytes": file_path.stat().st_size,
        "count_consistent": len(set(count_values)) == 1,
        "digest_consistent": len(set(digest_values)) == 1,
        "elapsed_sec": {
            "min": min(elapsed_values),
            "max": max(elapsed_values),
            "mean": statistics.mean(elapsed_values),
            "median": statistics.median(elapsed_values),
        },
        "runs": runs,
    }

    print(json.dumps(summary, ensure_ascii=False, indent=2))

    if args.output_json:
        output_path = Path(args.output_json).expanduser().resolve()
        output_path.parent.mkdir(parents=True, exist_ok=True)
        with open(output_path, "w", encoding="utf-8") as f:
            json.dump(summary, f, ensure_ascii=False, indent=2)


if __name__ == "__main__":
    main()