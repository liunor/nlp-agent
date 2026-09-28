import * as Dialog from "@radix-ui/react-dialog";
import { Info, X } from "lucide-react";

const NOVA_QQ_GROUP_NUMBER = "1080497980";

export function JoinNovaDialog({ open, onClose }: { open: boolean; onClose: () => void }) {
  return (
    <Dialog.Root open={open} onOpenChange={(nextOpen) => { if (!nextOpen) onClose(); }}>
      <Dialog.Portal>
        <Dialog.Overlay className="dialog-backdrop category-dialog-backdrop" />
        <Dialog.Content className="category-dialog join-nova-dialog" aria-describedby="join-nova-description">
          <header>
            <span className="category-dialog-icon" aria-hidden="true"><Info size={20} /></span>
            <div>
              <Dialog.Title asChild><h2>加入 Nova</h2></Dialog.Title>
              <Dialog.Description id="join-nova-description">
                加入 QQ 群，与我们一起参与 Nova 的开发与建设。
              </Dialog.Description>
            </div>
            <button className="category-dialog-close" type="button" aria-label="关闭加入 Nova" onClick={onClose}><X size={17} /></button>
          </header>

          <div className="join-nova-dialog-body">
            <section className="join-nova-qq" aria-labelledby="join-nova-qq-title">
              <h3 id="join-nova-qq-title">QQ 群</h3>
              <p>在 QQ 中搜索以下群号申请加入。</p>
              <div className="join-nova-qq-number"><span>群号</span><strong>{NOVA_QQ_GROUP_NUMBER}</strong></div>
            </section>
          </div>
        </Dialog.Content>
      </Dialog.Portal>
    </Dialog.Root>
  );
}
