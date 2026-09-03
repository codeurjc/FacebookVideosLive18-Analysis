#!/usr/bin/env python3
"""Index a tar once: emit one JSON line per member with its byte offset and size.

Scanning the headers of a 386 GB tar takes a couple of minutes because tar seeks
over the payloads; doing it once lets every reduction worker jump straight to its
member instead of rescanning.
"""
import json
import sys
import tarfile

tar_path = sys.argv[1]
out_path = sys.argv[2]

with tarfile.open(tar_path, "r|") as tf, open(out_path, "w") as out:
    for m in tf:
        if not m.isfile():
            continue
        out.write(json.dumps({
            "name": m.name,
            "offset": m.offset_data,
            "size": m.size,
        }) + "\n")
print(f"indexed {tar_path} -> {out_path}", file=sys.stderr)
