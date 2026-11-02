#!/usr/bin/env bash
set -euo pipefail
cd -- "$(dirname -- "${BASH_SOURCE[0]}")"
rm -f qareen.xpi
(cd extension && zip -q ../qareen.xpi manifest.json core.js background.js content.js options.html options.js)
echo 'Created qareen.xpi. For testing load extension/manifest.json through about:debugging. Permanent Firefox installation requires Mozilla signing.'
