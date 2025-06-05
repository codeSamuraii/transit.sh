# [Transit.sh](https://transit.sh)
Direct file transfer without intermediary storage.

Leveraging [asyncio](https://docs.python.org/3/library/asyncio.html) and [queues](https://docs.python.org/3/library/asyncio-queue.html), the API waits for the receiver to start downloading before accepting more incoming data.<br>

> The service is currently deployed as a proof-of-concept without any no guarantees. Access it [here](https://transit.sh).

## Local API
You can run the API locally to test it or use it in your own projects. The API is built with [FastAPI](https://fastapi.tiangolo.com/) and uses [Uvicorn](https://www.uvicorn.org/) as the ASGI server.

### Quick set-up
Dependencies :
```bash
pip install -r requirements.txt
```

Start the API :
```bash
uvicorn webapp:app --host 0.0.0.0 --port 80
```
