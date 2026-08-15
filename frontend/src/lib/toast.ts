import { toast } from 'sonner'

const TOAST_ID = 'gateway-notice'

export function notifyOk(text: string) {
  toast.success(text, { id: TOAST_ID })
}

export function notifyBad(text: string) {
  toast.error(text, { id: TOAST_ID })
}

export function notifyInfo(text: string) {
  toast(text, { id: TOAST_ID })
}
