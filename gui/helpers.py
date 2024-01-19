import aiohttp
import pathlib
import requests


def send_file(file_path: pathlib.Path, identifier: str):
    file_name = file_path.name
    send_url = f"http://127.0.0.1/{identifier}/{file_name}"

    with file_path.open('rb') as file:
        response = requests.put(send_url, files={file_name: file})

    return response.status_code


async def send_file_async(file_path: pathlib.Path, identifier: str):
    file_name = file_path.name
    send_url = f"http://127.0.0.1/{identifier}/{file_name}"

    async with aiohttp.ClientSession() as session:
        with file_path.open('rb') as file:
            async with session.put(send_url, data={file_name: file}) as req:
                rep = req.status
    
    return rep
        