# [Transit.sh](https://transit.sh)
Direct file transfer in your terminal. No subscription, no storage, no additional tool needed.

Leveraging [asyncio](https://docs.python.org/3/library/asyncio.html) and [queues](https://docs.python.org/3/library/asyncio-queue.html), the API waits for the receiver to start downloading before accepting more incoming data.<br>

> The service is currently deployed as a proof-of-concept without any no guarantees. Access it [here](https://transit.sh).

## Usage

```bash
# Send
curl -T <file> https://transit.sh/<some-string>/
```

```bash
# Receive
curl -JLO https://transit.sh/<some-string>/
```

```bash
# Example
curl -T /music/song.mp3 https://transit.sh/music-for-dad/
curl -JLO https://transit.sh/music-for-dad/
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
