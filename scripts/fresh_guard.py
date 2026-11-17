"""Tell a scheduled GitHub Actions run to skip itself when its data is already fresh.

GitHub's cron has been starting these jobs 6-10 hours late, so an on-time trigger
(the job-scheduler Worker in scheduler/) does the real run and GitHub's delayed
backup run should do nothing. Usage, as the first step after checkout:

    python3 scripts/fresh_guard.py <json-file> <timestamp-field> <max-age-hours>

Reads a "YYYY-MM-DD HH:MM UTC" timestamp from the file and writes skip=true|false
to $GITHUB_OUTPUT. Any problem reading the file means "run" — it never skips by
accident.
"""
import datetime as dt
import json
import os
import sys

path, field, max_hours = sys.argv[1], sys.argv[2], float(sys.argv[3])
try:
    with open(path, encoding="utf-8") as fh:
        stamp = json.load(fh).get(field, "")
    then = dt.datetime.strptime(stamp, "%Y-%m-%d %H:%M UTC").replace(tzinfo=dt.timezone.utc)
    age = (dt.datetime.now(dt.timezone.utc) - then).total_seconds() / 3600
except Exception as exc:  # missing file, unexpected format... -> run
    print(f"Could not read {field!r} from {path} ({exc}); running.")
    age = float("inf")

skip = age < max_hours
print(f"{path}: last refreshed {age:.1f} h ago -> {'skip' if skip else 'run'} (limit {max_hours:g} h)")
out = os.environ.get("GITHUB_OUTPUT")
if out:
    with open(out, "a", encoding="utf-8") as fh:
        fh.write(f"skip={'true' if skip else 'false'}\n")
