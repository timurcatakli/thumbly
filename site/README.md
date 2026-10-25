# Thumbly landing page

Live at https://timurcatakli.github.io/thumbly/

## Install into the repo

1. Copy `index.html` to `thumbly/site/index.html`.
2. Copy `.github/workflows/pages.yml` to `thumbly/.github/workflows/pages.yml`.
3. In GitHub: Settings → Pages → Build and deployment → Source: **GitHub Actions**.
4. Commit and push to `main`. The page deploys in about a minute.

## Updating

Re-export `index.html` from the design and push it. Only changes under `site/` trigger a deploy.
