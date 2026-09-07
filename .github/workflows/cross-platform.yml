name: Cross-Platform Test

on:
  push:
    branches: [main]
  pull_request:
    branches: [main]

jobs:
  test:
    strategy:
      matrix:
        os: [windows-latest, macos-latest]

    runs-on: ${{ matrix.os }}

    steps:
      - name: Checkout repository
        uses: actions/checkout@v4

      - name: Set up Python
        uses: actions/setup-python@v5
        with:
          python-version: "3.11"

      - name: Install dependencies
        run: |
          python -m pip install --upgrade pip
          pip install -e .

      - name: Check Python files
        run: |
          python -m compileall facemash

      - name: Check platform application
        run: |
          python -c "import platform; print('Testing on:', platform.system())"
