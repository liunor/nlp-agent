import * as DropdownMenu from "@radix-ui/react-dropdown-menu";
import { Check, ChevronDown } from "lucide-react";

export function ClassroomScopeSelect({ value, options, onChange, error }: {
  value: string;
  options: Array<{ value: string; label: string }>;
  onChange: (value: string) => void;
  error?: string;
}) {
  const label = options.find((option) => option.value === value)?.label ?? "选择班级";
  return <DropdownMenu.Root>
    <DropdownMenu.Trigger className="teacher-classroom-trigger" aria-label="统计班级" title={error || label}>
      <span>{label}</span><ChevronDown size={15} aria-hidden="true" />
    </DropdownMenu.Trigger>
    <DropdownMenu.Portal>
      <DropdownMenu.Content className="teacher-classroom-menu" align="end" sideOffset={5} collisionPadding={12}>
        <DropdownMenu.RadioGroup value={value} onValueChange={onChange}>
          {options.map((option) => <DropdownMenu.RadioItem className="teacher-classroom-option" value={option.value} key={option.value}>
            <span>{option.label}</span><DropdownMenu.ItemIndicator><Check size={14} /></DropdownMenu.ItemIndicator>
          </DropdownMenu.RadioItem>)}
        </DropdownMenu.RadioGroup>
      </DropdownMenu.Content>
    </DropdownMenu.Portal>
  </DropdownMenu.Root>;
}
