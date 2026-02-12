"""Tests for the state module."""

import pytest

from simplyprint_ws_client.core.state import PrinterStatus

from meltingplot.duet_simplyprint_connector.state import map_duet_state_to_printer_status


@pytest.mark.parametrize(
    'status,expected',
    [
        ('disconnected', PrinterStatus.OFFLINE),
        ('starting', PrinterStatus.NOT_READY),
        ('updating', PrinterStatus.NOT_READY),
        ('off', PrinterStatus.OFFLINE),
        ('halted', PrinterStatus.ERROR),
        ('pausing', PrinterStatus.PAUSING),
        ('paused', PrinterStatus.PAUSED),
        ('resuming', PrinterStatus.RESUMING),
        ('cancelling', PrinterStatus.CANCELLING),
        ('processing', PrinterStatus.PRINTING),
        ('simulating', PrinterStatus.OPERATIONAL),
        ('busy', PrinterStatus.OPERATIONAL),
        ('changingTool', PrinterStatus.OPERATIONAL),
        ('idle', PrinterStatus.OPERATIONAL),
    ],
)
def test_map_duet_state_not_printing(status, expected):
    """Test mapping when is_printing=False."""
    om = {'state': {'status': status}}
    assert map_duet_state_to_printer_status(om, is_printing=False) == expected


@pytest.mark.parametrize(
    'status,expected',
    [
        ('disconnected', PrinterStatus.OFFLINE),
        ('starting', PrinterStatus.NOT_READY),
        ('updating', PrinterStatus.NOT_READY),
        ('off', PrinterStatus.OFFLINE),
        ('halted', PrinterStatus.ERROR),
        ('pausing', PrinterStatus.PAUSING),
        ('paused', PrinterStatus.PAUSED),
        ('resuming', PrinterStatus.RESUMING),
        ('cancelling', PrinterStatus.CANCELLING),
        ('processing', PrinterStatus.PRINTING),
        ('simulating', PrinterStatus.NOT_READY),
        ('busy', PrinterStatus.PRINTING),
        ('changingTool', PrinterStatus.PRINTING),
        ('idle', PrinterStatus.OPERATIONAL),
    ],
)
def test_map_duet_state_while_printing(status, expected):
    """Test mapping when is_printing=True."""
    om = {'state': {'status': status}}
    assert map_duet_state_to_printer_status(om, is_printing=True) == expected


def test_map_duet_state_missing_status():
    """Test that missing status defaults to OFFLINE."""
    assert map_duet_state_to_printer_status({}) == PrinterStatus.OFFLINE


def test_map_duet_state_empty_state():
    """Test that empty state dict defaults to OFFLINE."""
    assert map_duet_state_to_printer_status({'state': {}}) == PrinterStatus.OFFLINE


def test_map_duet_state_unknown_status():
    """Test that an unknown status defaults to OFFLINE."""
    om = {'state': {'status': 'nonexistent_state'}}
    assert map_duet_state_to_printer_status(om) == PrinterStatus.OFFLINE
