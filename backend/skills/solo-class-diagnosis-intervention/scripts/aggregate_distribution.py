#!/usr/bin/env python3
"""Deterministically aggregate P/U/M/R/EA counts from Skill 2 results."""

from __future__ import annotations

import argparse
import json
from pathlib import Path


LEVELS = ("P", "U", "M", "R", "EA")
LEVEL_NAMES = {
    "P": "前结构",
    "U": "单点结构",
    "M": "多点结构",
    "R": "关联结构",
    "EA": "拓展抽象结构",
}


def aggregate(payload: dict) -> dict:
    results = payload.get("student_diagnosis_results")
    if not isinstance(results, list) or not results:
        raise ValueError("student_diagnosis_results must be a non-empty array")

    seen_ids: set[str] = set()
    buckets = {level: [] for level in LEVELS}
    assignments = []

    for index, result in enumerate(results):
        try:
            student = result["student"]
            student_id = student["student_id"]
            student_name = student["student_name"]
            level = result["diagnosis"]["level"]
            current_level = result["progression"]["current_level"]
        except (KeyError, TypeError) as exc:
            raise ValueError(f"invalid Skill 2 result at index {index}: {exc}") from exc

        if student_id in seen_ids:
            raise ValueError(f"duplicate student_id: {student_id}")
        if level not in LEVELS:
            raise ValueError(f"invalid SOLO level for {student_id}: {level}")
        if current_level != level:
            raise ValueError(
                f"inconsistent upstream level for {student_id}: diagnosis={level}, progression={current_level}"
            )

        seen_ids.add(student_id)
        ref = {"student_id": student_id, "student_name": student_name}
        buckets[level].append(ref)
        assignments.append({"student_id": student_id, "original_level": level})

    total = len(results)
    distribution = [
        {
            "level": level,
            "level_name": LEVEL_NAMES[level],
            "count": len(buckets[level]),
            "proportion": len(buckets[level]) / total,
            "students": buckets[level],
        }
        for level in LEVELS
    ]
    return {
        "total_students": total,
        "distribution": distribution,
        "level_assignments": assignments,
    }


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("input_json", type=Path)
    args = parser.parse_args()
    payload = json.loads(args.input_json.read_text(encoding="utf-8"))
    print(json.dumps(aggregate(payload), ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
