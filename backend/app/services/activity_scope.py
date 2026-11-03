"""Read legacy designs without carrying per-activity student rosters forward."""
from __future__ import annotations

import copy
from typing import Any


def without_activity_rosters(value: Any) -> Any:
    """Remove obsolete activity lists; never infer or reconstruct participants.

    Goal membership and original diagnosis remain intact. Existing database
    records are not migrated: reads and subsequent explicit saves use this view.
    """
    result = copy.deepcopy(value)
    activity_collections = {"activity_units", "activities", "parallel_group_tasks", "formative_evaluation_nodes"}

    def walk(item: Any) -> None:
        if isinstance(item, dict):
            for key, child in item.items():
                if key in activity_collections and isinstance(child, list):
                    for activity in child:
                        if isinstance(activity, dict):
                            activity.pop("target_students", None)
                            activity.pop("target_student_ids", None)
                walk(child)
        elif isinstance(item, list):
            for child in item:
                walk(child)

    walk(result)
    return result
