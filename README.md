# Gundam Manga

English-language Gundam manga releases available in Canada and the USA — one convenient spot to find them, with buy links verified daily.

Live site: https://gundam-manga-dashboard.vercel.app

## Features

- **Per-volume catalog:** every in-print English Gundam manga volume across 7 series, with ISBNs, publishers, and release dates.
- **Flexible buy links:** each volume links to whichever stores carry it — Amazon, Indigo, VIZ, and more — verified daily; a store's link is hidden when the volume is confirmed unavailable there (see `check_buy_links.py`).
- **Series & status filters:** filter by series or Released/Upcoming, and search by title, creator, publisher, or ISBN.
- **Daily automated updates:** new volumes are detected and buy links re-verified every day (see `.github/workflows/update-solicitations.yml`).

## Data

Catalog data lives in `data/gundam_manga.json` (one entry per volume). Release facts come from publisher and distributor pages first. Out-of-print and cancelled series are excluded by design.

Sister site: [DC Compact Comics dashboard](https://dc-compact-live-dashboard.vercel.app).
