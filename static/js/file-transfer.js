document.addEventListener('DOMContentLoaded', initFileTransfer);

const DEBUG = true; // Enable debug logging

const log = {
    debug: (...args) => DEBUG && console.log('[Transit.sh]', ...args),
    info: (...args) => console.info('[Transit.sh]', ...args),
    warn: (...args) => console.warn('[Transit.sh]', ...args),
    error: (...args) => console.error('[Transit.sh]', ...args)
};

const isMobileDevice = (() => {
    let cachedResult = null;
    let lastWindowWidth = null;

    return () => {
        // Check if window dimensions changed, which might affect mobile detection
        const currentWindowWidth = window.innerWidth;
        if (cachedResult !== null && lastWindowWidth === currentWindowWidth) {
            return cachedResult;
        }
        lastWindowWidth = currentWindowWidth;

        const isMobile = /Android|webOS|iPhone|iPad|iPod|BlackBerry|IEMobile|Opera Mini/i.test(navigator.userAgent) ||
               (window.matchMedia && window.matchMedia('(max-width: 768px)').matches);

        if (cachedResult === null || cachedResult !== isMobile) {
            log.debug('Device type:', isMobile ? 'Mobile' : 'Desktop', 'User Agent:', navigator.userAgent);
        }
        cachedResult = isMobile;
        return isMobile;
    };
})();

function initFileTransfer() {
    log.info('Initializing file transfer interface');

    const elements = {
        dropArea: document.getElementById('drop-area'),
        dropAreaText: document.getElementById('drop-area-text'),
        fileInput: document.getElementById('file-input'),
        uploadProgress: document.getElementById('upload-progress'),
        progressBarFill: document.getElementById('progress-bar-fill'),
        progressText: document.getElementById('progress-text'),
        statusText: document.getElementById('status-text'),
        shareLink: document.getElementById('share-link'),
        shareUrl: document.getElementById('share-url'),
    };

    // Update text for mobile devices
    if (isMobileDevice() && elements.dropAreaText) {
        elements.dropAreaText.textContent = 'Tap here to select a file';
        log.debug('Updated UI text for mobile device');
    }

    setupEventListeners(elements);
    log.debug('Event listeners setup complete');
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

// Transfer ID generation
function generateTransferId() {
    // Generate a UUID to get a high-entropy random value.
    const uuid = self.crypto.randomUUID();
    log.debug('Generated UUID:', uuid);
    const hex = uuid.replace(/-/g, ''); // We only need the hex digits

    const consonants = 'bcdfghjklmnpqrstvwxyz';
    const vowels = 'aeiou';

    // Function to create a pronounceable "word" from a hex string segment.
    const createWord = (hexSegment) => {
        let word = '';
        for (let i = 0; i < hexSegment.length; i++) {
            const charCode = parseInt(hexSegment[i], 16);
            if (i % 2 === 0) { // Consonant
                word += consonants[charCode % consonants.length];
            } else { // Vowel
                word += vowels[charCode % vowels.length];
            }
        }
        return word;
    };

    // Create two 6-letter words from the first 12 characters of the UUID hex.
    const word1 = createWord(hex.substring(0, 6));
    const word2 = createWord(hex.substring(6, 12));

    // Use the next 4 hex characters for a number between 0 and 999.
    // This gives a larger range than the original 0-99.
    const num = parseInt(hex.substring(12, 15), 16) % 1000;

    const transferId = `${word1}-${word2}-${num}`;
    log.debug('Generated transfer ID:', transferId);
    return transferId;
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
    shareUrl.value = `https://transit-sh.fly.dev/${transferId}`;
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
    const wsUrl = `wss://transit-sh.fly.dev/send/${transferId}`;
    log.info('Starting upload:', {
        transferId: transferId,
        fileName: file.name,
        fileSize: file.size,
        wsUrl: wsUrl
    });

    let ws = new WebSocket(wsUrl);
    let abortController = new AbortController();
    let uploadState = {
        file: file,
        transferId: transferId,
        isUploading: false,
        wakeLock: null
    };

    showProgress(elements);

    // WebSocket event handlers
    ws.onopen = () => {
        log.info('WebSocket connection opened');
        handleWsOpen(ws, file, transferId, elements, abortController, uploadState);
    };
    ws.onmessage = (event) => {
        log.debug('WebSocket message received:', event.data);
        handleWsMessage(event, ws, file, elements, abortController, uploadState);
    };
    ws.onerror = (error) => {
        log.error('WebSocket error:', error);
        handleWsError(error, statusText, uploadState);
        cleanupTransfer(abortController, uploadState);
    };
    ws.onclose = (event) => {
        log.info('WebSocket connection closed:', {
            code: event.code,
            reason: event.reason,
            wasClean: event.wasClean
        });

        if (uploadState.isUploading && !event.wasClean) {
            statusText.textContent = 'Connection lost. Please try uploading again.';
            statusText.style.color = 'var(--error)';
        }

        cleanupTransfer(abortController, uploadState);
    };

    // Handle page visibility changes - warn mobile users
    const handleVisibilityChange = () => {
        if (document.hidden && uploadState.isUploading) {
            log.warn('App went to background during active upload');
            // Mobile browsers may kill the connection when backgrounded
            if (isMobileDevice()) {
                // Will show when user returns
                statusText.textContent = '⚠️ Keep app in foreground during upload';
                statusText.style.color = 'var(--warning)';
            }
        } else if (!document.hidden && uploadState.isUploading) {
            log.info('App returned to foreground');
            // Check if connection is still alive
            if (ws.readyState !== WebSocket.OPEN) {
                statusText.textContent = 'Connection lost. Please try uploading again.';
                statusText.style.color = 'var(--error)';
                uploadState.isUploading = false;
            }
        }
    };

    document.addEventListener('visibilitychange', handleVisibilityChange);

    // Warn user before closing/refreshing during upload
    const handleBeforeUnload = (e) => {
        if (uploadState.isUploading) {
            const message = 'File upload in progress. Are you sure you want to leave?';
            e.preventDefault();
            e.returnValue = message;
            return message;
        }
    };

    window.addEventListener('beforeunload', handleBeforeUnload);

    // Cleanup on actual unload
    window.addEventListener('unload', () => {
        document.removeEventListener('visibilitychange', handleVisibilityChange);
        window.removeEventListener('beforeunload', handleBeforeUnload);
        cleanupTransfer(abortController, uploadState);
    }, { once: true });

    // Request wake lock for mobile devices during upload
    if (isMobileDevice() && 'wakeLock' in navigator) {
        requestWakeLock(uploadState);
    }

    return { ws, uploadState, abortController };
}

async function requestWakeLock(uploadState) {
    try {
        uploadState.wakeLock = await navigator.wakeLock.request('screen');
        log.info('Wake lock acquired to prevent screen sleep');

        uploadState.wakeLock.addEventListener('release', () => {
            log.info('Wake lock released');
        });
    } catch (err) {
        log.warn('Wake lock request failed:', err.message);
    }
}

function handleWsOpen(ws, file, transferId, elements, abortController, uploadState) {
    const { statusText } = elements;

    const metadata = {
        file_name: file.name,
        file_size: file.size,
        file_type: file.type || 'application/octet-stream'
    };

    log.info('Sending file metadata:', metadata);
    ws.send(JSON.stringify(metadata));
    statusText.textContent = 'Waiting for the receiver to start the download... (max. 5 minutes)';
    displayShareLink(elements, transferId);
}

function handleWsMessage(event, ws, file, elements, abortController, uploadState) {
    const { statusText } = elements;
    if (event.data === 'Go for file chunks') {
        log.info('Receiver connected, starting file transfer');
        statusText.textContent = 'Peer connected. Transferring file...';
        uploadState.isUploading = true;
        sendFileInChunks(ws, file, elements, abortController, uploadState);
    } else if (event.data.startsWith('Error')) {
        log.error('Server error:', event.data);
        statusText.textContent = event.data;
        statusText.style.color = 'var(--error)';
        cleanupTransfer(abortController, uploadState);
    } else {
        log.warn('Unexpected message:', event.data);
    }
}

function handleWsError(error, statusText, uploadState) {
    if (uploadState && uploadState.wasInBackground) {
        log.warn('Connection interrupted due to background suspension');
        statusText.textContent = 'Connection interrupted. Please keep the browser open.';
    } else {
        log.error('WebSocket error occurred:', error);
        statusText.textContent = 'Error: ' + (error.message || 'Connection failed');
    }
    statusText.style.color = 'var(--error)';
}

function cleanupTransfer(abortController, uploadState) {
    if (abortController) {
        abortController.abort();
        abortController = null;
    }

    // Release wake lock if held
    if (uploadState && uploadState.wakeLock) {
        uploadState.wakeLock.release().catch(() => {});
        uploadState.wakeLock = null;
    }
}

async function sendFileInChunks(ws, file, elements, abortController, uploadState) {
    const { statusText } = elements;
    const chunkSize = isMobileDevice() ? 32 * 1024 : 64 * 1024; // Smaller chunks for mobile
    log.info('Starting chunked upload:', {
        chunkSize: chunkSize,
        fileSize: file.size,
        totalChunks: Math.ceil(file.size / chunkSize)
    });

    const reader = new FileReader();
    let offset = 0;

    const signal = abortController.signal;
    if (signal.aborted) return;

    try {
        while (offset < file.size && !signal.aborted) {
            if (signal.aborted) break;

            // Wait until WebSocket buffer has room
            await waitForWebSocketBuffer(ws);

            if (signal.aborted) break;

            const end = Math.min(offset + chunkSize, file.size);
            const slice = file.slice(offset, end);

            const chunk = await readChunkAsArrayBuffer(reader, slice, signal);
            if (signal.aborted || !chunk) break;

            ws.send(chunk);
            offset += chunk.byteLength;

            const progress = offset / file.size;
            log.debug('Chunk sent:', {
                offset: offset,
                progress: Math.round(progress * 100) + '%',
                bufferedAmount: ws.bufferedAmount
            });

            // Update progress
            updateProgress(elements, progress);
        }

        // If we completed successfully (not aborted), finalize the transfer
        if (!signal.aborted && offset >= file.size) {
            log.info('Upload completed successfully');
            uploadState.isUploading = false;
            finalizeTransfer(ws, statusText, uploadState);
        }
    } catch (error) {
        if (!signal.aborted) {
            log.error('Upload failed:', error);
            statusText.textContent = `Error: ${error.message || 'Upload failed'}`;
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
        const threshold = isMobileDevice() ? 512 * 1024 : 1024 * 1024; // Lower threshold for mobile
        const checkBuffer = () => {
            if (ws.bufferedAmount < threshold) {
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

function finalizeTransfer(ws, statusText, uploadState) {
    // Send empty chunk to signal end of transfer
    log.info('Sending end-of-transfer signal');
    ws.send(new ArrayBuffer(0));

    setTimeout(() => {
        log.info('Transfer finalized successfully');
        statusText.textContent = '✓ Transfer complete!';

        // Release wake lock on completion
        if (uploadState && uploadState.wakeLock) {
            uploadState.wakeLock.release().catch(() => {});
            uploadState.wakeLock = null;
        }

        ws.close();
    }, 500);
}
