import csv
import os
import time
from collections import Counter, defaultdict
from datetime import datetime, timezone
from typing import Any, Dict, Iterable, List, Optional, Set

import requests
from jinja2 import Environment, FileSystemLoader

from env_utils import load_dotenv

load_dotenv()

HUBSPOT_TOKEN = os.getenv("HUBSPOT_TOKEN")
STALE_DAYS = 180


class HSListClient:
    def __init__(self, token: str):
        if not token:
            raise RuntimeError(
                "HUBSPOT_TOKEN is not set. Add it to your environment before running."
            )
        self.base = "https://api.hubapi.com"
        self.session = requests.Session()
        self.session.headers.update(
            {
                "Authorization": f"Bearer {token}",
                "Content-Type": "application/json",
            }
        )

    def _request(
        self,
        method: str,
        path: str,
        *,
        params: Optional[Dict[str, Any]] = None,
        json_body: Optional[Dict[str, Any]] = None,
    ) -> Dict[str, Any]:
        url = f"{self.base}{path}"
        while True:
            response = self.session.request(
                method,
                url,
                params=params or {},
                json=json_body,
                timeout=30,
            )
            if response.status_code == 429:
                time.sleep(2)
                continue
            response.raise_for_status()
            return response.json()

    def search_lists(self, offset: int = 0, count: int = 500) -> Dict[str, Any]:
        return self._request(
            "POST",
            "/crm/v3/lists/search",
            json_body={"offset": offset, "count": count},
        )

    def get_list(self, list_id: str, include_filters: bool = True) -> Dict[str, Any]:
        params = {"includeFilters": "true"} if include_filters else None
        return self._request("GET", f"/crm/v3/lists/{list_id}", params=params)


def parse_datetime(value: Optional[str]) -> Optional[datetime]:
    if not value:
        return None
    normalized = value.replace("Z", "+00:00")
    try:
        return datetime.fromisoformat(normalized)
    except ValueError:
        return None


def format_datetime(value: Optional[str]) -> str:
    parsed = parse_datetime(value)
    if parsed is None:
        return ""
    return parsed.astimezone(timezone.utc).strftime("%Y-%m-%d %H:%M UTC")


def age_in_days(value: Optional[str]) -> Optional[int]:
    parsed = parse_datetime(value)
    if parsed is None:
        return None
    return max(0, (datetime.now(timezone.utc) - parsed.astimezone(timezone.utc)).days)


def object_label(object_type_id: Optional[str]) -> str:
    mapping = {
        "0-1": "Contacts",
        "0-2": "Companies",
        "0-3": "Deals",
        "0-5": "Tickets",
        "0-48": "Products",
        "0-4": "Tasks",
    }
    return mapping.get(object_type_id or "", object_type_id or "Unknown")


def walk_filter_branch(branch: Any) -> Iterable[Dict[str, Any]]:
    if not isinstance(branch, dict):
        return

    for flt in branch.get("filters", []):
        if isinstance(flt, dict):
            yield flt

    for child in branch.get("filterBranches", []):
        if isinstance(child, dict):
            yield from walk_filter_branch(child)


def extract_filter_properties(list_definition: Dict[str, Any]) -> List[Dict[str, str]]:
    results = []
    list_id = str(list_definition.get("listId", ""))
    list_name = list_definition.get("name", "")

    for flt in walk_filter_branch(list_definition.get("filterBranch")):
        property_name = flt.get("property")
        filter_type = flt.get("filterType", "")
        operator = ""
        operation = flt.get("operation")
        if isinstance(operation, dict):
            operator = operation.get("operator", "")

        if property_name:
            results.append(
                {
                    "list_id": list_id,
                    "list_name": list_name,
                    "property": property_name,
                    "filter_type": filter_type,
                    "operator": operator,
                }
            )

    return results


def fetch_all_lists(client: HSListClient) -> List[Dict[str, Any]]:
    offset = 0
    definitions: List[Dict[str, Any]] = []

    while True:
        page = client.search_lists(offset=offset, count=500)
        for item in page.get("lists", []):
            list_id = item.get("listId")
            if not list_id:
                continue
            detailed = client.get_list(str(list_id), include_filters=True)
            definitions.append(detailed.get("list") or detailed)

        if not page.get("hasMore"):
            break
        offset = int(page.get("offset", 0))

    return definitions


def build_inventory(list_definitions: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    inventory = []
    for entry in list_definitions:
        size = entry.get("size")
        inventory.append(
            {
                "id": str(entry.get("listId", "")),
                "name": entry.get("name", ""),
                "object_type_id": entry.get("objectTypeId", ""),
                "object_label": object_label(entry.get("objectTypeId")),
                "processing_type": entry.get("processingType", ""),
                "processing_status": entry.get("processingStatus", ""),
                "size": size if size is not None else "",
                "created_at": entry.get("createdAt", ""),
                "updated_at": entry.get("updatedAt", ""),
                "updated_at_label": format_datetime(entry.get("updatedAt")),
                "days_since_update": age_in_days(entry.get("updatedAt")),
                "has_filters": bool(entry.get("filterBranch")),
            }
        )
    inventory.sort(key=lambda item: (item["name"].lower(), item["id"]))
    return inventory


def build_summary(inventory: List[Dict[str, Any]]) -> Dict[str, Any]:
    processing_counts = Counter(item["processing_type"] or "UNKNOWN" for item in inventory)
    object_counts = Counter(item["object_label"] for item in inventory)
    total_members = sum(int(item["size"]) for item in inventory if str(item["size"]).isdigit())

    return {
        "total_lists": len(inventory),
        "total_members": total_members,
        "processing_counts": dict(sorted(processing_counts.items())),
        "object_counts": dict(sorted(object_counts.items())),
    }


def build_property_usage(filter_rows: List[Dict[str, str]]) -> List[Dict[str, Any]]:
    grouped: Dict[str, Dict[str, Any]] = {}
    for row in filter_rows:
        prop = row["property"]
        bucket = grouped.setdefault(
            prop,
            {"property": prop, "list_count": 0, "lists": set(), "operators": set()},
        )
        bucket["lists"].add(row["list_name"])
        if row["operator"]:
            bucket["operators"].add(row["operator"])

    results = []
    for prop, bucket in grouped.items():
        results.append(
            {
                "property": prop,
                "list_count": len(bucket["lists"]),
                "lists": ", ".join(sorted(bucket["lists"])[:6]),
                "operators": ", ".join(sorted(bucket["operators"])),
            }
        )
    results.sort(key=lambda item: (-item["list_count"], item["property"]))
    return results


def build_findings(
    inventory: List[Dict[str, Any]], filter_rows: List[Dict[str, str]]
) -> Dict[str, List[Dict[str, Any]]]:
    findings: Dict[str, List[Dict[str, Any]]] = {
        "duplicate_names": [],
        "stale_lists": [],
        "empty_manual_lists": [],
        "heavy_reuse_properties": [],
    }

    names: Dict[str, List[Dict[str, Any]]] = defaultdict(list)
    for item in inventory:
        names[item["name"].strip().lower()].append(item)

    for _, items in names.items():
        if len(items) > 1:
            findings["duplicate_names"].append(
                {
                    "name": items[0]["name"],
                    "count": len(items),
                    "ids": ", ".join(item["id"] for item in items),
                }
            )

    for item in inventory:
        days = item.get("days_since_update")
        if days is not None and days >= STALE_DAYS:
            findings["stale_lists"].append(
                {
                    "name": item["name"],
                    "days_since_update": days,
                    "processing_type": item["processing_type"],
                    "size": item["size"],
                }
            )
        if item["processing_type"] == "MANUAL" and str(item["size"]) in {"", "0"}:
            findings["empty_manual_lists"].append(
                {
                    "name": item["name"],
                    "id": item["id"],
                    "updated_at_label": item["updated_at_label"],
                }
            )

    property_usage = build_property_usage(filter_rows)
    findings["heavy_reuse_properties"] = [
        row for row in property_usage if row["list_count"] >= 3
    ][:15]

    return findings


def write_csv(path: str, rows: List[Dict[str, Any]], fieldnames: List[str]) -> None:
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "w", encoding="utf-8", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=fieldnames)
        writer.writeheader()
        writer.writerows(rows)


def render_html_report(
    summary: Dict[str, Any],
    inventory: List[Dict[str, Any]],
    filter_rows: List[Dict[str, str]],
    property_usage: List[Dict[str, Any]],
    findings: Dict[str, List[Dict[str, Any]]],
) -> str:
    env = Environment(loader=FileSystemLoader("templates"))
    template = env.get_template("list_report.html")
    return template.render(
        summary=summary,
        inventory=inventory,
        filter_rows=filter_rows,
        property_usage=property_usage,
        findings=findings,
        stale_days=STALE_DAYS,
    )


def write_html_report(html: str, output_path: str = "out/list_report.html") -> None:
    os.makedirs(os.path.dirname(output_path), exist_ok=True)
    with open(output_path, "w", encoding="utf-8") as handle:
        handle.write(html)


def run_list_audit(token: str) -> Dict[str, Any]:
    client = HSListClient(token)
    list_definitions = fetch_all_lists(client)
    inventory = build_inventory(list_definitions)
    filter_rows: List[Dict[str, str]] = []
    for item in list_definitions:
        filter_rows.extend(extract_filter_properties(item))
    property_usage = build_property_usage(filter_rows)
    findings = build_findings(inventory, filter_rows)
    summary = build_summary(inventory)

    return {
        "summary": summary,
        "inventory": inventory,
        "filter_rows": filter_rows,
        "property_usage": property_usage,
        "findings": findings,
    }


def main() -> None:
    print("Connecting to HubSpot lists API...")
    audit = run_list_audit(HUBSPOT_TOKEN)

    write_csv(
        "out/lists_inventory.csv",
        audit["inventory"],
        [
            "id",
            "name",
            "object_type_id",
            "object_label",
            "processing_type",
            "processing_status",
            "size",
            "created_at",
            "updated_at",
            "updated_at_label",
            "days_since_update",
            "has_filters",
        ],
    )
    write_csv(
        "out/list_filter_properties.csv",
        audit["filter_rows"],
        ["list_id", "list_name", "property", "filter_type", "operator"],
    )
    html = render_html_report(
        audit["summary"],
        audit["inventory"],
        audit["filter_rows"],
        audit["property_usage"],
        audit["findings"],
    )
    write_html_report(html)

    print("\nDone! Open this file:")
    print("  out/list_report.html\n")


if __name__ == "__main__":
    main()
