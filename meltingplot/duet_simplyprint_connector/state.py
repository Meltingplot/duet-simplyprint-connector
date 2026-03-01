"""State Module for Duet SimplyPrint Connector."""

from simplyprint_ws_client.core.state import PrinterStatus

duet_state_simplyprint_status_mapping = {
    'disconnected': PrinterStatus.OFFLINE,
    'starting': PrinterStatus.NOT_READY,
    'updating': PrinterStatus.NOT_READY,
    'off': PrinterStatus.OFFLINE,
    'halted': PrinterStatus.ERROR,
    'pausing': PrinterStatus.PAUSING,
    'paused': PrinterStatus.PAUSED,
    'resuming': PrinterStatus.RESUMING,
    'cancelling': PrinterStatus.CANCELLING,
    'processing': PrinterStatus.PRINTING,
    'simulating': PrinterStatus.OPERATIONAL,
    'busy': PrinterStatus.OPERATIONAL,
    'changingTool': PrinterStatus.OPERATIONAL,
    'idle': PrinterStatus.OPERATIONAL,
}

duet_state_simplyprint_status_while_printing_mapping = {
    'disconnected': PrinterStatus.OFFLINE,
    'starting': PrinterStatus.NOT_READY,
    'updating': PrinterStatus.NOT_READY,
    'off': PrinterStatus.OFFLINE,
    'halted': PrinterStatus.ERROR,
    'pausing': PrinterStatus.PAUSING,
    'paused': PrinterStatus.PAUSED,
    'resuming': PrinterStatus.RESUMING,
    'cancelling': PrinterStatus.CANCELLING,
    'processing': PrinterStatus.PRINTING,
    'simulating': PrinterStatus.NOT_READY,
    'busy': PrinterStatus.PRINTING,
    'changingTool': PrinterStatus.PRINTING,
    'idle': PrinterStatus.OPERATIONAL,
}


def map_duet_state_to_printer_status(object_model: dict, is_printing: bool = False) -> 'PrinterStatus':
    """Map the Duet state to the printer status."""
    printer_state = object_model.get('state', {}).get('status', 'disconnected')

    status_mapping = (
        duet_state_simplyprint_status_while_printing_mapping if is_printing else duet_state_simplyprint_status_mapping
    )

    return status_mapping.get(printer_state, PrinterStatus.OFFLINE)
