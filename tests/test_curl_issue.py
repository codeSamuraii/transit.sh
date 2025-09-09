"""
Test for reproducing the curl upload issue described in the problem statement.
"""
import anyio
import httpx
import pytest
import tempfile
import os

from tests.helpers import generate_test_file


@pytest.mark.anyio
async def test_curl_upload_missing_content_length(test_client: httpx.AsyncClient):
    """Tests HTTP upload when Content-Length header is missing (simulating curl issue)."""
    uid = "curl-no-content-length"
    file_content, file_metadata = generate_test_file(size_in_kb=64)
    
    # Simulate curl upload without Content-Length header (one potential issue)
    headers = {
        'Content-Type': file_metadata.type,
        # Missing Content-Length - some curl configurations might not set this
    }
    
    response = await test_client.put(
        f"/{uid}/{file_metadata.name}", 
        content=file_content, 
        headers=headers
    )
    
    # This should fail with 400 due to missing/invalid metadata
    assert response.status_code == 400
    assert "Invalid file metadata" in response.text or "Cannot decode file metadata" in response.text


@pytest.mark.anyio
async def test_curl_upload_missing_content_type(test_client: httpx.AsyncClient):
    """Tests HTTP upload when Content-Type header is missing (simulating curl issue)."""
    uid = "curl-no-content-type"
    file_content, file_metadata = generate_test_file(size_in_kb=64)
    
    # Simulate curl upload without Content-Type header (another potential issue)
    headers = {
        'Content-Length': str(file_metadata.size),
        # Missing Content-Type - curl might not set this for unknown file types
    }
    
    response = await test_client.put(
        f"/{uid}/{file_metadata.name}", 
        content=file_content, 
        headers=headers
    )
    
    # This might work if Content-Type defaults properly, or fail
    print(f"Response status: {response.status_code}")
    print(f"Response text: {response.text}")


@pytest.mark.anyio
async def test_curl_upload_zero_content_length(test_client: httpx.AsyncClient):
    """Tests HTTP upload when Content-Length is 0 (potential curl issue)."""
    uid = "curl-zero-length"
    file_content, file_metadata = generate_test_file(size_in_kb=64)
    
    # Simulate wrong Content-Length (could happen with curl misuse)
    headers = {
        'Content-Type': file_metadata.type,
        'Content-Length': '0',  # Wrong content length
    }
    
    response = await test_client.put(
        f"/{uid}/{file_metadata.name}", 
        content=file_content, 
        headers=headers
    )
    
    # This should fail with 400 due to size validation (gt=0)
    assert response.status_code == 400
    assert "Invalid file metadata" in response.text


@pytest.mark.anyio
async def test_curl_upload_short_filename(test_client: httpx.AsyncClient):
    """Tests HTTP upload with a very short filename (potential validation issue)."""
    uid = "curl-short-name"
    file_content, file_metadata = generate_test_file(size_in_kb=64)
    
    # Simulate upload with very short filename
    short_filename = "a"  # Only 1 character - should fail min_length=2 validation
    headers = {
        'Content-Type': file_metadata.type,
        'Content-Length': str(file_metadata.size),
    }
    
    response = await test_client.put(
        f"/{uid}/{short_filename}", 
        content=file_content, 
        headers=headers
    )
    
    # This should fail with 400 due to filename validation
    assert response.status_code == 400
    assert "Invalid file metadata" in response.text


@pytest.mark.anyio
async def test_curl_upload_with_expect_100_continue(test_client: httpx.AsyncClient):
    """Tests HTTP upload with Expect: 100-continue header (like curl --expect100-timeout)."""
    uid = "curl-expect-100"
    file_content, file_metadata = generate_test_file(size_in_kb=64)
    
    # Simulate curl with --expect100-timeout flag
    headers = {
        'Content-Type': file_metadata.type,
        'Content-Length': str(file_metadata.size),
        'Expect': '100-continue',  # This header is set by curl --expect100-timeout
    }
    
    async def sender():
        response = await test_client.put(
            f"/{uid}/{file_metadata.name}", 
            content=file_content, 
            headers=headers
        )
        print(f"Upload response status: {response.status_code}")
        print(f"Upload response text: {response.text}")
        return response
    
    async def receiver():
        await anyio.sleep(1.0)  # Wait for upload to start
        response = await test_client.get(f"/{uid}?download=true")
        print(f"Download response status: {response.status_code}")
        return response
    
    async with anyio.create_task_group() as tg:
        tg.start_soon(sender)
        tg.start_soon(receiver)