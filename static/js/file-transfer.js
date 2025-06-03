document.addEventListener('DOMContentLoaded', initFileTransfer);

function initFileTransfer() {
    const elements = {
        dropArea: document.getElementById('drop-area'),
        fileInput: document.getElementById('file-input'),
        transferIdInput: document.getElementById('transfer-id'),
        uploadProgress: document.getElementById('upload-progress'),
        progressBarFill: document.getElementById('progress-bar-fill'),
        progressText: document.getElementById('progress-text'),
        statusText: document.getElementById('status-text'),
        shareLink: document.getElementById('share-link'),
        shareUrl: document.getElementById('share-url'),
        shareCommand: document.getElementById('share-command')
    };

    setupEventListeners(elements);
}

function setupEventListeners(elements) {
    const { dropArea, fileInput, transferIdInput } = elements;

    // Prevent default drag behaviors
    ['dragenter', 'dragover', 'dragleave', 'drop'].forEach(eventName => {
        dropArea.addEventListener(eventName, preventDefaults, false);
        document.body.addEventListener(eventName, preventDefaults, false);
    });

    // Highlight drop area when item is dragged over it
    ['dragenter', 'dragover'].forEach(eventName => {
        dropArea.addEventListener(eventName, () => highlight(dropArea), false);
    });

    ['dragleave', 'drop'].forEach(eventName => {
        dropArea.addEventListener(eventName, () => unhighlight(dropArea), false);
    });

    // Handle dropped files
    dropArea.addEventListener('drop', e => handleDrop(e, elements), false);

    // Handle click on drop area
    dropArea.addEventListener('click', () => fileInput.click());

    // Handle file selection
    fileInput.addEventListener('change', () => {
        if (fileInput.files.length) {
            handleFiles(fileInput.files, elements);
        }
    });
}

// Event helpers
function preventDefaults(e) {
    e.preventDefault();
    e.stopPropagation();
}

function highlight(dropArea) {
    dropArea.classList.add('highlight');
}

function unhighlight(dropArea) {
    dropArea.classList.remove('highlight');
}

function handleDrop(e, elements) {
    const dt = e.dataTransfer;
    const files = dt.files;
    handleFiles(files, elements);
}

function handleFiles(files, elements) {
    if (files.length > 0) {
        uploadFile(files[0], elements);
    }
}

// Transfer ID generation
function generateTransferId(transferIdInput) {
    if (!transferIdInput.value.trim()) {
        const randomId = Math.random().toString(36).substring(2, 10);
        transferIdInput.value = randomId;
    }
    return transferIdInput.value;
}

// UI updates
function showProgress(elements, message = 'Connecting...') {
    const { uploadProgress, statusText } = elements;
    uploadProgress.style.display = 'block';
    statusText.textContent = message;
}

function updateProgress(elements, progress) {
    const { progressBarFill, progressText } = elements;
    const percentage = Math.min(100, Math.round(progress * 100));
    progressBarFill.style.width = `${percentage}%`;
    progressText.textContent = `${percentage}%`;
}

function displayShareLink(elements, transferId) {
    const { shareUrl, shareCommand, shareLink } = elements;
    shareUrl.value = `https://transit.sh/${transferId}`;
    shareCommand.textContent = `curl -JLO https://transit.sh/${transferId}`;
    shareLink.style.display = 'block';
}

// File upload with WebSockets
function uploadFile(file, elements) {
    const { transferIdInput, statusText } = elements;
    const transferId = generateTransferId(transferIdInput);
    const ws = new WebSocket(`ws://localhost:8080/send/${transferId}`);

    showProgress(elements);

    // WebSocket event handlers
    ws.onopen = () => handleWsOpen(ws, file, transferId, elements);
    ws.onmessage = (event) => handleWsMessage(event, ws, file, elements);
    ws.onerror = (error) => handleWsError(error, statusText);
    ws.onclose = () => console.log('WebSocket connection closed');
}

function handleWsOpen(ws, file, transferId, elements) {
    const { statusText } = elements;
    // Send file metadata
    const metadata = {
        file_name: file.name,
        file_size: file.size,
        file_type: file.type || 'application/octet-stream'
    };

    ws.send(JSON.stringify(metadata));
    statusText.textContent = 'Waiting for peer to connect...';
    displayShareLink(elements, transferId);
}

function handleWsMessage(event, ws, file, elements) {
    if (event.data === 'Go for file chunks') {
        const { statusText } = elements;
        statusText.textContent = 'Peer connected. Uploading file...';
        sendFileInChunks(ws, file, elements);
    } else {
        console.log('Unexpected message:', event.data);
    }
}

function handleWsError(error, statusText) {
    statusText.textContent = 'Error: ' + error.message;
    console.error('WebSocket Error:', error);
}

function sendFileInChunks(ws, file, elements) {
    const { statusText } = elements;
    const chunkSize = 32768; // 32KB chunks
    let offset = 0;
    const reader = new FileReader();

    function processNextChunk() {
        // Check if we're done
        if (offset >= file.size) {
            finalizeTransfer(ws, statusText);
            return;
        }

        // Check if websocket is ready to send more data
        if (ws.bufferedAmount > 1024 * 1024) {  // 1MB threshold
            setTimeout(processNextChunk, 100);
            return;
        }

        const end = Math.min(offset + chunkSize, file.size);
        const slice = file.slice(offset, end);

        reader.onload = (e) => {
            if (e.target.result.byteLength > 0) {
                try {
                    ws.send(e.target.result);
                    offset += e.target.result.byteLength;

                    // Update progress
                    updateProgress(elements, offset / file.size);

                    // Schedule the next chunk processing
                    setTimeout(processNextChunk, 0);
                } catch (error) {
                    statusText.textContent = `Error: ${error.message}`;
                    console.error('Error sending chunk:', error);
                }
            }
        };

        reader.onerror = (error) => {
            statusText.textContent = `Error reading file: ${error}`;
            console.error('FileReader error:', error);
        };

        // Start reading this chunk
        reader.readAsArrayBuffer(slice);
    }

    // Start the upload process
    processNextChunk();
}

function finalizeTransfer(ws, statusText) {
    // Send empty chunk to signal end of transfer
    ws.send(new ArrayBuffer(0));

    // Wait a moment to ensure the empty buffer is sent
    setTimeout(() => {
        // Close the connection explicitly
        ws.close();
        statusText.textContent = 'Upload complete!';
    }, 100);
}
