// EDIT THIS after you deploy the backend (Render/Railway URL, no trailing slash)
const API_BASE = 'https://video-upload-app-95q4.onrender.com';
const IG_LIMIT = 2200;
const $ = (id) => document.getElementById(id);
let selectedFile = null;
let status = { youtube: null, instagram: null }; // null = unknown

// --- Service worker registration ---
if ('serviceWorker' in navigator) navigator.serviceWorker.register('service-worker.js');

// --- Videos shared in via Android's share sheet (stored by the service worker) ---
function openDb() {
  return new Promise((resolve, reject) => {
    const req = indexedDB.open('vidpub', 1);
    req.onupgradeneeded = () => req.result.createObjectStore('pending-share');
    req.onsuccess = () => resolve(req.result);
    req.onerror = () => reject(req.error);
  });
}
async function loadSharedVideoIfAny() {
  if (new URLSearchParams(location.search).get('shared') !== '1') return;
  const db = await openDb();
  const store = db.transaction('pending-share', 'readwrite').objectStore('pending-share');
  const req = store.get('latest');
  req.onsuccess = () => {
    const rec = req.result;
    if (rec && rec.file) {
      setSelectedFile(rec.file);
      if (rec.text) $('caption-main').value = rec.text;
      refresh();
    }
    store.delete('latest');
  };
}

function setSelectedFile(file) {
  selectedFile = file;
  const v = $('preview');
  v.src = URL.createObjectURL(file);
  v.hidden = false;
  $('frame').classList.add('has');
  $('frame').querySelector('.drop').hidden = true;
  $('file-meta').textContent = `${file.name} · ${(file.size / 1048576).toFixed(1)} MB`;
  $('pick-btn').textContent = 'Choose a different video';
  refresh();
}

// --- Form state ---
function refresh() {
  const main = $('caption-main').value;
  const split = $('split-caption').checked;
  const ig = split ? $('caption-ig').value : main;
  $('count-main').textContent = main.length;
  $('ig-caption-wrap').hidden = !split;
  $('count-ig').textContent = `${$('caption-ig').value.length}/${IG_LIMIT}`;
  $('count-ig').classList.toggle('over', $('caption-ig').value.length > IG_LIMIT);
  $('yt-title').textContent = main.split('\n')[0].trim() ? main.trim().slice(0, 100) : 'New video';
  $('yt-title-hint').hidden = !$('platform-yt').checked;
  const anyPlatform = $('platform-yt').checked || $('platform-ig').checked;
  const igTooLong = $('platform-ig').checked && ig.length > IG_LIMIT;
  $('publish-btn').disabled = !(selectedFile && anyPlatform) || igTooLong;
}
['caption-main', 'caption-ig', 'split-caption', 'platform-yt', 'platform-ig'].forEach((id) =>
  $(id).addEventListener('input', refresh));
$('pick-btn').addEventListener('click', () => $('file-input').click());
$('file-input').addEventListener('change', (e) => e.target.files[0] && setSelectedFile(e.target.files[0]));

// --- Account status ---
function showStatus(key, prefix, label) {
  const on = status[key], el = $(`${prefix}-status`), btn = $(`${prefix}-connect-btn`);
  el.textContent = on === null ? 'Server not reachable. It may be waking up.' : on ? 'Connected' : 'Not connected';
  el.classList.toggle('on', on === true);
  btn.textContent = on ? 'Reconnect' : 'Connect';
  btn.disabled = on === null;
  const cb = $(`platform-${prefix === 'yt' ? 'yt' : 'ig'}`), lbl = $(`lbl-${prefix}`);
  lbl.classList.toggle('off', on === false);
  cb.disabled = on === false;
  if (on === false) cb.checked = false;
}
async function refreshConnectStatus() {
  try {
    const data = await (await fetch(`${API_BASE}/api/status`)).json();
    status = { youtube: !!data.youtube_connected, instagram: !!data.instagram_connected };
  } catch (e) {
    status = { youtube: null, instagram: null };
  }
  showStatus('youtube', 'yt'); showStatus('instagram', 'ig');
  refresh();
}
$('yt-connect-btn').addEventListener('click', () => (location.href = `${API_BASE}/auth/youtube`));
$('ig-connect-btn').addEventListener('click', () => (location.href = `${API_BASE}/auth/instagram`));

// --- Publish (XHR so we can show real upload progress) ---
$('publish-btn').addEventListener('click', () => {
  if (!selectedFile) return;
  const btn = $('publish-btn'), st = $('publish-status');
  const yt = $('platform-yt').checked, ig = $('platform-ig').checked;
  const form = new FormData();
  form.append('video', selectedFile);
  form.append('caption_main', $('caption-main').value);
  form.append('caption_ig', $('split-caption').checked ? $('caption-ig').value : '');
  form.append('post_youtube', yt);
  form.append('post_instagram', ig);

  btn.disabled = true; $('results').innerHTML = '';
  $('bar-wrap').hidden = false; $('bar').style.width = '0';
  const fail = (m) => { $('bar-wrap').hidden = true; st.textContent = m; refresh(); };

  const xhr = new XMLHttpRequest();
  xhr.open('POST', `${API_BASE}/api/upload`);
  xhr.upload.onprogress = (e) => {
    if (!e.lengthComputable) return;
    const p = Math.round((e.loaded / e.total) * 100);
    $('bar').style.width = p + '%';
    st.textContent = p < 100 ? `Uploading ${p}%` : 'Upload finished. Waiting for the platforms to publish. This can take a minute or two.';
  };
  xhr.onerror = () => fail('Upload failed. Check your connection and try again.');
  xhr.onload = () => {
    let data = {};
    try { data = JSON.parse(xhr.responseText); } catch (e) {}
    if (!data.results) return fail(`The server returned an error (${xhr.status}).`);
    $('bar-wrap').hidden = true; st.textContent = '';
    const rows = [['youtube', 'YouTube', yt], ['instagram', 'Instagram', ig]].filter((r) => r[2]);
    $('results').innerHTML = rows.map(([k, name]) => {
      const r = data.results[k];
      return r === 'ok'
        ? `<li><span>${name}</span><span class="ok">Published</span></li>`
        : `<li><span>${name}</span><span class="bad">${String(typeof r === 'string' ? r : JSON.stringify(r) || 'No response').replace(/[<>&]/g, '')}</span></li>`;
    }).join('');
    refresh();
  };
  xhr.send(form);
});

loadSharedVideoIfAny();
refreshConnectStatus();
refresh();