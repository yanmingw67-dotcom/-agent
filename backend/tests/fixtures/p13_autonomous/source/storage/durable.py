def reserve_once(store, tenant, event_id):
    # store must implement an atomic persistent put_if_absent.
    return store.put_if_absent((tenant, event_id), True)
