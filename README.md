# RAI + NPS Risk Dashboard

Live at: https://<your-username>.github.io/<repo-name>/rai_dashboard.html

Data refreshes automatically every day via the GitHub Actions workflow in
`.github/workflows/update-data.yml` (runs update_rai.py + update_nps.py and
commits the refreshed JSON files, which GitHub Pages then serves).

To trigger an immediate refresh instead of waiting for the daily schedule,
go to the "Actions" tab -> "Update RAI & NPS data" -> "Run workflow".
