import pytest
from pydantic import ValidationError
from lib.metadata import FileMetadata


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
