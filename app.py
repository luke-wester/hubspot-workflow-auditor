import os
import json

import requests
from flask import Flask, render_template, request
from jinja2 import TemplateNotFound

from env_utils import load_dotenv
from run_list_audit import render_html_report, run_list_audit

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
        return f"HubSpot returned an API error while loading lists: HTTP {status_code}."

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

    return f"HubSpot returned an API error while loading lists: HTTP {status_code}.{details}"


@app.route("/", methods=["GET", "POST"])
def index():
    report_html = None
    error = None
    masked_token = None
    list_count = None

    if request.method == "POST":
        token = request.form.get("hubspot_token", "").strip() or os.getenv("HUBSPOT_TOKEN", "").strip()

        if not token:
            error = "Paste a HubSpot private app access token, or set HUBSPOT_TOKEN in .env."
        else:
            try:
                audit = run_list_audit(token)
                report_html = render_html_report(
                    audit["summary"],
                    audit["inventory"],
                    audit["filter_rows"],
                    audit["property_usage"],
                    audit["findings"],
                )
                masked_token = mask_token(token)
                list_count = len(audit["inventory"])
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
        list_count=list_count,
        has_env_token=bool(os.getenv("HUBSPOT_TOKEN", "").strip()),
    )


if __name__ == "__main__":
    port = int(os.getenv("PORT", "5000"))
    app.run(debug=True, host="0.0.0.0", port=port)
