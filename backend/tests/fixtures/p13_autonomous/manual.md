# Event processing
The API has preview and commit modes. Operators need to know whether repeated events in different tenants or repeated requests affect the result. Configuration controls commit processing. Preview is a local convenience and must not automatically be treated as a substitute for committed processing. Storage behavior depends on the injected implementation.
