import urwid
import os

from .tui import TUI
from .config import Config

# Support /etc, prefer ~/.config
CONFIG_LOCATIONS = [
    os.path.expanduser("~/.config/hatui-config.yaml"),
    "/etc/hatui-config.yaml",
    os.path.abspath("hatui-config.yaml"),
]
for path in CONFIG_LOCATIONS:
    if os.path.exists(path):
        CONFIG_PATH = path
        break
else:
    # We're gonna start writing the shit out of this file, so
    # we need to make sure it's somewhere writable for the user.
    # Default to $HOME/.config/hatui-config.yaml if not found
    # Gee whiz, I sure hope they have a ~/.config directory.
    # Isn't that like an Honorary POSIX Standard by now?
    CONFIG_PATH = os.path.expanduser("~/.config/hatui-config.yaml")

def main():
    config = Config(CONFIG_PATH)
    tui = TUI(config)
    tui.run()

if __name__ == "__main__":
    main()
