#!/usr/bin/env bash
# Set up crapper with Lua 5.4 on Ubuntu (including Ubuntu under WSL2).
set -euo pipefail
cd "$(dirname "$0")/.."

sudo DEBIAN_FRONTEND=noninteractive apt-get update
sudo DEBIAN_FRONTEND=noninteractive apt-get install -y python3 python3-venv build-essential \
  lua5.4 liblua5.4-dev luarocks ca-certificates curl tar

sudo luarocks --lua-version=5.4 install busted
sudo luarocks --lua-version=5.4 install luacov
sudo luarocks --lua-version=5.4 install cluacov 1.0.0-1

# Lua searches /usr/local before ./?.lua, and ~/.luarocks after it. Coverage-gate
# tests stub ./luacov/reporter/lcov.lua and that stub must win. A system copy
# would shadow it, so the reporter lives only in the user tree.
sudo luarocks --lua-version=5.4 remove --force --deps-mode none luacov-reporter-lcov || true
sudo rm -f /usr/local/share/lua/5.4/luacov/reporter/lcov.lua
luarocks --lua-version=5.4 --tree "${HOME}/.luarocks" install --deps-mode none luacov-reporter-lcov

python3 -m venv .venv
.venv/bin/pip install -e '.[dev]'

if ! command -v bb >/dev/null 2>&1; then
  tmp=$(mktemp -d)
  url="https://github.com/babashka/babashka/releases/download/v1.13.228/babashka-1.13.228-linux-amd64-static.tar.gz"
  curl -fsSL -o "$tmp/bb.tgz" "$url"
  echo "cf643eb2bd1064fe3cbeab9751d59c2877094516ce692e89893fce56ec98ab00  $tmp/bb.tgz" | sha256sum -c -
  tar -xzf "$tmp/bb.tgz" -C "$tmp" bb
  sudo install -m 755 "$tmp/bb" /usr/local/bin/bb
  rm -rf "$tmp"
fi

echo "Done. Run: .venv/bin/pytest"
