import pytest
from lib.metadata import FileMetadata


@pytest.mark.parametrize("size_bytes, expected", [
    (0, "0 B"),
    (1023, "1023.0 B"),
    (1024, "1.0 KiB"),
    (1536, "1.5 KiB"),
    (1024 ** 2, "1.0 MiB"),
    (int(1.5 * 1024 ** 2), "1.5 MiB"),
    (1024 ** 3, "1.0 GiB"),
])
def test_format_size(size_bytes, expected):
    assert FileMetadata.format_size(size_bytes) == expected


@pytest.mark.parametrize("filename, expected", [
    ("file:name.txt", "filename.txt"),
    ("file|name.txt", "filename.txt"),
    ("file@name.txt", "filename.txt"),
    ("file/name.txt", "filename.txt"),
    ("file\\name.txt", "filename.txt"),
    ("valid-name.zip", "valid-name.zip"),
])
def test_escape_filename(filename, expected):
    assert FileMetadata.escape_filename(filename) == expected


@pytest.mark.parametrize("length, expected", [
    ("1024", 1024),
    (2048, 2048),
    (" 4096 ", 4096),
])
def test_process_length(length, expected):
    assert FileMetadata.process_length(length) == expected


@pytest.mark.parametrize("invalid_length", ["-100", "0", "abc", "1.5"])
def test_process_length_invalid(invalid_length):
    with pytest.raises(ValueError):
        FileMetadata.process_length(invalid_length)
