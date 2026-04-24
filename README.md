# hubspot-workflow-auditor

## Local web app

This project now supports a small Flask UI for running a HubSpot lists and segments audit in a browser.

1. Create and activate a virtual environment.
2. Install dependencies with `pip install -r requirements.txt`.
3. Copy `.env.example` to `.env` and set `HUBSPOT_TOKEN`.
4. Start the server with `python app.py`.
5. Open `http://127.0.0.1:5000`.
6. Paste a HubSpot private app token into the form, or leave it blank to use the token from `.env`.

The safer pattern is:

- The browser submits the token to your backend.
- The backend calls HubSpot.
- The backend renders the results.

Do not put the HubSpot token into browser-side JavaScript.

## Lists audit CLI

To run the new lists/segments audit directly:

```bash
python run_list_audit.py
```

It writes:

- `out/list_report.html`
- `out/lists_inventory.csv`
- `out/list_filter_properties.csv`

## Share it on the web

The simplest path for this app is to deploy it as a Flask web service on Render.

### What to do

1. Push this project to a GitHub repo.
2. Create a new Render Web Service from that repo.
3. Set the environment variable `HUBSPOT_TOKEN` in Render.
4. Deploy and share the generated `onrender.com` URL.

### Render settings

- Build command: `pip install -r requirements.txt`
- Start command: `gunicorn app:app`

This repo also includes `render.yaml`, so Render can pick up the service config automatically.

### Important security note

If you deploy this for other people to use, do not share your personal HubSpot private app token with all users through a single public site unless that is intentional. A safer next step is:

- each user pastes their own private app token into the form, or
- add login/auth before exposing a shared account token behind the app
