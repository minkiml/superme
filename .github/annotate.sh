#!/usr/bin/env bash
# Republishes a failed step's tail as a workflow annotation, which the REST API serves to
# anyone. Downloading a run's logs needs admin rights on the repository.
title="$1"; file="$2"
python - "$title" "$file" <<'PY'
import sys
# A Windows console encodes stdout as cp1252, and every gate prints box glyphs.
sys.stdout.reconfigure(encoding="utf-8", errors="replace")
title, path = sys.argv[1], sys.argv[2]
raw = open(path, encoding="utf-8", errors="replace").read()
# Buffering can bury a traceback mid-log, so report from it rather than from either end.
cut = raw.rfind("Traceback (most recent call last)")
text = raw[cut:cut + 6000] + "\n[…]\n" + raw[-2000:] if cut >= 0 else raw[-8000:]
esc = text.replace("%", "%25").replace("\r", "%0D").replace("\n", "%0A")
print(f"::error title={title}::{esc}")
PY

# The reporter itself must never be the thing that fails silently.
if [ $? -ne 0 ]; then
    tail -c 4000 "$file" | tr -cd '\11\12\40-\176' | sed 's/%/%25/g' \
        | awk -v t="$title" 'BEGIN{printf "::error title=%s (ascii fallback)::", t}
                             {printf "%s%%0A", $0} END{print ""}'
fi
