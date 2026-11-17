from storage.durable import reserve_once

# compatibility note 1: plain padding, not business behavior
# compatibility note 2: plain padding, not business behavior
# compatibility note 3: plain padding, not business behavior
# compatibility note 4: plain padding, not business behavior
# compatibility note 5: plain padding, not business behavior
# compatibility note 6: plain padding, not business behavior
# compatibility note 7: plain padding, not business behavior
# compatibility note 8: plain padding, not business behavior
# compatibility note 9: plain padding, not business behavior
# compatibility note 10: plain padding, not business behavior
# compatibility note 11: plain padding, not business behavior
# compatibility note 12: plain padding, not business behavior
# compatibility note 13: plain padding, not business behavior
# compatibility note 14: plain padding, not business behavior
# compatibility note 15: plain padding, not business behavior
# compatibility note 16: plain padding, not business behavior
# compatibility note 17: plain padding, not business behavior
# compatibility note 18: plain padding, not business behavior
# compatibility note 19: plain padding, not business behavior
# compatibility note 20: plain padding, not business behavior
# compatibility note 21: plain padding, not business behavior
# compatibility note 22: plain padding, not business behavior
# compatibility note 23: plain padding, not business behavior
# compatibility note 24: plain padding, not business behavior
# compatibility note 25: plain padding, not business behavior
# compatibility note 26: plain padding, not business behavior
# compatibility note 27: plain padding, not business behavior
# compatibility note 28: plain padding, not business behavior
# compatibility note 29: plain padding, not business behavior
# compatibility note 30: plain padding, not business behavior
# compatibility note 31: plain padding, not business behavior
# compatibility note 32: plain padding, not business behavior
# compatibility note 33: plain padding, not business behavior
# compatibility note 34: plain padding, not business behavior
# compatibility note 35: plain padding, not business behavior
# compatibility note 36: plain padding, not business behavior
# compatibility note 37: plain padding, not business behavior
# compatibility note 38: plain padding, not business behavior
# compatibility note 39: plain padding, not business behavior
# compatibility note 40: plain padding, not business behavior
# compatibility note 41: plain padding, not business behavior
# compatibility note 42: plain padding, not business behavior
# compatibility note 43: plain padding, not business behavior
# compatibility note 44: plain padding, not business behavior
# compatibility note 45: plain padding, not business behavior
# compatibility note 46: plain padding, not business behavior
# compatibility note 47: plain padding, not business behavior
# compatibility note 48: plain padding, not business behavior
# compatibility note 49: plain padding, not business behavior
# compatibility note 50: plain padding, not business behavior
# compatibility note 51: plain padding, not business behavior
# compatibility note 52: plain padding, not business behavior
# compatibility note 53: plain padding, not business behavior
# compatibility note 54: plain padding, not business behavior
# compatibility note 55: plain padding, not business behavior
# compatibility note 56: plain padding, not business behavior
# compatibility note 57: plain padding, not business behavior
# compatibility note 58: plain padding, not business behavior
# compatibility note 59: plain padding, not business behavior
# compatibility note 60: plain padding, not business behavior
# compatibility note 61: plain padding, not business behavior
# compatibility note 62: plain padding, not business behavior
# compatibility note 63: plain padding, not business behavior
# compatibility note 64: plain padding, not business behavior
# compatibility note 65: plain padding, not business behavior
# compatibility note 66: plain padding, not business behavior
# compatibility note 67: plain padding, not business behavior
# compatibility note 68: plain padding, not business behavior
# compatibility note 69: plain padding, not business behavior
# compatibility note 70: plain padding, not business behavior
# compatibility note 71: plain padding, not business behavior
# compatibility note 72: plain padding, not business behavior
# compatibility note 73: plain padding, not business behavior
# compatibility note 74: plain padding, not business behavior
# compatibility note 75: plain padding, not business behavior
# compatibility note 76: plain padding, not business behavior
# compatibility note 77: plain padding, not business behavior
# compatibility note 78: plain padding, not business behavior
# compatibility note 79: plain padding, not business behavior
# compatibility note 80: plain padding, not business behavior
# compatibility note 81: plain padding, not business behavior
# compatibility note 82: plain padding, not business behavior
# compatibility note 83: plain padding, not business behavior
# compatibility note 84: plain padding, not business behavior
# compatibility note 85: plain padding, not business behavior
# compatibility note 86: plain padding, not business behavior
# compatibility note 87: plain padding, not business behavior
# compatibility note 88: plain padding, not business behavior
# compatibility note 89: plain padding, not business behavior
# compatibility note 90: plain padding, not business behavior
# compatibility note 91: plain padding, not business behavior
# compatibility note 92: plain padding, not business behavior
# compatibility note 93: plain padding, not business behavior
# compatibility note 94: plain padding, not business behavior
# compatibility note 95: plain padding, not business behavior
# compatibility note 96: plain padding, not business behavior
# compatibility note 97: plain padding, not business behavior
# compatibility note 98: plain padding, not business behavior
# compatibility note 99: plain padding, not business behavior
# compatibility note 100: plain padding, not business behavior
# compatibility note 101: plain padding, not business behavior
# compatibility note 102: plain padding, not business behavior
# compatibility note 103: plain padding, not business behavior
# compatibility note 104: plain padding, not business behavior
# compatibility note 105: plain padding, not business behavior
# compatibility note 106: plain padding, not business behavior
# compatibility note 107: plain padding, not business behavior
# compatibility note 108: plain padding, not business behavior
# compatibility note 109: plain padding, not business behavior
# compatibility note 110: plain padding, not business behavior
# compatibility note 111: plain padding, not business behavior
# compatibility note 112: plain padding, not business behavior
# compatibility note 113: plain padding, not business behavior


def process_batch(events, store, settings):
    accepted = []
    for event in events:
        tenant = event.get("tenant")
        if settings["tenant_required"] and not tenant:
            raise ValueError("tenant required")
        if settings["deduplicate"]:
            if not reserve_once(store, tenant, event["id"]):
                continue
        accepted.append(event)
    return accepted
