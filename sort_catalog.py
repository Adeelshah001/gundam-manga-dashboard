#!/usr/bin/env python3
"""
Gundam Media Hub - Catalog Sorter.

Sorts the three hub catalogs, preserving every field on every entry
(whole-dict round-trip). The frontend re-sorts for display; this order
is the canonical file order for humans editing the JSON and for clean
diffs.

    data/gundam_manga.json  by series, then volume number
    data/gundam_games.json  by title
    data/gundam_discs.json  by format (Streaming, Blu-ray, DVD, 4K Ultra HD), then title

Usage:
    python sort_catalog.py            # sort in place, print the catalogs
    python sort_catalog.py --check    # exit 1 if any file is not sorted; write nothing
"""

import argparse
import json
import os
import sys

DATA_DIR = os.path.join(os.path.dirname(__file__), "data")

FORMAT_ORDER = {"Streaming": 0, "Blu-ray": 1, "DVD": 2, "4K Ultra HD": 3}


def manga_key(item):
    volume = item.get("volume")
    return (str(item.get("seriesId", "")), volume if isinstance(volume, int) else 0)


def games_key(item):
    return str(item.get("title", "")).lower()


def discs_key(item):
    return (FORMAT_ORDER.get(item.get("format"), 9), str(item.get("title", "")).lower())


CATALOGS = {
    "manga": ("gundam_manga.json", manga_key, "series, then volume"),
    "games": ("gundam_games.json", games_key, "title"),
    "discs": ("gundam_discs.json", discs_key, "format, then title"),
}


def load_data(filename):
    path = os.path.join(DATA_DIR, filename)
    if not os.path.exists(path):
        return []
    with open(path, "r", encoding="utf-8") as f:
        return json.load(f)


def list_manga(data):
    print(f"\n=== GUNDAM MANGA (Sorted: Series, then Volume) ===")
    print(f"Total Volumes: {len(data)}\n")
    print(f"{'Series':<28} | {'Vol':<4} | {'Status':<9} | {'Release':<11} | {'Title'}")
    print("-" * 85)
    for item in data:
        vol = item.get("volume")
        print(f"{str(item.get('seriesId', '')):<28} | {str(vol) if vol is not None else '-':<4} | "
              f"{str(item.get('status', '')):<9} | {str(item.get('releaseDate', '')):<11} | {item.get('title')}")


def list_games(data):
    print(f"\n=== GUNDAM GAMES (Sorted: Title) ===")
    print(f"Total Games: {len(data)}\n")
    print(f"{'Status':<9} | {'Release':<11} | {'Title'}")
    print("-" * 85)
    for item in data:
        print(f"{str(item.get('status', '')):<9} | {str(item.get('releaseDate', '') or 'TBD'):<11} | {item.get('title')}")


def list_discs(data):
    print(f"\n=== GUNDAM DISCS (Sorted: Format, then Title) ===")
    print(f"Total Discs: {len(data)}\n")
    print(f"{'Format':<12} | {'Status':<9} | {'Release':<11} | {'Title'}")
    print("-" * 85)
    for item in data:
        print(f"{str(item.get('format', '')):<12} | {str(item.get('status', '')):<9} | "
              f"{str(item.get('releaseDate', '')):<11} | {item.get('title')}")


LISTERS = {"manga": list_manga, "games": list_games, "discs": list_discs}


def main():
    parser = argparse.ArgumentParser(description="Sort the Gundam Media Hub catalogs.")
    parser.add_argument("--check", action="store_true", help="Check sort order without writing.")
    args = parser.parse_args()

    exit_code = 0
    for name, (filename, key, order_desc) in CATALOGS.items():
        path = os.path.join(DATA_DIR, filename)
        data = load_data(filename)
        ordered = sorted(data, key=key)
        if [x.get("id") for x in ordered] != [x.get("id") for x in data]:
            if args.check:
                print(f"{filename} is NOT sorted by ({order_desc}).")
                exit_code = 1
            else:
                with open(path, "w", encoding="utf-8") as f:
                    json.dump(ordered, f, indent=2, ensure_ascii=False)
                print(f"Saved {len(ordered)} entries to {path} (sorted by {order_desc}).")
                data = ordered
        else:
            print(f"{filename} already sorted by ({order_desc}).")
        LISTERS[name](data)
    return exit_code


if __name__ == "__main__":
    sys.exit(main())
