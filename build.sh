#!/bin/sh
set -e

cd "$(dirname "$0")"

echo "Creating temporary build environment..."
python3 -m venv .buildenv
. .buildenv/bin/activate

echo "Installing dependencies..."
pip install --quiet numpy pyinstaller

echo "Building binary..."
pyinstaller --onefile --name barscope --clean --noconfirm main.py 2>&1 | tail -3

echo "Installing to ~/.local/bin..."
mkdir -p ~/.local/bin
cp dist/barscope ~/.local/bin/barscope

echo "Cleaning up..."
rm -rf .buildenv build dist barscope.spec __pycache__ venv

echo "Done. Run with: barscope"
