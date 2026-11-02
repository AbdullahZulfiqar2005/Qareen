#!/usr/bin/env bash
set -euo pipefail
umask 077
root=$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")/.." && pwd)
mode=${1:---demo}
if [[ "$mode" != --demo && "$mode" != --ml ]]; then echo 'Usage: scripts/install.sh [--demo|--ml]' >&2; exit 2; fi
command -v go >/dev/null || { echo 'Install the Go version specified by go.mod first.' >&2; exit 1; }
command -v python3 >/dev/null
command -v systemctl >/dev/null
app="$HOME/.local/share/qareen/app"
mkdir -p "$app" "$HOME/.local/bin" "$HOME/.config/systemd/user" "$HOME/.config/qareen"
# Stop old units before replacing their code. Missing/inactive units are harmless.
systemctl --user stop qareen.service qareen-embedding.service 2>/dev/null || true
(cd "$root" && go build -trimpath -o "$HOME/.local/bin/qareen" .)
for file in embedding_server.py qareen_client.py qareen_embeddings.py qareen_dotenv.py chat.py gmail_setup.py hyprland_monitor.py embedding_script.py requirements.txt requirements-ml.txt requirements-gmail.txt requirements-wiki.txt; do cp "$root/$file" "$app/$file"; done
cp -R "$root/qareen_core" "$root/dashboard" "$app/"
python3 -m venv "$app/.venv"
req=requirements.txt
if [[ "$mode" == --ml ]]; then
  "$app/.venv/bin/python" -m pip install torch==2.9.1 --index-url https://download.pytorch.org/whl/cpu
  req=requirements-ml.txt
fi
"$app/.venv/bin/python" -m pip install -r "$app/$req"
for unit in qareen.service qareen-embedding.service; do cp "$root/services/$unit" "$HOME/.config/systemd/user/$unit"; done
mkdir -p "$HOME/.config/systemd/user/qareen-embedding.service.d"
if [[ "$mode" == --demo ]]; then
  cat > "$HOME/.config/systemd/user/qareen-embedding.service.d/mode.conf" <<'MODE'
[Service]
ExecStart=
ExecStart="%h/.local/share/qareen/app/.venv/bin/python" "%h/.local/share/qareen/app/embedding_server.py" --demo
MODE
else
  rm -f "$HOME/.config/systemd/user/qareen-embedding.service.d/mode.conf"
fi
if [[ ! -e "$HOME/.config/qareen/environment" ]]; then
  printf '%s\n' '# Optional: GROQ_API_KEY=... (cloud sharing is separately opt-in)' > "$HOME/.config/qareen/environment"
fi
systemctl --user daemon-reload
printf '%s\n' 'Installed. Review ~/.config/qareen/environment, then run:' '  ~/.local/bin/qareen start' '  ~/.local/bin/qareen token admin' 'Dashboard: http://127.0.0.1:2846/' 'Collectors begin when you run qareen start. Demo vectors are not semantic embeddings.'
