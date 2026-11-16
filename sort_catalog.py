#!/usr/bin/env python3
"""
Gundam Manga - Catalog Sorter.

Sorts data/gundam_manga.json by series, then volume number, preserving
every field on every entry (whole-dict round-trip). The frontend re-sorts
for display; this order is the canonical file order for humans editing
the JSON and for clean diffs.

Usage:
    python sort_catalog.py            # sort in place, print the catalog
    python sort_catalog.py --check    # exit 1 if the file is not sorted; write nothing
"""

import argparse
import json
import os
import sys

DATA_FILE = os.path.join(os.path.dirname(__file__), "data", "gundam_manga.json")


def load_data():
    if not os.path.exists(DATA_FILE):
        return []
    with open(DATA_FILE, "r", encoding="utf-8") as f:
        return json.load(f)


def sort_key(item):
    volume = item.get("volume")
    return (str(item.get("seriesId", "")), volume if isinstance(volume, int) else 0)


def list_volumes(data):
    print(f"\n=== GUNDAM MANGA (Sorted: Series, then Volume) ===")
    print(f"Total Volumes: {len(data)}\n")
    print(f"{'Series':<28} | {'Vol':<4} | {'Status':<9} | {'Release':<11} | {'Title'}")
    print("-" * 85)
    for item in data:
        vol = item.get("volume")
        print(f"{str(item.get('seriesId', '')):<28} | {str(vol) if vol is not None else '-':<4} | "
              f"{str(item.get('status', '')):<9} | {str(item.get('releaseDate', '')):<11} | {item.get('title')}")


def main():
    parser = argparse.ArgumentParser(description="Sort the Gundam Manga catalog by series, then volume.")
    parser.add_argument("--check", action="store_true", help="Check sort order without writing.")
    args = parser.parse_args()

    data = load_data()
    ordered = sorted(data, key=sort_key)
    if [x.get("id") for x in ordered] != [x.get("id") for x in data]:
        if args.check:
            print("catalog is NOT sorted by (series, volume).")
            return 1
        with open(DATA_FILE, "w", encoding="utf-8") as f:
            json.dump(ordered, f, indent=2, ensure_ascii=False)
        print(f"Saved {len(ordered)} volumes to {DATA_FILE} (sorted by series, then volume).")
        data = ordered
    else:
        print("catalog already sorted by (series, volume).")
    list_volumes(data)
    return 0


if __name__ == "__main__":
    sys.exit(main())
