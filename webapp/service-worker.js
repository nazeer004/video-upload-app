const DB_NAME = 'vidpub';
const STORE_NAME = 'pending-share';

function openDb() {
  return new Promise((resolve, reject) => {
    const req = indexedDB.open(DB_NAME, 1);
    req.onupgradeneeded = () => {
      req.result.createObjectStore(STORE_NAME);
    };
    req.onsuccess = () => resolve(req.result);
    req.onerror = () => reject(req.error);
  });
}

async function storeSharedVideo(file, text) {
  const db = await openDb();
  return new Promise((resolve, reject) => {
    const tx = db.transaction(STORE_NAME, 'readwrite');
    tx.objectStore(STORE_NAME).put({ file, text, ts: Date.now() }, 'latest');
    tx.oncomplete = () => resolve();
    tx.onerror = () => reject(tx.error);
  });
}

self.addEventListener('install', () => self.skipWaiting());
self.addEventListener('activate', (e) => e.waitUntil(self.clients.claim()));

// Handles Android's share sheet POSTing a video file straight into the PWA.
self.addEventListener('fetch', (event) => {
  const url = new URL(event.request.url);
  if (event.request.method === 'POST' && url.pathname === '/share-target') {
    event.respondWith((async () => {
      const formData = await event.request.formData();
      const file = formData.get('video');
      const text = formData.get('text') || formData.get('title') || '';
      if (file) {
        await storeSharedVideo(file, text);
      }
      // Send the user into the app; index.html checks IndexedDB on load.
      return Response.redirect('/index.html?shared=1', 303);
    })());
  }
});
