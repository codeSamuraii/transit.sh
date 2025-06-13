# Transit.sh - Direct File Transfer

[![Python Version](https://img.shields.io/badge/python-3.9%2B-blue.svg)](https://www.python.org/)
[![License](https://img.shields.io/badge/license-Custom-lightgrey.svg)](LICENSE)
[![GitHub stars](https://img.shields.io/github/stars/codeSamuraii/transit.sh.svg?style=social&label=Star&maxAge=2592000)](https://github.com/codeSamuraii/transit.sh/stargazers/)

**Transit.sh** enables direct, peer-to-peer file transfers without intermediary storage. Files are streamed from sender to receiver in chunks, leveraging WebSockets and HTTP.

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

Transit.sh uses a combination of WebSockets for browser-based transfers and HTTP PUT/GET for cURL or programmatic access.

1.  **Sender Initiates**:
    *   **Web UI**: The sender drags & drops a file. A unique transfer ID is generated, and a WebSocket connection is established. File metadata is sent.
    *   **cURL**: The sender uses `curl` to initiate a transfer.
2.  **Waiting for Receiver**: The sender's connection is held, and the system waits for the receiver to connect using the generated transfer ID.
3.  **Receiver Connects**:
    *   **Web Browser**: The receiver opens the link `https://transit.sh/<transfer-id>`.
    *   **cURL**: The receiver uses `curl -JLO https://transit.sh/<transfer-id>/`.
4.  **Data Streaming**: Once both parties are connected, file data is streamed in chunks from the sender to the receiver. The server acts as a temporary pipe, holding chunks in memory briefly.
5.  **Transfer Completion**: Both parties are notified upon successful transfer or if an error occurs.

Redis is used for managing transfer state (metadata, signaling events like "receiver connected") and queuing data chunks.

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
python -m uvicorn app:app --host 0.0.0.0 --port 8080
```

> **Note:** While the API works with multiple workers, it hasn't been fully tested in a multi-worker setup.

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
