"""Measure warm search and autocomplete calls over the internal HTTP interface."""

import argparse
import json
from math import ceil
from statistics import median
from time import perf_counter
from urllib.parse import urlencode, urlsplit
from urllib.request import Request, urlopen


def request(base, path, payload=None):
    headers = {"Content-Type": "application/json"} if payload is not None else {}
    data = json.dumps(payload).encode("utf-8") if payload is not None else None
    with urlopen(Request(base + path, headers=headers, data=data), timeout=5) as response:
        return json.load(response)


def measure(call, samples):
    result = call()
    times = []
    for _ in range(samples):
        start = perf_counter()
        result = call()
        times.append((perf_counter() - start) * 1000)
    return result, {
        "samples": samples, "p50_ms": round(median(times), 2),
        "p95_ms": round(sorted(times)[ceil(0.95 * samples) - 1], 2),
    }


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--base", default="http://127.0.0.1:8001")
    parser.add_argument("--prefix", required=True)
    parser.add_argument("--query", required=True)
    parser.add_argument("--state")
    parser.add_argument("--samples", type=int, default=30)
    args = parser.parse_args()
    base = args.base.rstrip("/")
    url = urlsplit(base)
    if (url.scheme != "http" or url.hostname not in {"127.0.0.1", "::1"}
            or url.path or url.query or url.fragment or url.username or not 1 <= args.samples <= 1000):
        parser.error("Use a loopback HTTP service and a sample count from 1 to 1000")
    payload = {"query": args.query, "filters": {"state": args.state}}
    search, search_stats = measure(lambda: request(base, "/internal/search", payload), args.samples)
    suggestions, suggest_stats = measure(
        lambda: request(base, "/internal/autocomplete?" + urlencode({"prefix": args.prefix})), args.samples,
    )
    print(json.dumps({
        "generation": search["generation"], "query": args.query, "state": args.state,
        "search": {**search_stats, "total": search["total"]},
        "autocomplete": {**suggest_stats, "prefix": args.prefix, **suggestions},
    }, indent=2))


if __name__ == "__main__":
    main()
