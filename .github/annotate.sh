#!/usr/bin/env bash
# Republishes a failed step's tail as a workflow annotation, which the REST API serves to
# anyone. Downloading a run's logs needs admin rights on the repository.
title="$1"; file="$2"
python - "$title" "$file" <<'PY'
import sys
title, path = sys.argv[1], sys.argv[2]
text = open(path, encoding="utf-8", errors="replace").read()[-7000:]
esc = text.replace("%", "%25").replace("\r", "%0D").replace("\n", "%0A")
print(f"::error title={title}::{esc}")
PY
