// EDIT THIS to your deployed backend URL (no trailing slash)
const API_BASE = 'https://video-upload-app-95q4.onrender.com';

let selectedFile = null;
let ytConnected = false;
let igConnected = false;

// --- Service worker registration ---
if ('serviceWorker' in navigator) {
  navigator.serviceWorker.register('service-worker.js');
}

// --- IndexedDB read (for videos shared in via Android's share sheet) ---
function openDb() {
  return new Promise((resolve, reject) => {
    const req = indexedDB.open('vidpub', 1);
    req.onupgradeneeded = () => req.result.createObjectStore('pending-share');
    req.onsuccess = () => resolve(req.result);
    req.onerror = () => reject(req.error);
  });
}

async function loadSharedVideoIfAny() {
  const params = new URLSearchParams(location.search);
  if (params.get('shared') !== '1') return;
  const db = await openDb();
  const tx = db.transaction('pending-share', 'readwrite');
  const store = tx.objectStore('pending-share');
  const req = store.get('latest');
  req.onsuccess = () => {
    const record = req.result;
    if (record && record.file) {
      setSelectedFile(record.file);
      if (record.text) document.getElementById('caption-main').value = record.text;
    }
    store.delete('latest');
  };
}

function setSelectedFile(file) {
  selectedFile = file;
  document.getElementById('file-name').textContent = file.name;
  const preview = document.getElementById('preview');
  preview.src = URL.createObjectURL(file);
  updatePublishAvailability();
}

document.getElementById('pick-btn').addEventListener('click', () => {
  document.getElementById('file-input').click();
});
document.getElementById('file-input').addEventListener('change', (e) => {
  if (e.target.files[0]) setSelectedFile(e.target.files[0]);
});

document.getElementById('split-caption').addEventListener('change', (e) => {
  document.getElementById('ig-caption-wrap').hidden = !e.target.checked;
});

// --- Connect status: re-checked whenever the app regains focus, so
// coming back from the OAuth tab updates the dots without a manual reload ---
async function refreshConnectStatus() {
  try {
    const res = await fetch(`${API_BASE}/api/status`);
    const data = await res.json();
    ytConnected = !!data.youtube_connected;
    igConnected = !!data.instagram_connected;

    document.getElementById('yt-status').dataset.connected = ytConnected;
    document.getElementById('yt-status-text').textContent = ytConnected ? 'Connected' : 'Not connected';
    document.getElementById('ig-status').dataset.connected = igConnected;
    document.getElementById('ig-status-text').textContent = igConnected ? 'Connected' : 'Not connected';
  } catch (e) {
    console.warn('Could not reach backend for status', e);
  }
  updatePublishAvailability();
}

document.addEventListener('visibilitychange', () => {
  if (document.visibilityState === 'visible') refreshConnectStatus();
});

document.getElementById('yt-connect-btn').addEventListener('click', () => {
  window.location.href = `${API_BASE}/auth/youtube`;
});
document.getElementById('ig-connect-btn').addEventListener('click', () => {
  window.location.href = `${API_BASE}/auth/instagram`;
});

// --- Validation: catch problems before hitting the backend, not after ---
function updatePublishAvailability() {
  const wantYt = document.getElementById('platform-yt').checked;
  const wantIg = document.getElementById('platform-ig').checked;
  const msgEl = document.getElementById('validation-msg');
  const btn = document.getElementById('publish-btn');

  let message = '';
  if (!selectedFile) {
    message = '';
  } else if (!wantYt && !wantIg) {
    message = 'Select at least one platform to publish to.';
  } else if (wantYt && !ytConnected && wantIg && !igConnected) {
    message = 'Connect YouTube and Instagram above before publishing.';
  } else if (wantYt && !ytConnected) {
    message = 'Connect YouTube above before publishing.';
  } else if (wantIg && !igConnected) {
    message = 'Connect Instagram above before publishing.';
  }

  msgEl.textContent = message;
  msgEl.hidden = !message;
  btn.disabled = !selectedFile || !!message;
}

document.getElementById('platform-yt').addEventListener('change', updatePublishAvailability);
document.getElementById('platform-ig').addEventListener('change', updatePublishAvailability);

// --- Publish ---
document.getElementById('publish-btn').addEventListener('click', async () => {
  if (!selectedFile) return;
  const statusEl = document.getElementById('publish-status');
  const btn = document.getElementById('publish-btn');
  btn.disabled = true;
  statusEl.textContent = 'Uploading video…';

  const wantIg = document.getElementById('platform-ig').checked;
  let elapsedTimer = null;
  if (wantIg) {
    // Instagram processes video for up to ~60s after upload before it can
    // publish -- without this the UI looks frozen during that wait.
    let seconds = 0;
    elapsedTimer = setInterval(() => {
      seconds += 1;
      if (seconds > 8) {
        statusEl.textContent = `Instagram is processing the video… (${seconds}s)`;
      }
    }, 1000);
  }

  const form = new FormData();
  form.append('video', selectedFile);
  form.append('caption_main', document.getElementById('caption-main').value);
  const splitCaption = document.getElementById('split-caption').checked;
  form.append('caption_ig', splitCaption ? document.getElementById('caption-ig').value : '');
  form.append('post_youtube', document.getElementById('platform-yt').checked);
  form.append('post_instagram', wantIg);

  try {
    const res = await fetch(`${API_BASE}/api/upload`, { method: 'POST', body: form });
    const data = await res.json();
    clearInterval(elapsedTimer);

    const results = data.results || {};
    const parts = [];
    if ('youtube' in results) parts.push(`YouTube: ${results.youtube === 'ok' ? 'published' : 'failed'}`);
    if ('instagram' in results) parts.push(`Instagram: ${results.instagram === 'ok' ? 'published' : 'failed'}`);
    statusEl.textContent = parts.join(' · ') || 'Done.';

    const allOk = Object.values(results).every((v) => v === 'ok');
    if (allOk) resetForm();
  } catch (e) {
    clearInterval(elapsedTimer);
    statusEl.textContent = 'Failed: ' + e.message;
  } finally {
    btn.disabled = false;
    updatePublishAvailability();
  }
});

function resetForm() {
  selectedFile = null;
  document.getElementById('file-name').textContent = 'No video selected — or share one in from your editor.';
  const preview = document.getElementById('preview');
  preview.removeAttribute('src');
  document.getElementById('caption-main').value = '';
  document.getElementById('caption-ig').value = '';
  document.getElementById('split-caption').checked = false;
  document.getElementById('ig-caption-wrap').hidden = true;
  updatePublishAvailability();
}

loadSharedVideoIfAny();
refreshConnectStatus();
