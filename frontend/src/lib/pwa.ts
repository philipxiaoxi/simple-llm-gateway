const UPDATE_QUERY = 'v'

export async function forceAppUpdate() {
  try {
    if ('serviceWorker' in navigator) {
      const registrations = await navigator.serviceWorker.getRegistrations()
      await Promise.all(registrations.map((registration) => registration.unregister()))
    }
    if ('caches' in window) {
      const cacheNames = await caches.keys()
      await Promise.all(cacheNames.map((cacheName) => caches.delete(cacheName)))
    }
  } finally {
    const nextUrl = new URL(window.location.href)
    nextUrl.searchParams.set(UPDATE_QUERY, String(Date.now()))
    window.location.replace(nextUrl.href)
  }
}

export function checkServiceWorkerUpdate() {
  if (!('serviceWorker' in navigator)) return
  void navigator.serviceWorker.getRegistration().then((registration) => {
    void registration?.update()
  })
}
