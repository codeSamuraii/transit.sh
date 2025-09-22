import pytest
from pydantic import ValidationError
from lib.metadata import FileMetadata
from lib.transfer import parse_range_header, format_content_range


def test_file_metadata_creation():
    """Test that FileMetadata can be created with valid data."""
    metadata = FileMetadata(
        name="test.txt",
        size=1024,
        type="text/plain"
    )
    assert metadata.name == "test.txt"
    assert metadata.size == 1024
    assert metadata.type == "text/plain"


def test_file_metadata_validation_invalid_size():
    """Test that FileMetadata validates size field."""
    with pytest.raises(ValidationError):
        FileMetadata(name="test.txt", size=0)

    with pytest.raises(ValidationError):
        FileMetadata(name="test.txt", size=-1)


def test_file_metadata_validation_invalid_name():
    """Test that FileMetadata validates name field."""
    with pytest.raises(ValidationError):
        FileMetadata(name="", size=1024)


def test_file_metadata_json_serialization():
    """Test that FileMetadata can be serialized to and from JSON."""
    metadata = FileMetadata(
        name="test.txt",
        size=1024,
        type="text/plain"
    )

    json_str = metadata.to_json()
    deserialized = FileMetadata.from_json(json_str)

    assert deserialized.name == metadata.name
    assert deserialized.size == metadata.size
    assert deserialized.type == metadata.type


def test_file_metadata_name_escaping():
    """Test that FileMetadata properly escapes filenames during validation."""
    metadata = FileMetadata(
        name="file:name.txt",
        size=1024
    )
    assert metadata.name == "file name.txt"


def test_file_metadata_size_conversion():
    """Test that FileMetadata properly converts size strings to integers."""
    metadata = FileMetadata(
        name="test.txt",
        size="1024"
    )
    assert metadata.size == 1024
    assert isinstance(metadata.size, int)


def test_file_metadata_size_human_readable():
    """Test that FileMetadata properly formats sizes using ByteSize's human_readable method."""
    metadata = FileMetadata(
        name="test.txt",
        size=1024
    )
    assert metadata.size.human_readable() == "1.0KiB"

    metadata = FileMetadata(
        name="test.txt",
        size=1048576
    )
    assert metadata.size.human_readable() == "1.0MiB"


def test_parse_range_header_valid():
    """Test parsing valid range headers."""
    file_size = 10000

    result = parse_range_header("bytes=0-499", file_size)
    assert result is not None, "Should parse valid range header"
    assert result['start'] == 0, "Start should be 0"
    assert result['end'] == 499, "End should be 499"
    assert result['length'] == 500, "Length should be 500"

    result = parse_range_header("bytes=500-999", file_size)
    assert result is not None, "Should parse valid range header"
    assert result['start'] == 500, "Start should be 500"
    assert result['end'] == 999, "End should be 999"
    assert result['length'] == 500, "Length should be 500"


def test_parse_range_header_open_ended():
    """Test parsing open-ended range headers."""
    file_size = 10000

    result = parse_range_header("bytes=9500-", file_size)
    assert result is not None, "Should parse open-ended range"
    assert result['start'] == 9500, "Start should be 9500"
    assert result['end'] == 9999, "End should be file_size - 1"
    assert result['length'] == 500, "Length should be 500"


def test_parse_range_header_suffix():
    """Test parsing suffix range headers."""
    file_size = 10000

    result = parse_range_header("bytes=-500", file_size)
    assert result is not None, "Should parse suffix range"
    assert result['start'] == 9500, "Start should be 9500 for last 500 bytes"
    assert result['end'] == 9999, "End should be 9999"
    assert result['length'] == 500, "Length should be 500"


def test_parse_range_header_beyond_file_size():
    """Test parsing range headers beyond file size."""
    file_size = 1000

    result = parse_range_header("bytes=2000-3000", file_size)
    assert result is None, "Should return None for range beyond file size"

    result = parse_range_header("bytes=800-2000", file_size)
    assert result is not None, "Should parse range with end beyond file size"
    assert result['start'] == 800, "Start should be 800"
    assert result['end'] == 999, "End should be clamped to file_size - 1"
    assert result['length'] == 200, "Length should be 200"


@pytest.mark.parametrize("invalid_header", [
    None,
    "",
    "bytes",
    "bytes=",
    "kilobytes=0-100",
    "bytes=abc-def",
    "notarangeheader",
    "bytes=100-50"  # start > end
])
def test_parse_range_header_invalid(invalid_header):
    """Test parsing invalid range headers."""
    file_size = 10000

    result = parse_range_header(invalid_header, file_size)
    assert result is None, f"Should return None for invalid header: {invalid_header}"


def test_format_content_range():
    """Test formatting Content-Range header."""
    result = format_content_range(0, 499, 10000)
    assert result == "bytes 0-499/10000", f"Should format as 'bytes 0-499/10000', got '{result}'"

    result = format_content_range(500, 999, 10000)
    assert result == "bytes 500-999/10000", f"Should format as 'bytes 500-999/10000', got '{result}'"

    result = format_content_range(9500, 9999, 10000)
    assert result == "bytes 9500-9999/10000", f"Should format as 'bytes 9500-9999/10000', got '{result}'"