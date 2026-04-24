import os
import time
import itertools
from collections import defaultdict
from typing import List, Dict, Any

import requests
from jinja2 import Environment, FileSystemLoader

from env_utils import load_dotenv

load_dotenv()

HUBSPOT_TOKEN = os.getenv("HUBSPOT_TOKEN")

###############################################################################
# HUBSPOT CLIENT
###############################################################################

class HSClient:
    def __init__(self, token: str):
        if not token:
            raise RuntimeError(
                "HUBSPOT_TOKEN is not set. Add it to your environment before running."
            )
        self.token = token
        self.base = "https://api.hubapi.com"
        self.session = requests.Session()
        self.session.headers.update(
            {
                "Authorization": f"Bearer {self.token}",
                "Content-Type": "application/json",
            }
        )

    def _get(self, path: str, params: Dict[str, Any] = None) -> Dict[str, Any]:
        url = f"{self.base}{path}"
        while True:
            resp = self.session.get(url, params=params or {}, timeout=30)
            if resp.status_code == 429:
                time.sleep(2)
                continue
            resp.raise_for_status()
            return resp.json()

    def list_workflows(self) -> List[Dict[str, Any]]:
        workflows = []
        path = "/automation/v3/workflows"
        params = {"limit": 250}

        while True:
            data = self._get(path, params)

            if isinstance(data, dict):
                items = data.get("workflows") or data.get("results") or []
            else:
                items = data

            workflows.extend(items)

            if isinstance(data, dict) and data.get("hasMore"):
                params["offset"] = data["offset"]
            else:
                break

        return workflows

    def get_workflow(self, workflow_id: int) -> Dict[str, Any]:
        return self._get(f"/automation/v3/workflows/{workflow_id}")

###############################################################################
# HELPERS
###############################################################################

READ = "READ"
WRITE = "WRITE"

def _safe_get(d: Any, key: str, default=None):
    return d.get(key, default) if isinstance(d, dict) else default

###############################################################################
# PROPERTY TOUCH EXTRACTION
###############################################################################

def extract_touches(workflow: Dict[str, Any]) -> List[Dict[str, Any]]:
    touches = []
    wid = _safe_get(workflow, "id") or _safe_get(workflow, "workflowId")
    name = _safe_get(workflow, "name", "")
    obj = _safe_get(workflow, "objectType") or _safe_get(workflow, "type", "")

    # Triggers = READ
    triggers = _safe_get(workflow, "startingConditions") or _safe_get(workflow, "triggers") or []
    for trig in triggers:
        prop = trig.get("propertyName") or trig.get("property")
        if prop:
            touches.append({
                "workflow_id": wid,
                "workflow_name": name,
                "object": obj,
                "mode": READ,
                "property": prop,
                "action_type": "TRIGGER",
            })

    # Actions = WRITE or READ (branches)
    def walk_actions(actions):
        for act in actions or []:
            a_type = _safe_get(act, "type", "")

            # Write detection
            prop = (
                _safe_get(act, "propertyName") or
                _safe_get(act, "targetProperty") or
                _safe_get(act, "property")
            )

            if prop and any(word in a_type.upper() for word in ["SET", "COPY", "CLEAR", "_PROPERTY"]):
                touches.append({
                    "workflow_id": wid,
                    "workflow_name": name,
                    "object": obj,
                    "mode": WRITE,
                    "property": prop,
                    "action_type": a_type,
                })

            # Read detection in branches
            conds = _safe_get(act, "conditions") or _safe_get(act, "condition")
            if isinstance(conds, list):
                for c in conds:
                    cprop = _safe_get(c, "propertyName") or _safe_get(c, "property")
                    if cprop:
                        touches.append({
                            "workflow_id": wid,
                            "workflow_name": name,
                            "object": obj,
                            "mode": READ,
                            "property": cprop,
                            "action_type": a_type,
                        })

            if _safe_get(act, "actions"):
                walk_actions(_safe_get(act, "actions"))

    walk_actions(_safe_get(workflow, "actions"))
    return touches

###############################################################################
# SUGGESTIONS ENGINE
###############################################################################

def categorize_property(prop: str) -> str:
    prop = prop.lower()
    if "lifecycle" in prop or "lead_status" in prop:
        return "Lifecycle"
    if "score" in prop:
        return "Scoring"
    if "owner" in prop:
        return "Ownership"
    if "source" in prop:
        return "Attribution"
    if "status" in prop:
        return "Sales Status"
    return "Other"

def build_suggestions(workflows, touches):

    # Build safe name lookup
    wf_names = {}
    for w in workflows:
        wid = str(w.get("id"))
        wf_names[wid] = w.get("name", f"Workflow {wid}")

    def safe_name(wid):
        wid = str(wid)
        return wf_names.get(wid, f"Unknown Workflow ({wid})")

    suggestions = {
        "merge": [],
        "split": [],
        "risky": [],
        "redundant": [],
        "chains": []
    }

    writes_by_wf = defaultdict(set)
    reads_by_wf = defaultdict(set)
    props_by_cat = defaultdict(lambda: defaultdict(int))

    for t in touches:
        wid = str(t["workflow_id"])
        prop = t["property"]
        cat = categorize_property(prop)

        if t["mode"] == WRITE:
            writes_by_wf[wid].add(prop)
            props_by_cat[wid][cat] += 1
        else:
            reads_by_wf[wid].add(prop)

    ###################################################################
    # MERGE SUGGESTIONS
    ###################################################################
    ids = list(wf_names.keys())
    for a, b in itertools.combinations(ids, 2):
        shared = writes_by_wf[a] & writes_by_wf[b]
        if shared:
            suggestions["merge"].append({
                "workflows": [safe_name(a), safe_name(b)],
                "reason": f"Both write to: {', '.join(shared)}"
            })

        # Similar naming pattern merge
        if safe_name(a).split()[0] == safe_name(b).split()[0]:
            suggestions["merge"].append({
                "workflows": [safe_name(a), safe_name(b)],
                "reason": "Workflows share similar naming patterns."
            })

    ###################################################################
    # SPLIT SUGGESTIONS
    ###################################################################
    for wid, cats in props_by_cat.items():
        if len(cats.keys()) >= 3:
            suggestions["split"].append({
                "workflow": safe_name(wid),
                "reason": f"Touches too many unrelated categories: {', '.join(cats.keys())}"
            })
        if len(writes_by_wf[wid]) >= 8:
            suggestions["split"].append({
                "workflow": safe_name(wid),
                "reason": f"Writes {len(writes_by_wf[wid])} different properties."
            })

    ###################################################################
    # RISKY
    ###################################################################
    risky_keywords = ["lifecycle_stage", "email", "phone", "owner", "clear"]
    for wid, props in writes_by_wf.items():
        hits = [p for p in props if any(k in p.lower() for k in risky_keywords)]
        if hits:
            suggestions["risky"].append({
                "workflow": safe_name(wid),
                "reason": f"Risky writes: {', '.join(hits)}"
            })

    ###################################################################
    # REDUNDANT
    ###################################################################
    all_reads = set()
    for rset in reads_by_wf.values():
        all_reads |= rset

    for wid, props in writes_by_wf.items():
        if not props:
            suggestions["redundant"].append({
                "workflow": safe_name(wid),
                "reason": "Does not write any properties."
            })
        elif not (props & all_reads):
            suggestions["redundant"].append({
                "workflow": safe_name(wid),
                "reason": "Writes properties nothing else reads."
            })

    ###################################################################
    # CHAINS
    ###################################################################
    for a in ids:
        for b in ids:
            if a != b:
                shared = writes_by_wf[a] & reads_by_wf[b]
                if shared:
                    suggestions["chains"].append({
                        "from": safe_name(a),
                        "to": safe_name(b),
                        "property": list(shared)[0],
                        "reason": "Workflow dependency chain detected."
                    })

    return suggestions

###############################################################################
# AUDIT BUILDERS
###############################################################################

def fetch_full_workflows(client: HSClient) -> List[Dict[str, Any]]:
    workflows_raw = client.list_workflows()
    full_defs = []

    print("Fetching full workflow definitions...")
    for w in workflows_raw:
        wid = w.get("id")
        try:
            details = client.get_workflow(wid)
            merged = {**w, **details}
        except requests.RequestException:
            merged = w
        full_defs.append(merged)

    return full_defs


def build_inventory(full_defs: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    return [
        {
            "id": w.get("id"),
            "name": w.get("name"),
            "active": w.get("enabled", True),
            "object": w.get("objectType"),
        }
        for w in full_defs
    ]


def build_collisions(touches: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    collisions = []
    by_prop = defaultdict(list)
    for t in touches:
        if t["mode"] == WRITE:
            by_prop[t["property"]].append(t)

    for prop, writers in by_prop.items():
        if len(writers) > 1:
            for a, b in itertools.combinations(writers, 2):
                collisions.append({
                    "property": prop,
                    "workflow_a": a["workflow_name"],
                    "workflow_b": b["workflow_name"],
                })

    return collisions


def run_audit(token: str) -> Dict[str, Any]:
    client = HSClient(token)
    full_defs = fetch_full_workflows(client)
    inventory = build_inventory(full_defs)

    touches = []
    for w in full_defs:
        touches.extend(extract_touches(w))

    collisions = build_collisions(touches)
    suggestions = build_suggestions(inventory, touches)

    return {
        "workflows": inventory,
        "touches": touches,
        "collisions": collisions,
        "suggestions": suggestions,
    }

###############################################################################
# HTML REPORT GENERATION
###############################################################################

def render_html_report(workflows, touches, collisions, suggestions):
    env = Environment(loader=FileSystemLoader("templates"))
    template = env.get_template("report.html")

    return template.render(
        workflows=workflows,
        touches=touches,
        collisions=collisions,
        suggestions=suggestions
    )


def write_html_report(html: str, output_path: str = "out/report.html"):
    os.makedirs(os.path.dirname(output_path), exist_ok=True)
    with open(output_path, "w", encoding="utf-8") as f:
        f.write(html)

###############################################################################
# MAIN
###############################################################################

def main():
    print("Connecting to HubSpot...")
    audit = run_audit(HUBSPOT_TOKEN)
    html = render_html_report(
        audit["workflows"],
        audit["touches"],
        audit["collisions"],
        audit["suggestions"],
    )
    write_html_report(html)

    print("\nDone! Open this file:")
    print("  out/report.html\n")

if __name__ == "__main__":
    main()
