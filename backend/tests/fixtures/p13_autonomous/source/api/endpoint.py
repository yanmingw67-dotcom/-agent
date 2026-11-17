from core.h_engine import process_batch
from core.i_preview import preview_unique


def handle_events(mode, events, store, settings):
    if mode == "preview":
        return preview_unique(events)
    if mode != "commit":
        raise ValueError("unknown mode")
    return process_batch(events, store, settings)
