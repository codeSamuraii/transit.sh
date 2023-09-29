# Transit.sh
Inspired by `transfer.sh`, this very simple API allows you to transfer large files without the need for storage.

The API waits for the receiver to start downloading before accepting more incoming data. Leveraging asyncio's queues, barely any data stays in memory.

### Usage
Upload a file :
```bash
curl --upload-file myfile.bin http://localhost:8000/curl/my-file-upload/
```

Download the file (you can also use your browser) :
```bash
curl http://localhost:8000/my-file-upload --output myfile.bin
```

### Demonstration
https://github.com/codeSamuraii/transit.sh/assets/17270548/7b6e46c7-3595-4a38-bc9b-7b89c0eadc81

