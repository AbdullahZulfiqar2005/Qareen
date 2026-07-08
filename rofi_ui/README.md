# Qareen Rofi UI

A keybind-triggered prompt bar for Hyprland: type a question, get an answer
from your digital twin, without leaving your current window for a terminal.

## How it works

```
keybind -> qareen-rofi.sh
             |
             1. rofi -dmenu   (prompt bar, curved theme)
             2. rofi -e "thinking..."   (shown while step 3 runs)
             3. qareen_ask.py <prompt>
                   -> subprocess: qareen query "<prompt>"
                        -> embeds the query, does similarity search over
                           ~/.qareen.db, falls back to the local Arch Wiki
                           index, asks Groq, and logs the Q&A back into
                           ~/.qareen.db as a new memory event
                   -> strips ANSI + the timeline preamble, keeps just the answer
             4. rofi -e "<answer>"
```

Reusing `qareen query` (rather than re-implementing the retrieval/LLM logic
in Python) means the rofi UI's memory is exactly the daemon's memory - every
question you ask through it becomes part of your history too.

## Requirements

- `qareen start` already running (embedding server + tracking daemon)
- `rofi` installed (`rofi-wayland` fork recommended on Hyprland)
- `GROQ_API_KEY` available, either exported in your shell or in a `.env`
  file at `~/.config/qareen/.env`:
  ```
  GROQ_API_KEY="your-key-here"
  ```
- A [Nerd Font](https://www.nerdfonts.com/) installed if you want the icon
  glyphs in the theme (`qareen-theme.rasi` uses "JetBrainsMono Nerd Font" -
  change this to any font you have if you don't want to install one).

## Setup

1. Add the keybind (edit the path if you cloned the repo somewhere other
   than `~/Qareen`) - see `hyprland-snippet.conf` for the exact lines to
   paste into `~/.config/hypr/hyprland.conf`:
   ```
   bind = SUPER, A, exec, ~/Qareen/rofi_ui/qareen-rofi.sh
   ```
2. `hyprctl reload`
3. Press `SUPER+A`, type a question, hit Enter.

## Customizing the look

Everything visual lives in `qareen-theme.rasi` - colors, corner radius
(`border-radius`), width, font, and padding are all plain variables/values
near the top of the file. The Hyprland side (`hyprland-snippet.conf`)
controls the open/close animation and background blur, since rofi doesn't
animate its own window.

## Troubleshooting

- **Nothing happens on keybind press** - run `~/Qareen/rofi_ui/qareen-rofi.sh`
  directly from a terminal to see errors.
- **"Couldn't find the `qareen` binary on $PATH"** - build it
  (`go build -o qareen .` in the repo root) and either put it on `$PATH`
  or leave it in the repo root; `qareen_ask.py` will find it either way.
- **"GROQ_API_KEY is not set"** - see Requirements above.
- **Answer looks cut off / stale** - make sure `qareen start` is running so
  the embedding server can actually be reached; `qareen query "test"` from
  a terminal will surface the same error more verbosely.
