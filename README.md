# hubspot-workflow-auditor

## Local web app

This project now supports a browser-based HubSpot workflow auditor.

1. Create and activate a virtual environment.
2. Install dependencies with `pip install -r requirements.txt`.
3. Start the server with `python app.py`.
4. Open `http://127.0.0.1:5000`.
5. Paste a HubSpot private app token into the form.

The safer pattern is:

- The browser submits the token to your backend.
- The backend calls HubSpot.
- The backend renders the results.

Do not put the HubSpot token into browser-side JavaScript.

## Workflow requirements

Users should create a HubSpot private app token with:

- `automation`

The audited HubSpot account must also have workflow access in its subscription.

## Share it on the web

The simplest path for this app is to deploy it as a Flask web service on Render.

### What to do

1. Push this project to a GitHub repo.
2. Create a new Render Web Service from that repo.
3. Deploy and share the generated `onrender.com` URL.

### Render settings

- Build command: `pip install -r requirements.txt`
- Start command: `gunicorn app:app`

This repo also includes `render.yaml`, so Render can pick up the service config automatically.

### Important security note

This hosted app is designed so each user pastes their own HubSpot private app token into the form.
Do not configure a shared production token for all users unless that is intentional.
