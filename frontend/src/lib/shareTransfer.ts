const SHARE_READY_MESSAGE = 'gateway-share-ready'
const SHARE_KEY_MESSAGE = 'gateway-share-key'

export function openShareWithApiKey(apiKey: string) {
  const shareWindow = window.open('/share', '_blank')
  if (!shareWindow) return false
  const targetWindow = shareWindow

  function onMessage(event: MessageEvent) {
    if (event.origin !== window.location.origin || event.source !== targetWindow) return
    if (event.data?.type !== SHARE_READY_MESSAGE) return
    targetWindow.postMessage({ type: SHARE_KEY_MESSAGE, apiKey }, window.location.origin)
    window.removeEventListener('message', onMessage)
  }

  window.addEventListener('message', onMessage)
  window.setTimeout(() => window.removeEventListener('message', onMessage), 10000)
  return true
}

export function listenForShareApiKey(onApiKey: (apiKey: string) => void) {
  function onMessage(event: MessageEvent) {
    if (event.origin !== window.location.origin || event.source !== window.opener) return
    if (event.data?.type !== SHARE_KEY_MESSAGE || typeof event.data.apiKey !== 'string') return
    onApiKey(event.data.apiKey)
    window.removeEventListener('message', onMessage)
  }

  window.addEventListener('message', onMessage)
  window.opener?.postMessage({ type: SHARE_READY_MESSAGE }, window.location.origin)
  return () => window.removeEventListener('message', onMessage)
}
