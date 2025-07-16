from string import ascii_letters
from itertools import islice, repeat, chain
from typing import Tuple, Iterable, Iterator
from annotated_types import T

from lib.metadata import FileMetadata


def generate_test_file(size_in_kb: int = 10) -> tuple[bytes, FileMetadata]:
    """Generates a test file with specified size in KB."""
    chunk_generator = ((letter * 1024).encode() for letter in chain.from_iterable(repeat(ascii_letters)))
    content = b''.join(next(chunk_generator) for _ in range(size_in_kb))

    metadata = FileMetadata(
        name="test_file.bin",
        size=len(content),
        content_type="application/octet-stream"
    )
    return content, metadata


def batched(iterable: Iterable[T], chunk_size: int) -> Iterator[Tuple[T, ...]]:
    "Batch data into lists of length n. The last batch may be shorter."
    # batched('ABCDEFG', 3) --> ABC DEF G
    it = iter(iterable)
    while True:
        batch = bytes(islice(it, chunk_size))
        if not batch:
            return
        yield batch
