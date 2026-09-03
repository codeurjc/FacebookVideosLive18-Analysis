#!/usr/bin/env python3
"""
cleanup_instance_event.py

Remove any CSV row that immediately follows another row where the
`instance_event` column equals 2. Sequences of consecutive 2s are
collapsed to a single row with value 2.

Usage:
  python scripts/cleanup_instance_event.py file1.csv [file2.csv ...]
  cat file.csv | python scripts/cleanup_instance_event.py > cleaned.csv

The script edits files in-place. When reading from stdin it writes to stdout.
Reports the number of removed rows to stderr as REMOVED_ROWS=N and prints
"processed: <file>" to stderr for file operations.
"""
from __future__ import annotations

import argparse
import csv
import os
import sys
import tempfile
import shutil
import stat
from typing import Iterable, List


def norm_val(v: str | None) -> str | None:
    if v is None:
        return None
    v = v.strip()
    if v == "":
        return ""
    try:
        f = float(v)
        if int(f) == f:
            return str(int(f))
        return str(f)
    except Exception:
        return v


def process_rows(rows: Iterable[List[str]]) -> Iterable[List[str]]:
    """Yield rows with consecutive instance_event==2 collapsed.

    The first row must be the header (yielded as-is).
    """
    it = iter(rows)
    try:
        header = next(it)
    except StopIteration:
        return
        yield  # pragma: no cover

    yield header
    if 'instance_event' not in header:
        raise ValueError('header does not contain instance_event')

    idx = header.index('instance_event')
    prev_is_two = False
    for row in it:
        val = row[idx] if idx < len(row) else ''
        is_two = (norm_val(val) == '2')
        if prev_is_two and is_two:
            # skip this row
            continue
        yield row
        prev_is_two = is_two


def process_file_inplace(path: str) -> int:
    """Process a file in-place. Returns number of removed rows."""
    removed = 0
    # read all rows first (memory proportional to file size). For huge files
    # you may want a streaming approach that writes to a temp file while
    # counting removed rows; here we do streaming to a temp file.
    dirpath = os.path.dirname(os.path.abspath(path)) or '.'
    fd, tmppath = tempfile.mkstemp(prefix='.cleanup-', dir=dirpath)
    os.close(fd)
    try:
        with open(path, newline='', encoding='utf-8') as inf, open(tmppath, 'w', newline='', encoding='utf-8') as outf:
            reader = csv.reader(inf)
            writer = csv.writer(outf)

            try:
                header = next(reader)
            except StopIteration:
                # empty file
                writer.writerows([])
                os.replace(tmppath, path)
                return 0

            if 'instance_event' not in header:
                os.remove(tmppath)
                raise ValueError('header does not contain instance_event')

            idx = header.index('instance_event')
            writer.writerow(header)
            prev_is_two = False
            for row in reader:
                val = row[idx] if idx < len(row) else ''
                is_two = (norm_val(val) == '2')
                if prev_is_two and is_two:
                    removed += 1
                    continue
                writer.writerow(row)
                prev_is_two = is_two

        # atomic replace with retries to handle Windows permission issues
        try:
            safe_replace(tmppath, path)
        except Exception:
            # ensure temp file removed if replace failed
            try:
                if os.path.exists(tmppath):
                    os.remove(tmppath)
            except Exception:
                pass
            raise
        return removed
    except Exception:
        # cleanup temp file on error
        try:
            if os.path.exists(tmppath):
                os.remove(tmppath)
        except Exception:
            pass
        raise


def safe_replace(src: str, dst: str) -> None:
    """Replace dst with src atomically, with Windows-friendly retries.

    Attempts os.replace(src, dst). On PermissionError (commonly WinError 5)
    it will try to make dst writable and retry. If that fails it will try to
    remove dst and then move src into place.
    """
    try:
        os.replace(src, dst)
        return
    except PermissionError as e:
        # Try to make destination writable and retry
        if os.path.exists(dst):
            try:
                os.chmod(dst, stat.S_IWRITE)
            except Exception:
                # best-effort; continue to next strategy
                pass
            try:
                os.replace(src, dst)
                return
            except PermissionError:
                # try removing destination then move
                try:
                    os.remove(dst)
                except Exception as rm_err:
                    raise PermissionError(f'Failed to replace {dst}: {e}; also failed to remove {dst}: {rm_err}')
                # now move
                try:
                    shutil.move(src, dst)
                    return
                except Exception as mv_err:
                    raise OSError(f'Failed to move {src} to {dst} after removing target: {mv_err}')
        # if destination doesn't exist, try move
        try:
            shutil.move(src, dst)
            return
        except Exception:
            raise


def process_stdin_stdout() -> int:
    """Read CSV from stdin, write cleaned CSV to stdout. Returns removed count."""
    reader = csv.reader(sys.stdin)
    writer = csv.writer(sys.stdout, lineterminator=os.linesep)
    try:
        header = next(reader)
    except StopIteration:
        return 0

    if 'instance_event' not in header:
        raise ValueError('header does not contain instance_event')
    idx = header.index('instance_event')
    writer.writerow(header)
    prev_is_two = False
    removed = 0
    for row in reader:
        val = row[idx] if idx < len(row) else ''
        is_two = (norm_val(val) == '2')
        if prev_is_two and is_two:
            removed += 1
            continue
        writer.writerow(row)
        prev_is_two = is_two
    return removed


def main(argv: list[str] | None = None) -> int:
    argv = argv if argv is not None else sys.argv[1:]
    parser = argparse.ArgumentParser(description='Cleanup consecutive instance_event==2 rows in CSV files')
    parser.add_argument('files', nargs='*', help='CSV files to process')
    parser.add_argument('--simulated', '-s', action='store_true',
                        help='Process all CSV files in the top-level "simulated" directory (non-recursive).')
    args = parser.parse_args(argv)

    files_to_process: list[str] = list(args.files)

    # If --simulated is specified, collect CSV files from ./simulated and add them
    if args.simulated:
        simulated_dir = os.path.join(os.getcwd(), 'simulated')
        if not os.path.isdir(simulated_dir):
            print(f'ERROR: simulated directory not found at {simulated_dir}', file=sys.stderr)
            return 2
        csvs = sorted(
            os.path.join(simulated_dir, fn)
            for fn in os.listdir(simulated_dir)
            if os.path.isfile(os.path.join(simulated_dir, fn)) and fn.lower().endswith('.csv')
        )
        if not csvs:
            print(f'WARNING: no .csv files found in {simulated_dir}', file=sys.stderr)
        # merge, but avoid duplicates
        for p in csvs:
            if p not in files_to_process:
                files_to_process.append(p)

    if not files_to_process:
        # default to stdin/stdout when no files provided and --simulated not used
        try:
            removed = process_stdin_stdout()
            print(f'REMOVED_ROWS={removed}', file=sys.stderr)
            return 0
        except ValueError as e:
            print(f'ERROR: {e}', file=sys.stderr)
            return 2

    exit_code = 0
    for fn in files_to_process:
        if not os.path.isfile(fn):
            print(f'skipping: {fn} (not a file)', file=sys.stderr)
            continue
        try:
            removed = process_file_inplace(fn)
            print(f'processed: {fn}', file=sys.stderr)
            print(f'REMOVED_ROWS={removed}', file=sys.stderr)
        except ValueError as e:
            print(f'ERROR: {e}', file=sys.stderr)
            exit_code = 2
        except Exception as e:
            print(f'ERROR processing {fn}: {e}', file=sys.stderr)
            exit_code = 3

    return exit_code


if __name__ == '__main__':
    raise SystemExit(main())
