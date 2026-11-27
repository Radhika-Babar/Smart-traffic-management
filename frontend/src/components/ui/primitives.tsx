/** shadcn/ui components on Radix primitives, styled with this app's tokens. */
import * as DialogPrimitive from "@radix-ui/react-dialog";
import * as SelectPrimitive from "@radix-ui/react-select";
import * as SliderPrimitive from "@radix-ui/react-slider";
import { Slot } from "@radix-ui/react-slot";
import * as SwitchPrimitive from "@radix-ui/react-switch";
import * as TabsPrimitive from "@radix-ui/react-tabs";
import * as TooltipPrimitive from "@radix-ui/react-tooltip";
import { cva, type VariantProps } from "class-variance-authority";
import { Check, ChevronDown, Info, X } from "lucide-react";
import * as React from "react";

import { cn } from "@/utils/format";

const buttonStyles = cva(
  "inline-flex h-8 shrink-0 items-center justify-center gap-1.5 rounded-md px-3 text-[13px] font-medium whitespace-nowrap transition-colors active:translate-y-px disabled:opacity-50 disabled:pointer-events-none [&_svg]:size-3.5",
  {
    variants: {
      variant: {
        primary: "bg-accent text-white hover:bg-[#5a96f8]",
        outline: "border border-line-strong text-ink hover:bg-card-hover",
        ghost: "text-ink-2 hover:bg-card-hover hover:text-ink",
        danger: "border border-bad/60 text-[#fca5a5] hover:bg-bad/15",
      },
      size: { default: "", sm: "h-7 px-2 text-xs", icon: "w-8 px-0" },
    },
    defaultVariants: { variant: "outline", size: "default" },
  },
);

export interface ButtonProps
  extends React.ButtonHTMLAttributes<HTMLButtonElement>,
    VariantProps<typeof buttonStyles> {
  asChild?: boolean;
}

export const Button = React.forwardRef<HTMLButtonElement, ButtonProps>(
  ({ className, variant, size, asChild, ...props }, ref) => {
    const Component = asChild ? Slot : "button";
    return (
      <Component
        ref={ref}
        type={asChild ? undefined : "button"}
        className={cn(buttonStyles({ variant, size }), className)}
        {...props}
      />
    );
  },
);
Button.displayName = "Button";

export function Input({ className, ...props }: React.InputHTMLAttributes<HTMLInputElement>) {
  return (
    <input
      className={cn(
        "tnum h-8 w-full rounded-md border border-line-strong bg-raised px-2.5 text-[13px] text-ink placeholder:text-ink-3 disabled:opacity-50",
        className,
      )}
      {...props}
    />
  );
}

export interface Option {
  value: string;
  label: string;
}

export function Select({
  value,
  onChange,
  options,
  placeholder,
  label,
  className,
  disabled,
}: {
  value: string;
  onChange: (value: string) => void;
  options: Option[];
  placeholder?: string;
  label: string;
  className?: string;
  disabled?: boolean;
}) {
  return (
    <SelectPrimitive.Root value={value} onValueChange={onChange} disabled={disabled}>
      <SelectPrimitive.Trigger
        aria-label={label}
        className={cn(
          "flex h-8 min-w-0 items-center justify-between gap-2 rounded-md border border-line-strong bg-raised px-2.5 text-[13px] text-ink disabled:opacity-50",
          className,
        )}
      >
        <span className="truncate">
          <SelectPrimitive.Value placeholder={placeholder} />
        </span>
        <ChevronDown className="size-3.5 shrink-0 text-ink-3" />
      </SelectPrimitive.Trigger>
      <SelectPrimitive.Portal>
        <SelectPrimitive.Content
          position="popper"
          sideOffset={4}
          className="z-50 max-h-72 min-w-[var(--radix-select-trigger-width)] overflow-hidden rounded-md border border-line-strong bg-card shadow-xl"
        >
          <SelectPrimitive.Viewport className="p-1">
            {options.map((option) => (
              <SelectPrimitive.Item
                key={option.value}
                value={option.value}
                className="relative flex h-8 cursor-pointer items-center rounded-sm pr-7 pl-2 text-[13px] text-ink-2 outline-none data-[highlighted]:bg-card-hover data-[highlighted]:text-ink"
              >
                <SelectPrimitive.ItemText>{option.label}</SelectPrimitive.ItemText>
                <SelectPrimitive.ItemIndicator className="absolute right-2">
                  <Check className="size-3.5" />
                </SelectPrimitive.ItemIndicator>
              </SelectPrimitive.Item>
            ))}
          </SelectPrimitive.Viewport>
        </SelectPrimitive.Content>
      </SelectPrimitive.Portal>
    </SelectPrimitive.Root>
  );
}

export function Switch({
  checked,
  onChange,
  label,
  disabled,
}: {
  checked: boolean;
  onChange: (checked: boolean) => void;
  label: string;
  disabled?: boolean;
}) {
  return (
    <SwitchPrimitive.Root
      checked={checked}
      onCheckedChange={onChange}
      aria-label={label}
      disabled={disabled}
      className="relative h-5 w-9 shrink-0 rounded-full bg-line-strong transition-colors data-[state=checked]:bg-accent disabled:opacity-50"
    >
      <SwitchPrimitive.Thumb className="block size-4 translate-x-0.5 rounded-full bg-white transition-transform data-[state=checked]:translate-x-[18px]" />
    </SwitchPrimitive.Root>
  );
}

export function Slider({
  value,
  onChange,
  onCommit,
  min,
  max,
  step,
  label,
}: {
  value: number;
  onChange?: (value: number) => void;
  onCommit?: (value: number) => void;
  min: number;
  max: number;
  step: number;
  label: string;
}) {
  return (
    <SliderPrimitive.Root
      className="relative flex h-5 w-full touch-none items-center select-none"
      value={[value]}
      min={min}
      max={max}
      step={step}
      onValueChange={(values) => onChange?.(values[0])}
      onValueCommit={(values) => onCommit?.(values[0])}
    >
      <SliderPrimitive.Track className="relative h-1 grow rounded-full bg-line-strong">
        <SliderPrimitive.Range className="absolute h-full rounded-full bg-accent" />
      </SliderPrimitive.Track>
      <SliderPrimitive.Thumb
        aria-label={label}
        className="block size-4 rounded-full border-2 border-accent bg-ground"
      />
    </SliderPrimitive.Root>
  );
}

export const Tabs = TabsPrimitive.Root;
export const TabPanel = TabsPrimitive.Content;

export function TabList({ tabs }: { tabs: Option[] }) {
  return (
    <TabsPrimitive.List className="flex gap-3.5 overflow-x-auto overflow-y-hidden border-b border-line [scrollbar-width:none]">
      {tabs.map((tab) => (
        <TabsPrimitive.Trigger
          key={tab.value}
          value={tab.value}
          className="border-b-2 border-transparent px-0.5 pb-2 text-[13px] font-medium whitespace-nowrap text-ink-3 hover:text-ink data-[state=active]:border-accent data-[state=active]:text-ink"
        >
          {tab.label}
        </TabsPrimitive.Trigger>
      ))}
    </TabsPrimitive.List>
  );
}

export function Hint({ text, children }: { text: string; children?: React.ReactNode }) {
  return (
    <TooltipPrimitive.Root delayDuration={150}>
      <TooltipPrimitive.Trigger asChild>
        {children ? (
          <span tabIndex={0}>{children}</span>
        ) : (
          <button type="button" aria-label={text} className="inline-flex text-ink-3 hover:text-ink">
            <Info className="size-3" />
          </button>
        )}
      </TooltipPrimitive.Trigger>
      <TooltipPrimitive.Portal>
        <TooltipPrimitive.Content
          sideOffset={5}
          className="z-50 max-w-64 rounded-md border border-line-strong bg-raised px-2.5 py-1.5 text-xs leading-snug text-ink-2 shadow-xl"
        >
          {text}
        </TooltipPrimitive.Content>
      </TooltipPrimitive.Portal>
    </TooltipPrimitive.Root>
  );
}

export const HintProvider = TooltipPrimitive.Provider;

export function Dialog({
  open,
  onClose,
  title,
  children,
}: {
  open: boolean;
  onClose: () => void;
  title: string;
  children: React.ReactNode;
}) {
  return (
    <DialogPrimitive.Root open={open} onOpenChange={(next) => !next && onClose()}>
      <DialogPrimitive.Portal>
        <DialogPrimitive.Overlay className="fixed inset-0 z-40 bg-black/60" />
        <DialogPrimitive.Content
          aria-describedby={undefined}
          className="fixed top-1/2 left-1/2 z-50 max-h-[85vh] w-[calc(100%-32px)] max-w-lg -translate-x-1/2 -translate-y-1/2 overflow-y-auto rounded-lg border border-line-strong bg-card p-5 shadow-2xl"
        >
          <div className="mb-4 flex items-center justify-between">
            <DialogPrimitive.Title className="text-base font-semibold">{title}</DialogPrimitive.Title>
            <DialogPrimitive.Close aria-label="Close" className="text-ink-3 hover:text-ink">
              <X className="size-4" />
            </DialogPrimitive.Close>
          </div>
          {children}
        </DialogPrimitive.Content>
      </DialogPrimitive.Portal>
    </DialogPrimitive.Root>
  );
}
