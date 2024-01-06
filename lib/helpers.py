import pathlib
import requests


def send_file(file_path: pathlib.Path, identifier: str):
    file_name = file_path.name
    send_url = f"http://127.0.0.1/{identifier}/{file_name}"

    with file_path.open() as file:
        response = requests.post(send_url, files={file_name: file})

    return response.status_code

