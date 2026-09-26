"""The exceptions this library raises. All inherit from LibreSyncError."""


class LibreSyncError(Exception):
    """Base class for every error raised by aiolibresync."""


class NotConnectedError(LibreSyncError):
    """A command was issued while the relevant socket was down."""


class UnsafeCommandError(LibreSyncError):
    """A port-7777 frame was requested for a message box outside ALLOWED_MIDS.

    The `command` field is a LUCI message-box id, so an arbitrary value
    addresses an arbitrary box on the internal bus. This is a guard, not a
    validation nicety.
    """


class ConfirmationTimeout(LibreSyncError):
    """The device did not confirm a command, or did not answer a query in time."""
