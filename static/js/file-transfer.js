const TRANSFER_ID_MAX_NUMBER = 1000;
const CHUNK_SIZE_MOBILE = 32 * 1024;
const CHUNK_SIZE_DESKTOP = 64 * 1024;
const BUFFER_THRESHOLD_MOBILE = CHUNK_SIZE_MOBILE * 16;
const BUFFER_THRESHOLD_DESKTOP = CHUNK_SIZE_DESKTOP * 16;
const MAX_HASH_SAMPLING = 2 * 1024**2;
const DEBUG_LOGS = false;

const log = {
    debug: (...args) => DEBUG_LOGS && console.debug(...args),
    info: (...args) => console.info(...args),
    warn: (...args) => console.warn(...args),
    error: (...args) => console.error(...args)
};

initFileTransfer();

function initFileTransfer() {
    log.debug('Initializing file transfer interface');
    const elements = getUIElements();

    if (isMobileDevice() && elements.dropAreaText) {
        elements.dropAreaText.textContent = 'Tap here to select a file';
        log.debug('Updated UI text for mobile device');
    }

    setupEventListeners(elements);
    log.debug('Event listeners setup complete');
}

function getUIElements() {
    return {
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
}

function setupEventListeners(elements) {
    const { dropArea, fileInput } = elements;

    const dragEvents = ['dragenter', 'dragover', 'dragleave', 'drop'];
    dragEvents.forEach(eventName => {
        dropArea.addEventListener(eventName, preventDefaults);
        document.body.addEventListener(eventName, preventDefaults);
    });

    const highlightEvents = ['dragenter', 'dragover'];
    const unhighlightEvents = ['dragleave', 'drop'];

    highlightEvents.forEach(eventName => {
        dropArea.addEventListener(eventName, () => dropArea.classList.add('highlight'));
    });

    unhighlightEvents.forEach(eventName => {
        dropArea.addEventListener(eventName, () => dropArea.classList.remove('highlight'));
    });

    dropArea.addEventListener('drop', e => handleDrop(e, elements));
    dropArea.addEventListener('click', () => fileInput.click());
    fileInput.addEventListener('change', async () => {
        if (fileInput.files.length) {
            await handleFiles(fileInput.files, elements);
        }
    });
}

function preventDefaults(e) {
    e.preventDefault();
    e.stopPropagation();
}

async function handleDrop(e, elements) {
    const files = e.dataTransfer.files;
    await handleFiles(files, elements);
}

async function handleFiles(files, elements) {
    if (files.length > 0) {
        const file = files[0];
        try {
            log.info('File selected:', {
                name: file.name,
                size: file.size,
                type: file.type,
                lastModified: new Date(file.lastModified).toISOString()
            });
            await uploadFile(file, elements);
        } catch (error) {
            log.error('Failed to handle file:', error);
        }
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

function saveUploadProgress(key, bytesUploaded, transferId) {
    try {
        const progress = { bytesUploaded, transferId, timestamp: Date.now() };
        localStorage.setItem(key, JSON.stringify(progress));
        log.debug('Progress saved:', progress);
    } catch (e) {
        log.warn('Failed to save progress:', e);
    }
}

function getUploadProgress(key) {
    try {
        const saved = localStorage.getItem(key);
        if (!saved) return null;

        const progress = JSON.parse(saved);
        const lastHour = Date.now() - 3600 * 1000;

        if (progress?.timestamp >= lastHour) {
            log.debug('Loaded saved progress:', progress);
            return progress;
        }

        localStorage.removeItem(key);
    } catch (e) {
        log.warn('Failed to load progress:', e);
        localStorage.removeItem(key);
    }
    return null;
}

function clearUploadProgress(key) {
    try {
        localStorage.removeItem(key);
        log.debug('Progress cleared');
    } catch (e) {
        log.warn('Failed to clear progress:', e);
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
    }, 300);
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

    } else if (event.data.startsWith('Resume from:')) {
        const resumeBytes = parseInt(event.data.split(':')[1].trim());
        log.info('Resuming from byte:', resumeBytes);
        elements.statusText.textContent = `Resuming transfer from ${Math.round(resumeBytes / file.size * 100)}%...`;
        uploadState.isUploading = true;
        uploadState.resumePosition = resumeBytes;
        sendFileInChunks(ws, file, elements, abortController, uploadState);

    } else if (event.data.startsWith('Error')) {
        log.error('Server error:', event.data);
        elements.statusText.textContent = event.data;
        elements.statusText.style.color = 'var(--error)';
        clearUploadProgress(uploadState.uploadKey);
        cleanupTransfer(abortController, uploadState, ws);

    } else {
        log.warn('Unexpected message:', event.data);
    }
}

function handleWsError(error, statusText) {
    log.error('WebSocket error:', error);
    statusText.textContent = 'Error: ' + (error.message || 'Connection failed');
    statusText.style.color = 'var(--error)';
}

function isMobileDevice() {
    return /Android|webOS|iPhone|iPad|iPod|BlackBerry|IEMobile|Opera Mini/i.test(navigator.userAgent) ||
        (window.matchMedia && window.matchMedia(`(max-width: ${768}px)`).matches);
}

async function requestWakeLock(uploadState) {
    try {
        uploadState.wakeLock = await navigator.wakeLock.request('screen');
        log.info('Wake lock acquired to prevent screen sleep');

        uploadState.wakeLock.addEventListener('release', () => {
            log.debug('Wake lock released');
            uploadState.wakeLock = null;
        });
    } catch (err) {
        log.warn('Wake lock request failed:', err.message);
        uploadState.wakeLock = null;
    }
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

async function calculateFileHash(file) {
    const sampleSize = Math.min(file.size, MAX_HASH_SAMPLING);
    const chunkSize = isMobileDevice() ? CHUNK_SIZE_MOBILE : CHUNK_SIZE_DESKTOP;
    let hash = 0;

    try {
        for (let offset = 0; offset < sampleSize; offset += chunkSize) {
            const end = Math.min(offset + chunkSize, sampleSize);
            const slice = file.slice(offset, end);
            const arrayBuffer = await readFileSlice(slice);
            const chunk = new Uint8Array(arrayBuffer);

            for (let i = 0; i < chunk.length; i++) {
                hash = ((hash ^ chunk[i]) * 16777619) >>> 0;
            }
        }

        hash = hash ^ file.size ^ simpleStringHash(file.name);
        return Math.abs(hash).toString(16);
    } catch (error) {
        log.warn('File hashing error:', error);
        return Math.floor(Math.random() * 0xFFFFFFFF).toString(16);
    }
}

function readFileSlice(slice) {
    return new Promise((resolve, reject) => {
        const reader = new FileReader();
        reader.onload = e => resolve(e.target.result);
        reader.onerror = () => reject(new Error('Failed to read file chunk'));
        reader.readAsArrayBuffer(slice);
    });
}

function simpleStringHash(str) {
    let hash = 0;
    for (let i = 0; i < str.length; i++) {
        const char = str.charCodeAt(i);
        hash = ((hash << 5) - hash) + char;
        hash = hash >>> 0; // Convert to 32-bit unsigned
    }
    return hash;
}

async function uploadFile(file, elements) {
    const fileHash = await calculateFileHash(file);
    const uploadKey = `upload_${fileHash}`;
    const savedProgress = getUploadProgress(uploadKey);
    const isResume = savedProgress?.bytesUploaded > 0;
    const transferId = savedProgress?.transferId || generateTransferId();

    const wsProtocol = window.location.protocol === 'https:' ? 'wss:' : 'ws:';
    const endpoint = isResume ? 'resume' : 'send';
    const wsUrl = `${wsProtocol}//${window.location.host}/${endpoint}/${transferId}`;

    log.info(isResume ? 'Resuming upload:' : 'Starting upload:', {
        transferId,
        fileName: file.name,
        fileSize: file.size,
        wsUrl,
        resumeFrom: savedProgress?.bytesUploaded || 0
    });

    try {
        const uploadState = createUploadState(file, transferId, uploadKey);
        const { ws, abortController } = createWebSocketConnection(wsUrl, file, elements, uploadState);
        setupPageEventListeners(uploadState, elements, ws, abortController);
        showProgress(elements);

        if (isMobileDevice() && 'wakeLock' in navigator) {
            await requestWakeLock(uploadState);
        }
    } catch (error) {
        log.error('Failed to initialize upload:', error);
        elements.statusText.textContent = 'Error: Failed to start upload';
        elements.statusText.style.color = 'var(--error)';
    }
}

function createUploadState(file, transferId, uploadKey) {
    return {
        file,
        transferId,
        isUploading: false,
        wakeLock: null,
        uploadKey,
        resumePosition: 0
    };
}

function createWebSocketConnection(wsUrl, file, elements, uploadState) {
    const ws = new WebSocket(wsUrl);
    const abortController = new AbortController();

    ws.onopen = () => handleWsOpen(ws, file, uploadState.transferId, elements);
    ws.onmessage = (event) => handleWsMessage(event, ws, file, elements, abortController, uploadState);
    ws.onerror = (error) => handleWsError(error, elements.statusText);
    ws.onclose = (event) => handleWsClose(event, elements, uploadState, abortController);

    return { ws, abortController };
}

function handleWsClose(event, elements, uploadState, abortController) {
    log.info('WebSocket connection closed:', {
        code: event.code,
        reason: event.reason,
        wasClean: event.wasClean
    });

    if (uploadState.isUploading && !event.wasClean) {
        elements.statusText.textContent = 'Connection lost. Please try uploading again.';
        elements.statusText.style.color = 'var(--error)';
    }
    cleanupTransfer(abortController, uploadState);
}

function setupPageEventListeners(uploadState, elements, ws, abortController) {
    const handleVisibilityChange = createVisibilityHandler(uploadState, elements, ws);
    const handleBeforeUnload = createBeforeUnloadHandler(uploadState);
    const handleUnload = createUnloadHandler(handleVisibilityChange, handleBeforeUnload, abortController, uploadState);

    document.addEventListener('visibilitychange', handleVisibilityChange);
    window.addEventListener('beforeunload', handleBeforeUnload);
    window.addEventListener('unload', handleUnload, { once: true });
}

function createVisibilityHandler(uploadState, elements, ws) {
    return () => {
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
}

function createBeforeUnloadHandler(uploadState) {
    return (e) => {
        if (uploadState.isUploading) {
            e.preventDefault();
            e.returnValue = 'File upload in progress. Are you sure you want to leave?';
            return e.returnValue;
        }
    };
}

function createUnloadHandler(visibilityHandler, beforeUnloadHandler, abortController, uploadState) {
    return () => {
        document.removeEventListener('visibilitychange', visibilityHandler);
        window.removeEventListener('beforeunload', beforeUnloadHandler);
        cleanupTransfer(abortController, uploadState);
    };
}

async function sendFileInChunks(ws, file, elements, abortController, uploadState) {
    const chunkSize = isMobileDevice() ? CHUNK_SIZE_MOBILE : CHUNK_SIZE_DESKTOP;
    const startOffset = uploadState.resumePosition || 0;

    log.info('Starting chunked upload:', {
        chunkSize,
        fileSize: file.size,
        startOffset,
        totalChunks: Math.ceil((file.size - startOffset) / chunkSize)
    });

    const signal = abortController.signal;

    try {
        const bytesUploaded = await streamFileChunks(
            ws, file, signal, startOffset, chunkSize, elements, uploadState
        );

        if (!signal.aborted && bytesUploaded >= file.size) {
            handleUploadSuccess(ws, elements, uploadState);
        }
    } catch (error) {
        handleUploadError(error, signal, elements, ws);
    }
}

async function streamFileChunks(ws, file, signal, startOffset, chunkSize, elements, uploadState) {
    let offset = startOffset;

    while (offset < file.size && !signal.aborted) {
        await waitForWebSocketBuffer(ws, signal);
        if (signal.aborted) break;

        const chunk = await readNextChunk(file, offset, chunkSize, signal);
        if (signal.aborted || !chunk) break;

        ws.send(chunk);
        offset += chunk.byteLength;

        updateUploadProgress(offset, file.size, elements, uploadState);
    }

    return offset;
}

function readNextChunk(file, offset, chunkSize, signal) {
    if (signal.aborted) return null;

    const end = Math.min(offset + chunkSize, file.size);
    const slice = file.slice(offset, end);
    return readChunkAsArrayBuffer(slice, signal);
}

function updateUploadProgress(offset, fileSize, elements, uploadState) {
    const progress = offset / fileSize;
    log.debug('Chunk sent:', {
        offset,
        progress: `${Math.round(progress * 100)}%`
    });
    updateProgress(elements, progress);

    if (offset % (256 * 1024) === 0 || offset === fileSize) {
        saveUploadProgress(uploadState.uploadKey, offset, uploadState.transferId);
    }
}

function handleUploadSuccess(ws, elements, uploadState) {
    log.info('Upload completed successfully');
    uploadState.isUploading = false;
    clearUploadProgress(uploadState.uploadKey);
    finalizeTransfer(ws, elements.statusText, uploadState);
}

function handleUploadError(error, signal, elements, ws) {
    if (!signal.aborted) {
        log.error('Upload failed:', error);
        elements.statusText.textContent = `Error: ${error.message || 'Upload failed'}`;
        elements.statusText.style.color = 'var(--error)';
        ws.close();
    }
}

function readChunkAsArrayBuffer(blob, signal) {
    if (signal.aborted) return null;

    return new Promise((resolve, reject) => {
        const reader = new FileReader();

        const cleanup = () => {
            reader.onload = null;
            reader.onerror = null;
        };

        const handleAbort = () => {
            reader.abort();
            cleanup();
            resolve(null);
        };

        signal.addEventListener('abort', handleAbort, { once: true });

        reader.onload = (e) => {
            signal.removeEventListener('abort', handleAbort);
            cleanup();
            resolve(e.target.result);
        };

        reader.onerror = () => {
            signal.removeEventListener('abort', handleAbort);
            cleanup();
            reject(new Error('Error reading file'));
        };

        reader.readAsArrayBuffer(blob);
    });
}

async function waitForWebSocketBuffer(ws, signal) {
    const threshold = isMobileDevice() ? BUFFER_THRESHOLD_MOBILE : BUFFER_THRESHOLD_DESKTOP;

    while (!signal.aborted && ws.bufferedAmount >= threshold) {
        await new Promise(resolve => setTimeout(resolve, 200));
    }
}

function finalizeTransfer(ws, statusText, uploadState) {
    log.info('Sending end-of-transfer signal');
    ws.send(new ArrayBuffer(0));

    setTimeout(() => {
        log.info('Transfer finalized successfully');
        statusText.textContent = '✓ Transfer complete!';
        releaseWakeLock(uploadState);
        ws.close();
    }, 300);
}

function cleanupTransfer(abortController, uploadState) {
    abortController?.abort();
    releaseWakeLock(uploadState);
}

function releaseWakeLock(uploadState) {
    if (uploadState?.wakeLock) {
        uploadState.wakeLock.release().catch(() => {});
        uploadState.wakeLock = null;
    }
}
