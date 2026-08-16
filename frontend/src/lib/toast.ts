import { toast } from 'sonner'

export function notifyOk(text: string) {
  toast.success(text)
}

export function notifyBad(text: string) {
  toast.error(text)
}

export function notifyInfo(text: string) {
  toast(text)
}
