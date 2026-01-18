# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Project Overview

This is a bridge/connector between Duet-based 3D printers (RepRapFirmware) and SimplyPrint.io cloud service. It enables cloud printing, monitoring, and control of Duet firmware-based 3D printers via the Duet HTTP API and SimplyPrint WebSocket protocol.

## Common Commands

```bash
# Run all tests with coverage
./pytest-module.sh
# Or directly:
pytest --cov-config .coveragerc --cov meltingplot tests/ -vv

# Run a single test file
pytest tests/test_virtual_client.py -vv

# Run a specific test
pytest tests/test_watchdog.py::test_watchdog_basic_reset_and_stop -vv

# Lint with flake8
./flake8-module.sh

# Auto-format code with yapf
./yapf-module.sh

# Check formatting without modifying (diff only)
yapf --style .style.yapf -r --diff meltingplot/

# Build package
./create-package.sh

# Run the application
simplyprint start
# Or: python -m meltingplot.duet_simplyprint_connector start
```

## Code Style

- Max line length: 120 characters (flake8) / 119 (yapf)
- Formatter: yapf with `.style.yapf` config
- Linter: flake8 with extensions (docstrings, bugbear, import-order, etc.)
- Max complexity: 10

## Architecture

### Key Components

- **VirtualClient** (`virtual_client.py`): Main integration point extending `DefaultClient` from simplyprint-ws-client. Bridges Duet API and SimplyPrint WebSocket protocol.
- **DuetPrinter** (`duet/model.py`): Manages Duet connection state with event emission via `pyee.asyncio.AsyncIOEventEmitter`.
- **RepRapFirmware** (`duet/api.py`): HTTP API wrapper for Duet with retry/reauthentication logic.
- **Watchdog** (`watchdog.py`): Dedicated thread monitoring for deadlocks with configurable timeout.

### Patterns

- **Event-driven**: Uses `pyee.asyncio.AsyncIOEventEmitter` for state changes and objectmodel updates
- **Retry decorator**: `@reauthenticate` on API calls handles 401/502/503 with exponential backoff
- **Async decorators**: `@async_task` and `@async_supress` manage task lifecycle and error handling
- **State mapping**: `state.py` maps Duet states to SimplyPrint status enums

### Data Flow

1. VirtualClient connects to SimplyPrint.io via WebSocket
2. RepRapFirmware polls Duet printer via HTTP API
3. DuetPrinter emits events on state changes
4. VirtualClient translates and forwards state/commands between systems

## Testing

- Framework: pytest with pytest-asyncio for async tests
- Mocking: pytest-mock with AsyncMock for async functions
- Use `@pytest.mark.asyncio` decorator for async test functions

## Configuration

- Config file: `~/.config/SimplyPrint/DuetConnector.json`
- Printer cookie: `0:/sys/simplyprint-connector.json` (stored on printer for identification)
- Default Duet password: `reprap`
