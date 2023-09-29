# Transit.sh
> Inspired by [`transfer.sh`](https://transfer.sh/)

This very simple API allows you to transfer large files seamlessly without the need for storage.

Leveraging [asyncio](https://docs.python.org/3/library/asyncio.html) and [queues](https://docs.python.org/3/library/asyncio-queue.html), the API waits for the receiver to start downloading before accepting more incoming data.<br>

### Demonstration
https://github.com/codeSamuraii/transit.sh/assets/17270548/7b6e46c7-3595-4a38-bc9b-7b89c0eadc81

### Quick set-up
Dependencies (requires `pipenv`) :
```bash
pipenv install
```

Start the API :
```bash
pipenv run uvicorn webapp:app --host 0.0.0.0 --port 80
```

### Usage
Upload a file :
```bash
curl --upload-file myfile.bin http://localhost/my-file-upload/
```
_You don't need the `curl/` prefix anymore when uploading the file._

Download the file :
```bash
wget http://localhost/my-file-upload
```
_You can also navigate to the URL with your browser._
