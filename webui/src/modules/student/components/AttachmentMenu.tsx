import * as DropdownMenu from "@radix-ui/react-dropdown-menu";
import { FileUp, FolderOpen, Plus } from "lucide-react";

export function AttachmentMenu({ disabled, onUpload, onChooseFile }: {
  disabled: boolean;
  onUpload: () => void;
  onChooseFile: () => void;
}) {
  return <DropdownMenu.Root>
    <DropdownMenu.Trigger className="attachment-button" disabled={disabled} aria-label="上传附件" title="添加附件">
      <Plus size={18} />
    </DropdownMenu.Trigger>
    <DropdownMenu.Portal>
      <DropdownMenu.Content className="attachment-menu" align="start" side="top" sideOffset={8} collisionPadding={12}>
        <DropdownMenu.Item className="attachment-menu-item" onSelect={onUpload}><FileUp size={17} />上传文件</DropdownMenu.Item>
        <DropdownMenu.Item className="attachment-menu-item" onSelect={onChooseFile}><FolderOpen size={17} />从文件空间选择</DropdownMenu.Item>
      </DropdownMenu.Content>
    </DropdownMenu.Portal>
  </DropdownMenu.Root>;
}
