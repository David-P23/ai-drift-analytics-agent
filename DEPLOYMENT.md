# Deployment

This app should not rely on `localhost.run` for demos. Those tunnel URLs are temporary and can return `503` or fail to serve Streamlit static assets.

## Recommended for recruiter demos: Render Starter

Use a paid always-on web service for the public portfolio link. The main reason is first impression:
free Streamlit-style hosting can show a sleeping/wake-up screen before the app loads.

This repo includes a `render.yaml` configured for Render's `starter` web service plan.

1. Push the repo to GitHub.
2. Create a new Render web service from the repo.
3. Use Docker environment.
4. Render will read `render.yaml` or build from the `Dockerfile`.
5. Health check path: `/_stcore/health`.
6. Optional: set `TABLEAU_DASHBOARD_URL` after the Tableau dashboard is published.

## Free demo option: Streamlit Community Cloud

1. Create a GitHub repository for this project.
2. Push the project files to the repository.
3. Go to Streamlit Community Cloud and create a new app.
4. Select the GitHub repository.
5. Set the main file path to `app.py`.
6. Deploy.

No secrets are required for the current app. It runs from bundled demo data, optional uploaded spreadsheet data,
and local SQLite storage.

Suggested git commands after creating an empty GitHub repo:

```powershell
git init
git add .
git commit -m "Deploy AI Drift Analytics Agent"
git branch -M main
git remote add origin https://github.com/<your-user>/<your-repo>.git
git push -u origin main
```

## Other Docker Hosts: Railway, Fly, Azure, or similar

This repo includes:

- `Dockerfile`
- `.dockerignore`
- `render.yaml`
- `Procfile`
- `.streamlit/config.toml`
- `runtime.txt`

Azure App Service Free is useful for trials and learning, but it is not the right public portfolio target:
Microsoft describes the free plan as unsupported for production workloads, with no SLA and shared CPU limits.
For a recruiter-facing app, prefer a paid always-on plan or keep Streamlit Community Cloud with the known
wake-up tradeoff.

## Runtime Notes

- Uploaded workbook data is stored in the app container's SQLite file.
- On free ephemeral hosts, that file can reset when the service restarts.
- For a durable multi-user production deployment, replace local SQLite with a managed database and store uploaded workbooks in object storage.
