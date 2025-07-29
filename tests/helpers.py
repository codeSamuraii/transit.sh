import asyncio
from string import ascii_letters
from itertools import islice, repeat, chain
from typing import Tuple, Iterable, AsyncIterator
from annotated_types import T

from lib.metadata import FileMetadata


def generate_test_file(size_in_kb: int = 10) -> tuple[bytes, FileMetadata]:
    """Generates a test file with specified size in KB."""
    chunk_generator = ((letter * 1024).encode() for letter in chain.from_iterable(repeat(ascii_letters)))
    content = b''.join(next(chunk_generator) for _ in range(size_in_kb))

    metadata = FileMetadata(
        name="test_file.bin",
        size=len(content),
        type="application/octet-stream"
    )
    return content, metadata


async def chunks(data: bytes, chunk_size: int = 1024) -> AsyncIterator[bytes]:
    """Yield successive chunks of data."""
    for i in range(0, len(data), chunk_size):
        yield data[i:i + chunk_size]
        await asyncio.sleep(0)
