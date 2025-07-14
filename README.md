# Transit.sh - Direct File Transfer

[![Python Version](https://img.shields.io/badge/python-3.9%2B-blue.svg)](https://www.python.org/)
[![License](https://img.shields.io/badge/license-Custom-lightgrey.svg)](LICENSE)
[![GitHub stars](https://img.shields.io/github/stars/codeSamuraii/transit.sh.svg?style=social&label=Star&maxAge=2592000)](https://github.com/codeSamuraii/transit.sh/stargazers/)

**Transit.sh** enables direct, client-to-client file transfers without intermediary storage. Files are streamed from sender to receiver in real time. It leverages Redis, WebSockets and FastAPI for a modern, scalable architecture.

> **Service Status:** The public instance at [https://transit.sh](https://transit.sh) is a proof-of-concept deployment. While functional, it comes with no service guarantees.

## Usage
1.  Open [https://transit.sh](https://transit.sh) (or your local instance) in your browser.
2.  Drag and drop a file onto the designated area or click to select a file.
3.  A unique shareable link will be generated. Copy and send this link to the receiver.
4.  Wait for the receiver to start the download. The progress will be displayed.

## Features

*   **Direct Transfer**: Files are streamed directly between peers, not stored on the server.
*   **Multiple Interfaces**:
    *   Web UI for drag-and-drop uploads.
    *   cURL-friendly HTTP endpoints for command-line usage.
*   **No Sign-up Required**: Generate a unique link and share it.
*   **Lightweight & Fast**: Built with FastAPI, Redis, Uvicorn, and asyncio for high performance.

## How It Works

Transit.sh orchestrates a direct data stream between a sender and a receiver, using a Redis backend for signaling and temporary chunk buffering. The server acts as a smart pipe, ensuring the sender only transmits data when the receiver is ready, and that data is forwarded in real-time without being stored on disk.

1.  **Initiation (Sender)**:
    *   A sender initiates a transfer, for example, by dropping a file in the web UI. A unique transfer ID is generated.
    *   A WebSocket connection is established to the `/send/{transfer-id}` endpoint.
    *   The client sends a JSON message containing the file's metadata (name, size, type).
    *   On the server, a `FileTransfer` object is created, and the metadata is stored in a Redis key with a set expiration time.

2.  **Waiting for Receiver (Sender)**:
    *   The sender's connection is held open. The server process handling the sender now subscribes to a unique Redis Pub/Sub channel for this specific transfer (e.g., `transfer:{transfer-id}:client_connected`).
    *   The sender waits for a "receiver connected" signal on this channel. This is a blocking operation that prevents any file data from being sent until the receiver is present.

3.  **Connection (Receiver)**:
    *   The receiver uses the shared link to access `/{transfer-id}`.
    *   The server retrieves the file metadata from Redis to serve a download page or prepare for a direct download (e.g., for `cURL`).
    *   When the download is initiated, the server publishes a message to the transfer's specific Pub/Sub channel.

4.  **Data Streaming**:
    *   The message published by the receiver's process is received by the sender's process, unblocking the wait step.
    *   The sender's client is now instructed to start sending file data.
    *   Chunks of the file are sent over the WebSocket connection. Each chunk is pushed into a Redis list, which serves as a temporary, in-memory queue for the transfer.
    *   The receiver's process, which has been waiting since it connected, starts pulling chunks from the Redis list as they arrive.
    *   These chunks are immediately streamed to the receiver over an HTTP connection.
    *   A simple backpressure mechanism is in place: the sender will pause if the Redis list (queue) grows too large, ensuring the sender doesn't overwhelm a slower receiver.

5.  **Completion & Cleanup**:
    *   Once the sender has sent all the file's bytes, it places a special `DONE_FLAG` in the queue.
    *   When the receiver reads this flag, it knows the transfer is complete.
    *   If either party disconnects prematurely, an `INTERRUPT` flag is set, which terminates the transfer on the other end.
    *   After the transfer is finished or has failed, a cleanup process removes all associated keys (metadata, queue, event flags) from Redis.

This architecture ensures that the file data is never stored at rest on the server, flowing from sender to receiver with minimal buffering in Redis.

## Local Development & Deployment

### Prerequisites

*   Python 3.9+
*   Redis server running

### Setup

1.  **Clone the repository:**
    ```bash
    git clone https://github.com/codeSamuraii/transit.sh.git
    cd transit.sh
    ```

2.  **Create a virtual environment (recommended):**
    ```bash
    python -m venv venv
    source venv/bin/activate  # On Windows: venv\Scripts\activate
    ```

3.  **Install dependencies:**
    ```bash
    pip install -r requirements.txt
    ```

4.  **Configure environment variables (optional):**
    Create a `.env` file or set environment variables directly:
    *   `REDIS_URL`: Defaults to `redis://localhost:6379`.
    *   `SENTRY_DSN`: For Sentry error tracking (optional).

### Running the Application
Ensure your Redis server is running and accessible at the configured URL.

For local development:
```bash
python -u app.py
```

For deployment:
```bash
uvicorn app:app --host 0.0.0.0 --port 8080
```

> **Note:** The API supports multiple workers on different machines as long as the Redis cache is accessible on all of them, ideally with low latency. Accessing Redis over the internet would drastically reduce transfers speeds.

## Contributing

Contributions are welcome! Please feel free to submit pull requests or open issues for bugs, feature requests, or improvements.

1.  Fork the repository.
2.  Create a new branch (`git checkout -b feature/your-feature-name`).
3.  Make your changes.
4.  Commit your changes (`git commit -am 'Add some feature'`).
5.  Push to the branch (`git push origin feature/your-feature-name`).
6.  Create a new Pull Request.

## License

This project is licensed under a custom license for now. See the [LICENSE](LICENSE) file for details.
Briefly:
- Free for non-commercial use.
- Forks are allowed under the same license with attribution.
- Forks under a different license require the author's authorization.
- Commercial use is prohibited.

I want to use a more standard open-source license in the future, such as AGPLv3 or MPL 2.0, but for now, please refer to the [LICENSE](LICENSE) file for the current terms.

## Acknowledgements
*   Inspired by [transfer.sh](https://github.com/dutchcoders/transfer.sh) and [JustBeamIt](https://www.justbeamit.com/)
