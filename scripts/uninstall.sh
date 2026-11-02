#!/usr/bin/env bash
set -euo pipefail
systemctl --user disable --now qareen.service qareen-embedding.service 2>/dev/null || true
rm -f "$HOME/.config/systemd/user/qareen.service" "$HOME/.config/systemd/user/qareen-embedding.service" "$HOME/.local/bin/qareen"
rm -rf "$HOME/.config/systemd/user/qareen-embedding.service.d" "$HOME/.local/share/qareen/app"
systemctl --user daemon-reload
printf '%s\n' 'Application removed. Your database, settings, tokens, and OAuth credentials remain. See docs/PRIVACY.md to erase them deliberately.'
