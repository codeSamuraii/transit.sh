const CHUNK_SIZE_MOBILE = 32 * 1024;                        // 32KiB for mobile devices
const CHUNK_SIZE_DESKTOP = 64 * 1024;                       // 64KiB for desktop devices
const BUFFER_THRESHOLD_MOBILE = CHUNK_SIZE_MOBILE * 16;     // 512KiB buffer threshold for mobile
const BUFFER_THRESHOLD_DESKTOP = CHUNK_SIZE_DESKTOP * 16;   // 1MiB buffer threshold for desktop
const BUFFER_CHECK_INTERVAL = 200;                          // 200ms interval for buffer checks
const SHARE_LINK_FOCUS_DELAY = 300;                         // 300ms delay before focusing share link
const TRANSFER_FINALIZE_DELAY = 1000;                       // 1000ms delay before finalizing transfer
const MOBILE_BREAKPOINT = 768;                              // 768px mobile breakpoint
const TRANSFER_ID_MAX_NUMBER = 1000;                        // Maximum number for transfer ID generation (0-999)

const DEBUG_LOGS = true;
const log = {
    debug: (...args) => DEBUG_LOGS && console.debug(...args),
    info: (...args) => console.info(...args),
    warn: (...args) => console.warn(...args),
    error: (...args) => console.error(...args)
};

initFileTransfer();

function initFileTransfer() {
    log.debug('Initializing file transfer interface');
    const elements = {
        dropArea: document.getElementById('drop-area'),
        dropAreaText: document.getElementById('drop-area-text'),
        fileInput: document.getElementById('file-input'),
        uploadProgress: document.getElementById('upload-progress'),
        progressBarFill: document.getElementById('progress-bar-fill'),
        progressText: document.getElementById('progress-text'),
        statusText: document.getElementById('status-text'),
        shareLink: document.getElementById('share-link'),
        shareUrl: document.getElementById('share-url')
    };

    if (isMobileDevice() && elements.dropAreaText) {
        elements.dropAreaText.textContent = 'Tap here to select a file';
        log.debug('Updated UI text for mobile device');
    }

    setupEventListeners(elements);
}

function setupEventListeners(elements) {
    const { dropArea, fileInput } = elements;

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

    dropArea.addEventListener('drop', e => handleDrop(e, elements), false);
    dropArea.addEventListener('click', () => fileInput.click());
    fileInput.addEventListener('change', () => {
        if (fileInput.files.length) {
            handleFiles(fileInput.files, elements);
        }
    });
}

function preventDefaults(e) {
    e.preventDefault();
    e.stopPropagation();
}

function highlight(element) {
    element.classList.add('highlight');
}

function unhighlight(element) {
    element.classList.remove('highlight');
}

function handleDrop(e, elements) {
    const files = e.dataTransfer.files;
    handleFiles(files, elements);
}

function handleFiles(files, elements) {
    if (files.length > 0) {
        const file = files[0];
        log.info('File selected:', {
            name: file.name,
            size: file.size,
            type: file.type,
            lastModified: new Date(file.lastModified).toISOString()
        });
        uploadFile(file, elements);
    }
}

function showProgress(elements, message = 'Connecting...') {
    const { uploadProgress, statusText } = elements;
    uploadProgress.style.display = 'block';
    statusText.textContent = message;
    uploadProgress.setAttribute('aria-valuenow', '0');
}

function updateProgress(elements, progress) {
    const { progressBarFill, progressText, uploadProgress, statusText } = elements;
    const percentage = Math.min(100, Math.round(progress * 100));
    progressBarFill.style.width = `${percentage}%`;
    progressText.textContent = `${percentage}%`;
    uploadProgress.setAttribute('aria-valuenow', percentage);

    if (percentage === 100) {
        statusText.textContent = 'Completing transfer...';
    }
}

function displayShareLink(elements, transferId) {
    const { shareUrl, shareLink, dropArea } = elements;
    shareUrl.value = `${window.location.origin}/${transferId}`;
    shareLink.style.display = 'flex';
    dropArea.style.display = 'none';

    setTimeout(() => {
        shareUrl.focus();
        shareUrl.select();
    }, SHARE_LINK_FOCUS_DELAY);
}

function uploadFile(file, elements) {
    const transferId = generateTransferId();

    const wsProtocol = window.location.protocol === 'https:' ? 'wss:' : 'ws:';
    const wsUrl = `${wsProtocol}//${window.location.host}/send/${transferId}`;

    log.info('Starting upload:', { transferId, fileName: file.name, fileSize: file.size, wsUrl });

    const ws = new WebSocket(wsUrl);
    const abortController = new AbortController();
    const uploadState = {
        file: file,
        transferId: transferId,
        isUploading: false,
        wakeLock: null
    };

    showProgress(elements);

    ws.onopen = () => handleWsOpen(ws, file, transferId, elements);
    ws.onmessage = (event) => handleWsMessage(event, ws, file, elements, abortController, uploadState);
    ws.onerror = (error) => handleWsError(error, elements.statusText);
    ws.onclose = (event) => {
        log.info('WebSocket connection closed:', { code: event.code, reason: event.reason, wasClean: event.wasClean });
        if (uploadState.isUploading && !event.wasClean) {
            elements.statusText.textContent = 'Connection lost. Please try uploading again.';
            elements.statusText.style.color = 'var(--error)';
        }
        cleanupTransfer(abortController, uploadState);
    };

    const handleVisibilityChange = () => {
        if (document.hidden && uploadState.isUploading) {
            log.warn('App went to background during active upload');
            if (isMobileDevice()) {
                elements.statusText.textContent = '⚠️ Keep app in foreground during upload';
                elements.statusText.style.color = 'var(--warning)';
            }
        } else if (!document.hidden && uploadState.isUploading) {
            log.info('App returned to foreground');
            if (ws.readyState !== WebSocket.OPEN) {
                elements.statusText.textContent = 'Connection lost. Please try uploading again.';
                elements.statusText.style.color = 'var(--error)';
                uploadState.isUploading = false;
            }
        }
    };
    document.addEventListener('visibilitychange', handleVisibilityChange);

    const handleBeforeUnload = (e) => {
        if (uploadState.isUploading) {
            e.preventDefault();
            e.returnValue = 'File upload in progress. Are you sure you want to leave?';
            return e.returnValue;
        }
    };
    window.addEventListener('beforeunload', handleBeforeUnload);

    window.addEventListener('unload', () => {
        document.removeEventListener('visibilitychange', handleVisibilityChange);
        window.removeEventListener('beforeunload', handleBeforeUnload);
        cleanupTransfer(abortController, uploadState);
    }, { once: true });

    if (isMobileDevice() && 'wakeLock' in navigator) {
        requestWakeLock(uploadState);
    }
}

function handleWsOpen(ws, file, transferId, elements) {
    log.info('WebSocket connection opened');
    const metadata = {
        file_name: file.name,
        file_size: file.size,
        file_type: file.type || 'application/octet-stream'
    };
    log.info('Sending file metadata:', metadata);
    ws.send(JSON.stringify(metadata));
    elements.statusText.textContent = 'Waiting for the receiver to start the download... (max. 5 minutes)';
    displayShareLink(elements, transferId);
}

function handleWsMessage(event, ws, file, elements, abortController, uploadState) {
    log.debug('WebSocket message received:', event.data);
    if (event.data === 'Go for file chunks') {
        log.info('Receiver connected, starting file transfer');
        elements.statusText.textContent = 'Peer connected. Transferring file...';
        uploadState.isUploading = true;
        sendFileInChunks(ws, file, elements, abortController, uploadState);
    } else if (event.data.startsWith('Error')) {
        log.error('Server error:', event.data);
        elements.statusText.textContent = event.data;
        elements.statusText.style.color = 'var(--error)';
        cleanupTransfer(abortController, uploadState);
    } else {
        log.warn('Unexpected message:', event.data);
    }
}

function handleWsError(error, statusText) {
    log.error('WebSocket error:', error);
    statusText.textContent = 'Error: ' + (error.message || 'Connection failed');
    statusText.style.color = 'var(--error)';
}

async function sendFileInChunks(ws, file, elements, abortController, uploadState) {
    const chunkSize = isMobileDevice() ? CHUNK_SIZE_MOBILE : CHUNK_SIZE_DESKTOP;
    log.info('Starting chunked upload:', { chunkSize, fileSize: file.size, totalChunks: Math.ceil(file.size / chunkSize) });

    const reader = new FileReader();
    let offset = 0;
    const signal = abortController.signal;

    try {
        while (offset < file.size && !signal.aborted) {
            await waitForWebSocketBuffer(ws, signal);
            if (signal.aborted) break;

            const end = Math.min(offset + chunkSize, file.size);
            const slice = file.slice(offset, end);

            const chunk = await readChunkAsArrayBuffer(reader, slice, signal);
            if (signal.aborted || !chunk) break;

            ws.send(chunk);
            offset += chunk.byteLength;

            const progress = offset / file.size;
            log.debug('Chunk sent:', { offset, progress: `${Math.round(progress * 100)}%`, bufferedAmount: ws.bufferedAmount });
            updateProgress(elements, progress);
        }

        if (!signal.aborted && offset >= file.size) {
            log.info('Upload completed successfully');
            uploadState.isUploading = false;
            finalizeTransfer(ws, elements.statusText, uploadState);
        }
    } catch (error) {
        if (!signal.aborted) {
            log.error('Upload failed:', error);
            elements.statusText.textContent = `Error: ${error.message || 'Upload failed'}`;
            ws.close();
        }
    } finally {
        reader.onload = null;
        reader.onerror = null;
    }
}

function readChunkAsArrayBuffer(reader, blob, signal) {
    return new Promise((resolve, reject) => {
        if (signal.aborted) return resolve(null);

        reader.onload = e => resolve(e.target.result);
        reader.onerror = () => reject(new Error('Error reading file'));

        signal.addEventListener('abort', () => {
            reader.abort();
            resolve(null);
        }, { once: true });

        reader.readAsArrayBuffer(blob);
    });
}

function waitForWebSocketBuffer(ws, signal) {
    return new Promise(resolve => {
        const threshold = isMobileDevice() ? BUFFER_THRESHOLD_MOBILE : BUFFER_THRESHOLD_DESKTOP;
        const checkBuffer = () => {
            if (signal.aborted || ws.bufferedAmount < threshold) {
                resolve();
            } else {
                setTimeout(checkBuffer, BUFFER_CHECK_INTERVAL);
            }
        };
        checkBuffer();
    });
}

function finalizeTransfer(ws, statusText, uploadState) {
    log.info('Sending end-of-transfer signal');
    ws.send(new ArrayBuffer(0));

    setTimeout(() => {
        log.info('Transfer finalized successfully');
        statusText.textContent = '✓ Transfer complete!';
        if (uploadState.wakeLock) {
            uploadState.wakeLock.release().catch(() => {});
            uploadState.wakeLock = null;
        }
        ws.close();
    }, TRANSFER_FINALIZE_DELAY);
}

function cleanupTransfer(abortController, uploadState) {
    if (abortController) {
        abortController.abort();
    }
    if (uploadState && uploadState.wakeLock) {
        uploadState.wakeLock.release().catch(() => {});
        uploadState.wakeLock = null;
    }
}

function isMobileDevice() {
    return /Android|webOS|iPhone|iPad|iPod|BlackBerry|IEMobile|Opera Mini/i.test(navigator.userAgent) ||
           (window.matchMedia && window.matchMedia(`(max-width: ${MOBILE_BREAKPOINT}px)`).matches);
}

function generateTransferId() {
    const uuid = self.crypto.randomUUID();
    const hex = uuid.replace(/-/g, '');
    const consonants = 'bcdfghjklmnpqrstvwxyz';
    const vowels = 'aeiou';

    const createWord = (hexSegment) => {
        let word = '';
        for (let i = 0; i < hexSegment.length; i++) {
            const charCode = parseInt(hexSegment[i], 16);
            word += (i % 2 === 0) ? consonants[charCode % consonants.length] : vowels[charCode % vowels.length];
        }
        return word;
    };

    const word1 = createWord(hex.substring(0, 6));
    const word2 = createWord(hex.substring(6, 12));
    const num = parseInt(hex.substring(12, 15), 16) % TRANSFER_ID_MAX_NUMBER;

    const transferId = `${word1}-${word2}-${num}`;
    log.debug('Generated transfer ID:', transferId);
    return transferId;
}

async function requestWakeLock(uploadState) {
    try {
        uploadState.wakeLock = await navigator.wakeLock.request('screen');
        log.info('Wake lock acquired to prevent screen sleep');
        uploadState.wakeLock.addEventListener('release', () => log.debug('Wake lock released'));
    } catch (err) {
        log.warn('Wake lock request failed:', err.message);
    }
}
