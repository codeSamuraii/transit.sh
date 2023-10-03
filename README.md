# [Transit.sh](https://transit-sh.onrender.com)
This very simple API allows you to transfer large files seamlessly without the need for storage.

Leveraging [asyncio](https://docs.python.org/3/library/asyncio.html) and [queues](https://docs.python.org/3/library/asyncio-queue.html), the API waits for the receiver to start downloading before accepting more incoming data.<br>

## Usage
> The service is currently deployed as a proof-of-concept, however no guarantees apply for the use of the service.

Upload a file :
```bash
curl --upload-file myfile.bin https://transit-sh.onrender.com/my-test-upload/
```

Download the file :
```bash
 curl -JLO  https://transit-sh.onrender.com/my-test-upload
```
_You can also navigate to the URL with your browser._

## Local API
### Demonstration
https://github.com/codeSamuraii/transit.sh/assets/17270548/7b6e46c7-3595-4a38-bc9b-7b89c0eadc81

### Quick set-up
Dependencies :
```bash
pip install -r requirements.txt
```

Start the API :
```bash
uvicorn webapp:app --host 0.0.0.0 --port 80
```
