#!/usr/bin/env python3
"""测试 bag 解包完整性"""
import os
import sys
from collections import defaultdict

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from gsbag import gsbag_reader

BAG_DIR = "/root/data/bags/20260124_085515"
BAG_FILES = ["default.bag", "camera.bag", "lidar.bag"]

# metadata.yaml 中预期的 camera 帧数
EXPECTED_CAMERA_COUNTS = {
    "/gac/cam/fw120_encoded": 3313,
    "/gac/cam/fw60_encoded": 1182,
    "/gac/cam/ft30_encoded": 3313,
    "/gac/cam/ft20_encoded": 1183,
    "/gac/cam/r50_encoded": 1182,
    "/gac/cam/fl99_encoded": 1182,
    "/gac/cam/fr99_encoded": 1182,
    "/gac/cam/rl99_encoded": 1182,
    "/gac/cam/rr99_encoded": 1182,
}

EXPECTED_LIDAR_COUNTS = {
    "/gac/lidar/lidar_data_raw": 1200,
}

EXPECTED_TOTAL = 101606


def count_bag(bag_file):
    path = os.path.join(BAG_DIR, bag_file)
    if not os.path.exists(path):
        print(f"  [SKIP] {bag_file} not found")
        return {}

    reader = gsbag_reader.GsBagReader(path)
    topic_counts = defaultdict(int)
    total = 0
    for msg in reader.read_messages():
        topic_counts[msg.topic_name] += 1
        total += 1

    print(f"  {bag_file}: {total} messages")
    for topic, count in sorted(topic_counts.items()):
        print(f"    {topic}: {count}")
    return dict(topic_counts), total


def main():
    print("=" * 60)
    print("Bag extraction test")
    print(f"Bag directory: {BAG_DIR}")
    print("=" * 60)

    all_topic_counts = defaultdict(int)
    grand_total = 0

    for bag_file in BAG_FILES:
        counts, total = count_bag(bag_file)
        for topic, c in counts.items():
            all_topic_counts[topic] += c
        grand_total += total

    print("\n" + "=" * 60)
    print("Summary")
    print("=" * 60)
    print(f"Total messages: {grand_total} (expected: {EXPECTED_TOTAL})")

    print("\nCamera topics:")
    for topic, expected in EXPECTED_CAMERA_COUNTS.items():
        actual = all_topic_counts.get(topic, 0)
        status = "✅" if actual == expected else "❌"
        print(f"  {status} {topic}: {actual} / {expected}")

    print("\nLidar topics:")
    for topic, expected in EXPECTED_LIDAR_COUNTS.items():
        actual = all_topic_counts.get(topic, 0)
        status = "✅" if actual == expected else "❌"
        print(f"  {status} {topic}: {actual} / {expected}")

    # 检查是否完整
    camera_ok = all(
        all_topic_counts.get(t, 0) == c for t, c in EXPECTED_CAMERA_COUNTS.items()
    )
    lidar_ok = all(
        all_topic_counts.get(t, 0) == c for t, c in EXPECTED_LIDAR_COUNTS.items()
    )
    total_ok = grand_total == EXPECTED_TOTAL

    print("\n" + "=" * 60)
    if camera_ok and lidar_ok and total_ok:
        print("✅ All checks passed! Bag is fully extractable.")
    else:
        print("❌ Some checks failed. Bag extraction is incomplete.")
    print("=" * 60)


if __name__ == "__main__":
    main()
