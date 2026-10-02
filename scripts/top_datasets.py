import os
import sys
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime, timezone

import requests

DOMAIN = os.getenv("SOCRATA_DOMAIN", "data.austintexas.gov")
APP_TOKEN = os.getenv("SOCRATA_APP_TOKEN")
TOP_N = int(os.getenv("TOP_N", "10"))
OUTPUT_PATH = os.getenv("OUTPUT_PATH", "TOP_DATASETS.md")

CATALOG_URL = "https://api.us.socrata.com/api/catalog/v1"
PAGE_SIZE = 100
MAX_WORKERS = 8

session = requests.Session()
if APP_TOKEN:
    session.headers["X-App-Token"] = APP_TOKEN


def list_datasets():
    """Page through the Discovery API and return base (non-derived) datasets."""
    datasets, offset = [], 0
    while True:
        resp = session.get(
            CATALOG_URL,
            params={
                "domains": DOMAIN,
                "search_context": DOMAIN,
                "only": "dataset",
                "limit": PAGE_SIZE,
                "offset": offset,
            },
            timeout=60,
        )
        resp.raise_for_status()
        results = resp.json().get("results", [])
        if not results:
            break
        for r in results:
            res = r["resource"]
            # Skip filtered views / derived views so the same rows aren't counted twice
            if res.get("parent_fxf"):
                continue
            datasets.append(
                {
                    "id": res["id"],
                    "name": res["name"],
                    "columns": len(res.get("columns_field_name", [])),
                    "updated": res.get("data_updated_at") or res.get("updatedAt"),
                }
            )
        offset += PAGE_SIZE
    return datasets


def count_rows(ds):
    """Ask the SODA endpoint for count(*). Returns None if the dataset isn't queryable."""
    url = f"https://{DOMAIN}/resource/{ds['id']}.json"
    try:
        resp = session.get(url, params={"$select": "count(*) AS n"}, timeout=120)
        resp.raise_for_status()
        return int(resp.json()[0]["n"])
    except Exception as exc:  # non-tabular assets, permission errors, timeouts
        print(f"  skip {ds['id']} ({ds['name'][:40]}): {exc}", file=sys.stderr)
        return None


def to_markdown(top, total_checked):
    now = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M UTC")
    lines = [
        f"# Top {len(top)} Largest Datasets on {DOMAIN}",
        "",
        f"_Ranked by row count. {total_checked:,} datasets checked. Last updated {now}._",
        "",
        "| Rank | Dataset | ID | Rows | Columns | Data last updated |",
        "|---:|---|---|---:|---:|---|",
    ]
    for i, ds in enumerate(top, 1):
        updated = (ds["updated"] or "")[:10]
        link = f"https://{DOMAIN}/d/{ds['id']}"
        name = ds["name"].replace("|", "\\|")
        lines.append(
            f"| {i} | [{name}]({link}) | `{ds['id']}` | {ds['rows']:,} | {ds['columns']} | {updated} |"
        )
    return "\n".join(lines) + "\n"


def main():
    print(f"Listing datasets on {DOMAIN}...")
    datasets = list_datasets()
    print(f"Found {len(datasets)} base datasets. Counting rows...")

    with ThreadPoolExecutor(max_workers=MAX_WORKERS) as pool:
        futures = {pool.submit(count_rows, ds): ds for ds in datasets}
        for fut in as_completed(futures):
            futures[fut]["rows"] = fut.result()

    counted = [ds for ds in datasets if ds.get("rows") is not None]
    top = sorted(counted, key=lambda d: d["rows"], reverse=True)[:TOP_N]

    with open(OUTPUT_PATH, "w", encoding="utf-8") as f:
        f.write(to_markdown(top, len(counted)))
    print(f"Wrote {OUTPUT_PATH}")


if __name__ == "__main__":
    main()