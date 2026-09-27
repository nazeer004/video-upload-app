// EDIT THIS after you deploy the backend (Render/Railway URL, no trailing slash)
const API_BASE = 'https://YOUR-BACKEND-URL.example.com';

let selectedFile = null;

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
    store.delete('latest'); // consumed, clear it
  };
}

function setSelectedFile(file) {
  selectedFile = file;
  document.getElementById('file-name').textContent = file.name;
  const preview = document.getElementById('preview');
  preview.src = URL.createObjectURL(file);
  preview.style.display = 'block';
  document.getElementById('publish-btn').disabled = false;
}

// --- Manual file picker fallback ---
document.getElementById('pick-btn').addEventListener('click', () => {
  document.getElementById('file-input').click();
});
document.getElementById('file-input').addEventListener('change', (e) => {
  if (e.target.files[0]) setSelectedFile(e.target.files[0]);
});

// --- Per-platform caption toggle ---
document.getElementById('split-caption').addEventListener('change', (e) => {
  document.getElementById('ig-caption-wrap').style.display = e.target.checked ? 'block' : 'none';
});

// --- Connect status ---
async function refreshConnectStatus() {
  try {
    const res = await fetch(`${API_BASE}/api/status`);
    const data = await res.json();
    document.getElementById('yt-status').textContent = data.youtube_connected ? 'Connected' : '';
    document.getElementById('ig-status').textContent = data.instagram_connected ? 'Connected' : '';
  } catch (e) {
    console.warn('Could not reach backend for status', e);
  }
}
document.getElementById('yt-connect-btn').addEventListener('click', () => {
  window.location.href = `${API_BASE}/auth/youtube`;
});
document.getElementById('ig-connect-btn').addEventListener('click', () => {
  window.location.href = `${API_BASE}/auth/instagram`;
});

// --- Publish ---
document.getElementById('publish-btn').addEventListener('click', async () => {
  if (!selectedFile) return;
  const statusEl = document.getElementById('publish-status');
  const btn = document.getElementById('publish-btn');
  btn.disabled = true;
  statusEl.textContent = 'Uploading...';

  const form = new FormData();
  form.append('video', selectedFile);
  form.append('caption_main', document.getElementById('caption-main').value);
  const splitCaption = document.getElementById('split-caption').checked;
  form.append('caption_ig', splitCaption ? document.getElementById('caption-ig').value : '');
  form.append('post_youtube', document.getElementById('platform-yt').checked);
  form.append('post_instagram', document.getElementById('platform-ig').checked);

  try {
    const res = await fetch(`${API_BASE}/api/upload`, { method: 'POST', body: form });
    const data = await res.json();
    statusEl.textContent = data.message || 'Done.';
  } catch (e) {
    statusEl.textContent = 'Failed: ' + e.message;
  } finally {
    btn.disabled = false;
  }
});

loadSharedVideoIfAny();
refreshConnectStatus();
