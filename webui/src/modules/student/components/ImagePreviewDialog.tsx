import * as Dialog from "@radix-ui/react-dialog";
import { X } from "lucide-react";

export interface ImagePreviewDialogProps {
  open: boolean;
  url?: string;
  alt?: string;
  onClose: () => void;
}

/**
 * ImagePreviewDialog renders an in-page full-size image lightbox using Radix Dialog.
 * It reuses the thumbnail URL, allowing the browser to use the already loaded resource
 * without opening a new tab. Radix provides focus locking, ESC, and backdrop dismissal.
 */
export function ImagePreviewDialog({
  open,
  url,
  alt,
  onClose,
}: ImagePreviewDialogProps) {
  if (!url) return null;

  return (
    <Dialog.Root
      open={open}
      onOpenChange={(nextOpen) => {
        if (!nextOpen) onClose();
      }}
    >
      <Dialog.Portal>
        <Dialog.Overlay className="image-preview-overlay" />
        <Dialog.Content
          className="image-preview-content"
          aria-describedby={undefined}
        >
          <Dialog.Title className="sr-only">
            {alt ? `查看原图：${alt}` : "图片原图预览"}
          </Dialog.Title>
          <Dialog.Close asChild>
            <button
              className="image-preview-close"
              type="button"
              aria-label="关闭原图预览"
            >
              <X size={20} />
            </button>
          </Dialog.Close>
          <img
            src={url}
            alt={alt || "图片原图预览"}
            className="image-preview-image"
          />
        </Dialog.Content>
      </Dialog.Portal>
    </Dialog.Root>
  );
}
