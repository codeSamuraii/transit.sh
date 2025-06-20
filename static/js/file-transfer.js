document.addEventListener('DOMContentLoaded', initFileTransfer);

function initFileTransfer() {
    const elements = {
        dropArea: document.getElementById('drop-area'),
        fileInput: document.getElementById('file-input'),
        uploadProgress: document.getElementById('upload-progress'),
        progressBarFill: document.getElementById('progress-bar-fill'),
        progressText: document.getElementById('progress-text'),
        statusText: document.getElementById('status-text'),
        shareLink: document.getElementById('share-link'),
        shareUrl: document.getElementById('share-url'),
    };

    setupEventListeners(elements);
}

function setupEventListeners(elements) {
    const { dropArea, fileInput } = elements;

    // Prevent default drag behaviors
    ['dragenter', 'dragover', 'dragleave', 'drop'].forEach(eventName => {
        dropArea.addEventListener(eventName, preventDefaults, false);
        document.body.addEventListener(eventName, preventDefaults, false);
    });
    ['dragenter', 'dragover'].forEach(eventName => {
        dropArea.addEventListener(eventName, () => highlight(dropArea), false);
    });
    ['dragleave', 'drop'].forEach(eventName => {
        dropArea.addEventListener(eventName, () => unhighlight(dropArea), false);
    });

    // Handle dropped files
    dropArea.addEventListener('drop', e => handleDrop(e, elements), false);
    dropArea.addEventListener('click', () => fileInput.click());
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
function generateTransferId() {
    const adjectives = [
        'misty', 'empty', 'dry', 'dark', 'icy', 'quiet', 'white', 'cool', 'dawn', 'wispy', 'blue','cold', 'damp', 'green',
        'long', 'late', 'bold', 'muddy', 'old', 'red', 'rough', 'still', 'small', 'shy', 'wild', 'black', 'young', 'holy'
    ];
    const nouns = [
        'river', 'breeze', 'moon', 'rain', 'wind', 'sea', 'snow', 'lake', 'sunset', 'pine', 'shadow', 'leaf', 'dawn',
        'forest', 'hill', 'cloud', 'meadow', 'sun', 'glade', 'bird', 'brook', 'bush', 'dew', 'dust', 'field', 'fire',
        'flower', 'grass', 'haze', 'night', 'pond', 'sound', 'sky', 'surf', 'violet', 'water', 'wave', 'wood', 'dream'
    ];

    // Generate a random transfer ID in the format: adjective-noun-number
    const adj = adjectives[Math.floor(Math.random() * adjectives.length)];
    const noun = nouns[Math.floor(Math.random() * nouns.length)];
    const num = Math.floor(Math.random() * 100);

    return `${adj}-${noun}-${num}`;
}

// UI updates
function showProgress(elements, message = 'Connecting...') {
    const { uploadProgress, statusText } = elements;
    uploadProgress.style.display = 'block';
    statusText.textContent = message;
    uploadProgress.setAttribute('aria-valuenow', '0'); // Add ARIA update
}

function updateProgress(elements, progress) {
    const { progressBarFill, progressText, uploadProgress } = elements; // Add uploadProgress
    const percentage = Math.min(100, Math.round(progress * 100));
    progressBarFill.style.width = `${percentage}%`;
    progressText.textContent = `${percentage}%`;
    uploadProgress.setAttribute('aria-valuenow', percentage); // Add ARIA update

    if (percentage === 100) {
        elements.statusText.textContent = 'Completing transfer...';
    }
}

function displayShareLink(elements, transferId) {
    const { shareUrl, shareLink, dropArea } = elements;
    shareUrl.value = `https://transit.sh/${transferId}`;
    shareLink.style.display = 'flex';
    dropArea.style.display = 'none';

    // Focus and select the share URL for easy copying
    setTimeout(() => {
        shareUrl.focus();
        shareUrl.select();
    }, 300);
}

function uploadFile(file, elements) {
    const { statusText } = elements;
    const transferId = generateTransferId();
    const ws = new WebSocket(`wss://transit.sh/send/${transferId}`);
    let abortController = new AbortController();

    showProgress(elements);

    // WebSocket event handlers
    ws.onopen = () => handleWsOpen(ws, file, transferId, elements, abortController);
    ws.onmessage = (event) => handleWsMessage(event, ws, file, elements, abortController);
    ws.onerror = (error) => {
        handleWsError(error, statusText);
        cleanupTransfer(abortController);
    };
    ws.onclose = () => {
        console.log('WebSocket connection closed');
        cleanupTransfer(abortController);
    };

    // Ensure cleanup on page unload
    window.addEventListener('beforeunload', () => cleanupTransfer(abortController), { once: true });
}

function handleWsOpen(ws, file, transferId, elements, abortController) {
    const { statusText } = elements;

    const metadata = {
        file_name: file.name,
        file_size: file.size,
        file_type: file.type || 'application/octet-stream'
    };

    ws.send(JSON.stringify(metadata));
    statusText.textContent = 'Waiting for the receiver to start the download... (max. 5 minutes)';
    displayShareLink(elements, transferId);
}

function handleWsMessage(event, ws, file, elements, abortController) {
    const { statusText } = elements;
    if (event.data === 'Go for file chunks') {
        statusText.textContent = 'Peer connected. Transferring file...';
        sendFileInChunks(ws, file, elements, abortController);
    } else if (event.data.startsWith('Error')) {
        statusText.textContent = event.data;
        statusText.style.color = 'var(--error)';
        console.error('Server error:', event.data);
        cleanupTransfer(abortController);
    } else {
        console.log('Unexpected message:', event.data);
    }
}

function handleWsError(error, statusText) {
    statusText.textContent = 'Error: ' + (error.message || 'Connection failed');
    statusText.style.color = 'var(--error)';
    console.error('WebSocket Error:', error);
}

function cleanupTransfer(abortController) {
    if (abortController) {
        abortController.abort();
        abortController = null;
    }
}

async function sendFileInChunks(ws, file, elements, abortController) {
    const { statusText } = elements;
    const chunkSize = 64 * 1024; // 64KiB
    let offset = 0;
    const reader = new FileReader();

    const signal = abortController.signal;
    if (signal.aborted) return;

    try {
        while (offset < file.size && !signal.aborted) {
            // Wait until WebSocket buffer has room
            await waitForWebSocketBuffer(ws);

            if (signal.aborted) break;

            const end = Math.min(offset + chunkSize, file.size);
            const slice = file.slice(offset, end);

            const chunk = await readChunkAsArrayBuffer(reader, slice, signal);
            if (signal.aborted || !chunk) break;

            ws.send(chunk);
            offset += chunk.byteLength;

            // Update progress
            updateProgress(elements, offset / file.size);
        }

        // If we completed successfully (not aborted), finalize the transfer
        if (!signal.aborted && offset >= file.size) {
            finalizeTransfer(ws, statusText);
        }
    } catch (error) {
        if (!signal.aborted) {
            statusText.textContent = `Error: ${error.message || 'Upload failed'}`;
            console.error('Upload error:', error);
            ws.close();
        }
    } finally {
        // Cleanup
        reader.onload = null;
        reader.onerror = null;
    }
}

// Promise-based wait for WebSocket buffer to clear
function waitForWebSocketBuffer(ws) {
    return new Promise(resolve => {
        const checkBuffer = () => {
            if (ws.bufferedAmount < 1024 * 1024) { // 1MiB threshold (16 chunks of 64KiB)
                resolve();
            } else {
                setTimeout(checkBuffer, 200);
            }
        };
        checkBuffer();
    });
}

// Promise-based file chunk reading
function readChunkAsArrayBuffer(reader, blob, signal) {
    return new Promise((resolve, reject) => {
        if (signal.aborted) {
            resolve(null);
            return;
        }

        reader.onload = e => resolve(e.target.result);
        reader.onerror = e => reject(new Error('Error reading file'));

        // Add abort handling
        signal.addEventListener('abort', () => {
            reader.abort();
            resolve(null);
        }, { once: true });

        reader.readAsArrayBuffer(blob);
    });
}

function finalizeTransfer(ws, statusText) {
    // Send empty chunk to signal end of transfer
    ws.send(new ArrayBuffer(0));

    setTimeout(() => {
        statusText.textContent = '✓ Transfer complete!';
        ws.close();
    }, 500);
}
