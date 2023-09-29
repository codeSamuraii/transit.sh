# Transit.sh
Inspired by `transfer.sh`, this very simple API allows you to transfer large files without the need for storage.

The API waits for the receiver to start downloading before accepting more incoming data. Leveraging asyncio's queues, barely any data stays in memory.

### Usage
Upload a file :
```bash
curl --upload-file myfile.bin http://localhost:8000/my-file-upload/
```

Download the file :
```bash
wget http://localhost:8000/my-file-upload
```