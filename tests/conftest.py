"""Helpers shared by every test module: capture loaders, and `until`.

The fixtures are the golden files for the whole codec: real bytes in, expected
objects out. See fixtures/README.md for what they are and what was changed.
"""

import asyncio
import re
from collections.abc import Callable
from pathlib import Path

CAPTURES = Path(__file__).parent / "fixtures"


async def until(
    predicate: Callable[[], bool],
    # A polling helper's natural interface is a timeout.
    timeout: float = 2.0,  # noqa: ASYNC109
) -> None:
    """Wait until `predicate()` is true, or fail the test.

    Polling rather than an Event because the conditions under test are side
    effects of a background task, not signals it raises.
    """
    async with asyncio.timeout(timeout):
        # There is no Event to await; the predicate is a poll.
        while not predicate():  # noqa: ASYNC110
            await asyncio.sleep(0.01)


def capture_arrays(filename: str):
    """Yield (name, bytes) for each C array in a Wireshark C-array export.

    `peer0_*` is client -> device, `peer1_*` is device -> client.
    """
    src = (CAPTURES / filename).read_text()
    for name, body in re.findall(r"char (\w+)\[\] = \{[^}]*?\*/\s*([^}]*)\}", src, re.S):
        yield name, bytes(int(x, 16) for x in re.findall(r"0x([0-9a-fA-F]{2})", body))


def tap_log_frames(filename: str):
    """Yield frames from a passive tap log. A tap sends nothing, so these are
    all device -> client."""
    for line in (CAPTURES / filename).read_text().splitlines():
        if line.startswith("#"):
            continue
        m = re.search(r"\d+\s+((?:[0-9a-f]{2} )+[0-9a-f]{2})", line)
        if m:
            yield bytes.fromhex(m.group(1).replace(" ", ""))
