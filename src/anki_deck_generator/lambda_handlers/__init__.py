"""Lambda handlers for the simplified sync architecture.

Two handlers:
- ``handler_webhook``: thin webhook receiver — verifies, enqueues, returns 200.
- ``handler_sync``: unified sync Lambda with two modes:
    * mode A (``pull_changes``): pull ``changes.list``, upsert PendingEdits, advance pageToken.
    * mode B (``process_pending``): poll ready PendingEdits, run ``run_sync``, clear processed rows.

Note: this package is named ``lambda_handlers`` (not ``lambda``) because
``lambda`` is a Python reserved word.
"""
