import os
import json

import requests
from flask import Flask, redirect, render_template, request, url_for
from jinja2 import TemplateNotFound

from env_utils import load_dotenv
from run_audit import render_html_report as render_workflow_report
from run_audit import run_audit as run_workflow_audit

load_dotenv()


app = Flask(__name__)


def mask_token(token: str) -> str:
    if len(token) <= 8:
        return "*" * len(token)
    return f"{token[:4]}...{token[-4:]}"


def summarize_hubspot_error(exc: requests.HTTPError) -> str:
    response = exc.response
    status_code = response.status_code if response is not None else "unknown"

    if response is None:
        return f"HubSpot returned an API error: HTTP {status_code}."

    details = ""
    try:
        payload = response.json()
    except ValueError:
        payload = None

    if isinstance(payload, dict):
        parts = []
        for key in ("message", "category", "correlationId"):
            value = payload.get(key)
            if value:
                parts.append(f"{key}: {value}")
        errors = payload.get("errors")
        if isinstance(errors, list) and errors:
            first_error = errors[0]
            if isinstance(first_error, dict):
                for key in ("message", "code", "context"):
                    value = first_error.get(key)
                    if value:
                        if isinstance(value, (dict, list)):
                            value = json.dumps(value)
                        parts.append(f"error_{key}: {value}")
        if parts:
            details = " " + " | ".join(parts)
    else:
        text = response.text.strip()
        if text:
            details = f" Response: {text[:400]}"

    return f"HubSpot returned an API error: HTTP {status_code}.{details}"


def run_workflow_page_audit(token: str) -> dict:
    audit = run_workflow_audit(token)
    report_html = render_workflow_report(
        audit["workflows"],
        audit["touches"],
        audit["collisions"],
        audit["suggestions"],
    )
    return {
        "report_html": report_html,
        "count": len(audit["workflows"]),
        "count_label": "workflows",
    }


def render_workflow_page():
    report_html = None
    error = None
    masked_token = None
    result_count = None
    count_label = None

    if request.method == "POST":
        token = request.form.get("hubspot_token", "").strip()

        if not token:
            error = "Paste a HubSpot private app access token to run the audit."
        else:
            try:
                result = run_workflow_page_audit(token)
                report_html = result["report_html"]
                masked_token = mask_token(token)
                result_count = result["count"]
                count_label = result["count_label"]
            except requests.HTTPError as exc:
                error = summarize_hubspot_error(exc)
            except requests.RequestException as exc:
                error = f"Network error while contacting HubSpot: {exc.__class__.__name__}."
            except TemplateNotFound:
                error = "The report template could not be found."
            except Exception as exc:
                error = f"Unexpected error while running the audit: {exc.__class__.__name__}."

    return render_template(
        "index.html",
        report_html=report_html,
        error=error,
        masked_token=masked_token,
        result_count=result_count,
        count_label=count_label,
    )


@app.route("/", methods=["GET", "POST"])
def index():
    return render_workflow_page()


@app.route("/workflows", methods=["GET", "POST"])
def workflows():
    if request.method == "POST":
        return render_workflow_page()
    return redirect(url_for("index"))


if __name__ == "__main__":
    port = int(os.getenv("PORT", "5000"))
    app.run(debug=True, host="0.0.0.0", port=port)
