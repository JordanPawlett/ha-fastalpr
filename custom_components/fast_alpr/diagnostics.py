"""Downloadable diagnostics without images, registrations, or backend addresses."""
from copy import deepcopy


async def async_get_config_entry_diagnostics(hass, entry):
    runtime = entry.runtime_data
    return {
        "last_scan": deepcopy(runtime.last_scan_report),
        "processed_batches": runtime.processed_batches,
        "last_audit_error": runtime.last_audit_error,
    }
